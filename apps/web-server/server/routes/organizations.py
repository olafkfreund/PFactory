"""
Organization CRUD and member management routes.

Provides:
- POST   /api/orgs                          - Create organization
- GET    /api/orgs                          - List user's organizations
- GET    /api/orgs/{org_id}                 - Organization details
- PUT    /api/orgs/{org_id}                 - Update organization
- DELETE /api/orgs/{org_id}                 - Delete organization (owner only)
- POST   /api/orgs/{org_id}/members/invite  - Invite member by email
- GET    /api/orgs/{org_id}/members         - List members
- PUT    /api/orgs/{org_id}/members/{user_id} - Change member role
- DELETE /api/orgs/{org_id}/members/{user_id} - Remove member
"""

import logging
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Organization, OrgMember, User
from ..database.engine import get_db
from .auth_routes import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orgs", tags=["Organizations"])

# ---------------------------------------------------------------------------
# Role hierarchy
# ---------------------------------------------------------------------------

ROLE_LEVELS: dict[str, int] = {
    "viewer": 0,
    "member": 1,
    "admin": 2,
    "owner": 3,
}

VALID_ROLES = set(ROLE_LEVELS.keys())


def _role_level(role: str) -> int:
    """Return the numeric level for a role string, defaulting to -1."""
    return ROLE_LEVELS.get(role, -1)


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Convert a string to a URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    text = text.strip("-")
    return text


async def _ensure_unique_slug(
    db: AsyncSession, slug: str, exclude_org_id: str | None = None
) -> str:
    """Return a unique slug, appending a numeric suffix if necessary.

    If ``exclude_org_id`` is provided, the slug belonging to that org is
    excluded from the uniqueness check (used during updates).
    """
    candidate = slug
    counter = 1

    while True:
        query = select(Organization).where(Organization.slug == candidate)
        if exclude_org_id is not None:
            query = query.where(Organization.id != exclude_org_id)

        result = await db.execute(query)
        if result.scalar_one_or_none() is None:
            return candidate

        candidate = f"{slug}-{counter}"
        counter += 1

        # Safety valve to prevent infinite loops
        if counter > 100:
            import uuid as _uuid

            return f"{slug}-{_uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class CreateOrgRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="URL-friendly slug. Auto-generated from name if omitted.",
    )


class UpdateOrgRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=255)
    plan: str | None = Field(default=None, max_length=50)
    settings_json: str | None = None


class InviteMemberRequest(BaseModel):
    email: EmailStr
    role: str = Field(default="member", description="Role: viewer, member, admin")


class UpdateMemberRoleRequest(BaseModel):
    role: str = Field(..., description="New role: viewer, member, admin, owner")


class OrgResponse(BaseModel):
    id: str
    name: str
    slug: str
    owner_id: str
    plan: str
    created_at: datetime
    member_count: int
    user_role: str

    class Config:
        from_attributes = True


class OrgMemberResponse(BaseModel):
    id: str
    user_id: str
    email: str
    name: str
    avatar_url: str | None
    role: str
    joined_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Dependency: require_org_role
# ---------------------------------------------------------------------------


class OrgRoleChecker:
    """Callable dependency that verifies the current user has at least the
    specified role within the organization identified by the ``org_id``
    path parameter.

    Usage::

        @router.get("/api/orgs/{org_id}/something")
        async def endpoint(
            membership: OrgMember = Depends(require_org_role("member")),
        ):
            ...
    """

    def __init__(self, minimum_role: str) -> None:
        if minimum_role not in VALID_ROLES:
            raise ValueError(
                f"Invalid minimum_role {minimum_role!r}. "
                f"Must be one of {VALID_ROLES}"
            )
        self.minimum_role = minimum_role
        self.minimum_level = _role_level(minimum_role)

    async def __call__(
        self,
        org_id: str,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> OrgMember:
        # Verify the organization exists
        result = await db.execute(
            select(Organization).where(Organization.id == org_id)
        )
        org = result.scalar_one_or_none()
        if org is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organization not found",
            )

        # Verify user membership
        result = await db.execute(
            select(OrgMember).where(
                OrgMember.org_id == org_id,
                OrgMember.user_id == current_user.id,
            )
        )
        membership = result.scalar_one_or_none()
        if membership is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a member of this organization",
            )

        # Verify role level
        if _role_level(membership.role) < self.minimum_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This action requires at least '{self.minimum_role}' role. "
                    f"Your role is '{membership.role}'."
                ),
            )

        return membership


