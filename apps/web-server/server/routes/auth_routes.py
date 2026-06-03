"""
Authentication routes for JWT-based user registration, login, and token management.

Provides:
- POST /api/auth/register  - Create a new account (+ default organization)
- POST /api/auth/login     - Authenticate and receive JWT tokens
- POST /api/auth/refresh   - Refresh an expired access token
- POST /api/auth/logout    - Logout (stateless no-op, returns success)
- GET  /api/auth/me        - Retrieve current user profile
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import Organization, OrgMember, User
from ..database.engine import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Auth"])

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    avatar_url: str | None
    role: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class AuthResponse(BaseModel):
    user: UserResponse
    access_token: str
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str


class MessageResponse(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def _create_access_token(user: User) -> str:
    """Create a short-lived access token containing user claims."""
    settings = get_settings()
    expires = datetime.now(timezone.utc) + timedelta(
        minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": user.id,
        "email": user.email,
        "role": user.role,
        "type": "access",
        "exp": expires,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _create_refresh_token(user: User) -> str:
    """Create a long-lived refresh token containing only the user id."""
    settings = get_settings()
    expires = datetime.now(timezone.utc) + timedelta(
        days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS
    )
    payload = {
        "sub": user.id,
        "type": "refresh",
        "exp": expires,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _slugify(text: str) -> str:
    """Convert a string to a URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text


# ---------------------------------------------------------------------------
# Dependency: get current user from JWT in request.state
# ---------------------------------------------------------------------------


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User:
    """Dependency that extracts the authenticated user from request.state.

    The ``TokenAuthMiddleware`` populates ``request.state.user`` with
    the JWT payload when a valid JWT is present.  This dependency loads
    the full ``User`` ORM object from the database.
    """
    user_data = getattr(request.state, "user", None)
    if user_data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = user_data.get("id") if isinstance(user_data, dict) else None
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user account",
)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Register a new user.

    Creates the user record, a default *Personal* organization, and adds
    the user as its owner.  Returns JWT access and refresh tokens.
    """
    # Check for existing user
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    # Create user
    user = User(
        email=body.email,
        name=body.name,
        password_hash=pwd_context.hash(body.password),
        role="user",
    )
    db.add(user)
    await db.flush()  # Populate user.id before creating org

    # Create default organization
    slug = _slugify(body.name) + "-personal"
    # Ensure slug uniqueness by appending a short suffix if needed
    existing_slug = await db.execute(
        select(Organization).where(Organization.slug == slug)
    )
    if existing_slug.scalar_one_or_none() is not None:
        slug = f"{slug}-{user.id[:8]}"

    org = Organization(
        name="Personal",
        slug=slug,
        owner_id=user.id,
        plan="free",
    )
    db.add(org)
    await db.flush()

    # Add user as owner member
    membership = OrgMember(
        org_id=org.id,
        user_id=user.id,
        role="owner",
    )
    db.add(membership)

    await db.commit()
    await db.refresh(user)

    logger.info(f"New user registered: {user.email} (id={user.id})")

    return AuthResponse(
        user=UserResponse.model_validate(user),
        access_token=_create_access_token(user),
        refresh_token=_create_refresh_token(user),
    )


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="Authenticate and receive JWT tokens",
)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Authenticate with email and password.

    Returns a short-lived access token (15 min) and a long-lived refresh
    token (7 days).
    """
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user is None or not pwd_context.verify(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    logger.info(f"User logged in: {user.email}")

    return AuthResponse(
        user=UserResponse.model_validate(user),
        access_token=_create_access_token(user),
        refresh_token=_create_refresh_token(user),
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh an expired access token",
)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Exchange a valid refresh token for a new access token.

    The refresh token itself is not rotated; it remains valid until its
    original expiry.
    """
    settings = get_settings()

    try:
        payload = jwt.decode(
            body.refresh_token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is not a refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return TokenResponse(access_token=_create_access_token(user))


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Logout (stateless)",
)
async def logout():
    """Logout endpoint.

    Since JWT tokens are stateless, this is a no-op on the server side.
    Clients should discard their stored tokens.
    """
    return MessageResponse(message="Successfully logged out")


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user profile",
)
async def me(current_user: User = Depends(get_current_user)):
    """Return the profile of the currently authenticated user."""
    return UserResponse.model_validate(current_user)