def require_org_role(minimum_role: str) -> OrgRoleChecker:
    """Factory that returns a FastAPI dependency for org-level role checking."""
    return OrgRoleChecker(minimum_role)


# ---------------------------------------------------------------------------
# Organization CRUD routes
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=OrgResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new organization",
)
async def create_organization(
    body: CreateOrgRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new organization and add the current user as owner."""

    # Generate or validate slug
    raw_slug = _slugify(body.slug) if body.slug else _slugify(body.name)
    if not raw_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to generate a valid slug from the provided name.",
        )

    slug = await _ensure_unique_slug(db, raw_slug)

    # Create the organization
    org = Organization(
        name=body.name,
        slug=slug,
        owner_id=current_user.id,
        plan="free",
    )
    db.add(org)
    await db.flush()

    # Add the creator as owner member
    membership = OrgMember(
        org_id=org.id,
        user_id=current_user.id,
        role="owner",
    )
    db.add(membership)
    await db.commit()
    await db.refresh(org)

    logger.info(
        f"Organization created: {org.name} (slug={org.slug}, "
        f"id={org.id}) by user {current_user.id}"
    )

    return OrgResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        owner_id=org.owner_id,
        plan=org.plan,
        created_at=org.created_at,
        member_count=1,
        user_role="owner",
    )


@router.get(
    "",
    response_model=list[OrgResponse],
    summary="List organizations the current user belongs to",
)
async def list_organizations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all organizations where the current user is a member.

    Each entry includes the member count and the user's role in that org.
    """

    # Subquery: member count per org
    member_count_sq = (
        select(
            OrgMember.org_id,
            func.count(OrgMember.id).label("member_count"),
        )
        .group_by(OrgMember.org_id)
        .subquery()
    )

    # Main query: join org with user's membership and the member count
    query = (
        select(
            Organization,
            OrgMember.role.label("user_role"),
            func.coalesce(member_count_sq.c.member_count, 0).label("member_count"),
        )
        .join(OrgMember, OrgMember.org_id == Organization.id)
        .outerjoin(member_count_sq, member_count_sq.c.org_id == Organization.id)
        .where(OrgMember.user_id == current_user.id)
        .order_by(Organization.name)
    )

    result = await db.execute(query)
    rows = result.all()

    return [
        OrgResponse(
            id=org.id,
            name=org.name,
            slug=org.slug,
            owner_id=org.owner_id,
            plan=org.plan,
            created_at=org.created_at,
            member_count=member_count,
            user_role=user_role,
        )
        for org, user_role, member_count in rows
    ]


@router.get(
    "/{org_id}",
    response_model=OrgResponse,
    summary="Get organization details",
)
async def get_organization(
    org_id: str,
    membership: OrgMember = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Return details for a single organization. Requires membership."""

    # Fetch the org
    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Count members
    count_result = await db.execute(
        select(func.count(OrgMember.id)).where(OrgMember.org_id == org_id)
    )
    member_count = count_result.scalar() or 0

    return OrgResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        owner_id=org.owner_id,
        plan=org.plan,
        created_at=org.created_at,
        member_count=member_count,
        user_role=membership.role,
    )


@router.put(
    "/{org_id}",
    response_model=OrgResponse,
    summary="Update organization",
)
async def update_organization(
    org_id: str,
    body: UpdateOrgRequest,
    membership: OrgMember = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Update organization fields. Requires admin or owner role."""

    # Fetch the org
    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Apply updates
    if body.name is not None:
        org.name = body.name

    if body.slug is not None:
        new_slug = _slugify(body.slug)
        if not new_slug:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid slug value.",
            )
        # Check uniqueness (excluding current org)
        slug_check = await db.execute(
            select(Organization).where(
                Organization.slug == new_slug,
                Organization.id != org_id,
            )
        )
        if slug_check.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"The slug '{new_slug}' is already in use.",
            )
        org.slug = new_slug

    if body.plan is not None:
        org.plan = body.plan

    if body.settings_json is not None:
        org.settings_json = body.settings_json

    await db.commit()
    await db.refresh(org)

    # Count members for response
    count_result = await db.execute(
        select(func.count(OrgMember.id)).where(OrgMember.org_id == org_id)
    )
    member_count = count_result.scalar() or 0

    logger.info(f"Organization updated: {org.slug} (id={org.id})")

    return OrgResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        owner_id=org.owner_id,
        plan=org.plan,
        created_at=org.created_at,
        member_count=member_count,
        user_role=membership.role,
    )


@router.delete(
    "/{org_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete organization (owner only)",
)
async def delete_organization(
    org_id: str,
    membership: OrgMember = Depends(require_org_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    """Delete an organization and all its members. Requires owner role.

    This cascade-deletes OrgMember rows but does NOT delete the User
    accounts that were members.
    """

    # Fetch the org
    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Delete all memberships first
    await db.execute(
        delete(OrgMember).where(OrgMember.org_id == org_id)
    )

    # Delete the organization
    await db.delete(org)
    await db.commit()

    logger.info(f"Organization deleted: {org.slug} (id={org.id})")

    return None


# ---------------------------------------------------------------------------
# Member management routes
# ---------------------------------------------------------------------------


@router.post(
    "/{org_id}/members/invite",
    response_model=OrgMemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invite a user to the organization by email",
)
async def invite_member(
    org_id: str,
    body: InviteMemberRequest,
    membership: OrgMember = Depends(require_org_role("admin")),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Invite a user to join the organization by their email address.

    Requires admin or owner role. The invited user must already have an
    account. The ``role`` field cannot be set to ``owner`` -- ownership
    transfer must be done through the role change endpoint.
    """

    # Validate the requested role
    if body.role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role '{body.role}'. Must be one of: {', '.join(sorted(VALID_ROLES))}",
        )

    if body.role == "owner":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot invite a user directly as owner. Use the role change endpoint instead.",
        )

    # Inviters can only assign roles at or below their own level
    if _role_level(body.role) > _role_level(membership.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot assign a role higher than your own.",
        )

    # Look up the user by email
    result = await db.execute(
        select(User).where(User.email == body.email)
    )
    target_user = result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No user found with email '{body.email}'.",
        )

    # Check if user is already a member
    result = await db.execute(
        select(OrgMember).where(
            OrgMember.org_id == org_id,
            OrgMember.user_id == target_user.id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This user is already a member of the organization.",
        )

    # Create the membership
    new_member = OrgMember(
        org_id=org_id,
        user_id=target_user.id,
        role=body.role,
        invited_by=current_user.id,
    )
    db.add(new_member)
    await db.commit()
    await db.refresh(new_member)

    logger.info(
        f"User {target_user.email} invited to org {org_id} "
        f"with role '{body.role}' by {current_user.id}"
    )

    return OrgMemberResponse(
        id=new_member.id,
        user_id=target_user.id,
        email=target_user.email,
        name=target_user.name,
        avatar_url=target_user.avatar_url,
        role=new_member.role,
        joined_at=new_member.joined_at,
    )


@router.get(
    "/{org_id}/members",
    response_model=list[OrgMemberResponse],
    summary="List organization members",
)
async def list_members(
    org_id: str,
    membership: OrgMember = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Return all members of the organization with user details.

    Requires at least viewer-level membership.
    """

    result = await db.execute(
        select(OrgMember, User)
        .join(User, User.id == OrgMember.user_id)
        .where(OrgMember.org_id == org_id)
        .order_by(OrgMember.joined_at)
    )
    rows = result.all()

    return [
        OrgMemberResponse(
            id=member.id,
            user_id=user.id,
            email=user.email,
            name=user.name,
            avatar_url=user.avatar_url,
            role=member.role,
            joined_at=member.joined_at,
        )
        for member, user in rows
    ]


@router.put(
    "/{org_id}/members/{user_id}",
    response_model=OrgMemberResponse,
    summary="Change a member's role",
)
async def update_member_role(
    org_id: str,
    user_id: str,
    body: UpdateMemberRoleRequest,
    membership: OrgMember = Depends(require_org_role("owner")),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change the role of an organization member. Requires owner role.

    Constraints:
    - An owner cannot change their own role (must transfer ownership first).
    - If assigning the ``owner`` role to another user, ownership is
      transferred: the current owner is demoted to ``admin``.
    - There must always be at least one owner in the organization.
    """

    # Validate role
    if body.role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role '{body.role}'. Must be one of: {', '.join(sorted(VALID_ROLES))}",
        )

    # Cannot change own role
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot change your own role. Transfer ownership to another member first.",
        )

    # Find the target membership
    result = await db.execute(
        select(OrgMember).where(
            OrgMember.org_id == org_id,
            OrgMember.user_id == user_id,
        )
    )
    target_membership = result.scalar_one_or_none()
    if target_membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this organization.",
        )

    old_role = target_membership.role

    # If demoting an owner, ensure at least one owner remains
    if old_role == "owner" and body.role != "owner":
        owner_count_result = await db.execute(
            select(func.count(OrgMember.id)).where(
                OrgMember.org_id == org_id,
                OrgMember.role == "owner",
            )
        )
        owner_count = owner_count_result.scalar() or 0
        if owner_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot demote the last owner. Transfer ownership to another member first.",
            )

    # If promoting to owner, demote the current owner to admin
    if body.role == "owner":
        membership.role = "admin"

        # Also update the org's owner_id
        org_result = await db.execute(
            select(Organization).where(Organization.id == org_id)
        )
        org = org_result.scalar_one_or_none()
        if org is not None:
            org.owner_id = user_id

    target_membership.role = body.role
    await db.commit()
    await db.refresh(target_membership)

    # Load the user info for the response
    user_result = await db.execute(
        select(User).where(User.id == user_id)
    )
    target_user = user_result.scalar_one_or_none()

    logger.info(
        f"Member {user_id} role changed from '{old_role}' to '{body.role}' "
        f"in org {org_id} by {current_user.id}"
    )

    return OrgMemberResponse(
        id=target_membership.id,
        user_id=target_membership.user_id,
        email=target_user.email if target_user else "",
        name=target_user.name if target_user else "",
        avatar_url=target_user.avatar_url if target_user else None,
        role=target_membership.role,
        joined_at=target_membership.joined_at,
    )


@router.delete(
    "/{org_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member from the organization",
)
async def remove_member(
    org_id: str,
    user_id: str,
    membership: OrgMember = Depends(require_org_role("viewer")),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a member from the organization.

    Rules:
    - Owners and admins can remove any non-owner member.
    - Any member can remove themselves (self-leave), except owners.
    - Owners cannot remove themselves -- they must transfer ownership first.
    - Viewers and members can only remove themselves.
    """

    is_self_remove = user_id == current_user.id

    # Owner cannot self-remove
    if is_self_remove and membership.role == "owner":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Owners cannot remove themselves. Transfer ownership first.",
        )

    # Non-admin/owner users can only remove themselves
    if not is_self_remove and _role_level(membership.role) < _role_level("admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to remove other members.",
        )

    # Find the target membership
    result = await db.execute(
        select(OrgMember).where(
            OrgMember.org_id == org_id,
            OrgMember.user_id == user_id,
        )
    )
    target_membership = result.scalar_one_or_none()
    if target_membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this organization.",
        )

    # Admins cannot remove owners
    if target_membership.role == "owner" and membership.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an owner can remove another owner.",
        )

    # Prevent removing the last owner (even by another owner)
    if target_membership.role == "owner":
        owner_count_result = await db.execute(
            select(func.count(OrgMember.id)).where(
                OrgMember.org_id == org_id,
                OrgMember.role == "owner",
            )
        )
        owner_count = owner_count_result.scalar() or 0
        if owner_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot remove the last owner. Transfer ownership first.",
            )

    await db.delete(target_membership)
    await db.commit()

    action = "left" if is_self_remove else "removed from"
    logger.info(
        f"User {user_id} {action} org {org_id} by {current_user.id}"
    )

    return None
