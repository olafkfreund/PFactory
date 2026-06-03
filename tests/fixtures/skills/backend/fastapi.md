# fastapi

> Source: [tiangolo/fastapi](https://github.com/tiangolo/fastapi) | Stars: 75k | Installs: 8,500 | Weekly: 8,200 | First seen: Jan 5, 2024

---

# FastAPI - Modern Python Web Framework

FastAPI is a modern, fast (high-performance) web framework for building APIs with Python 3.9+ based on standard Python type hints. Production-tested patterns with Pydantic v2, SQLAlchemy 2.0 async, and JWT authentication.

## When to Activate

Use when building REST APIs or backend services with Python:

- Building REST APIs with automatic OpenAPI documentation
- Creating async microservices
- Implementing JWT authentication and authorization
- Integrating with databases via SQLAlchemy async
- Building ML model serving endpoints

## Quick Start

### Project Setup with uv

```bash
# Create project
uv init my-api
cd my-api

# Add dependencies
uv add fastapi[standard] sqlalchemy[asyncio] aiosqlite python-jose[cryptography] passlib[bcrypt]

# Run development server
uv run fastapi dev src/main.py
```

### Minimal Working Example

```python
# src/main.py
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="My API", version="1.0.0")

class Item(BaseModel):
    name: str
    price: float
    description: str | None = None

@app.get("/")
async def root():
    return {"message": "Hello World"}

@app.post("/items", response_model=Item, status_code=201)
async def create_item(item: Item):
    return item
```

## Core Patterns

### Dependency Injection

```python
from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

@app.get("/users/{user_id}")
async def get_user(user_id: int, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
```

### Request Validation with Pydantic v2

```python
from pydantic import BaseModel, EmailStr, field_validator
from datetime import datetime

class UserCreate(BaseModel):
    email: EmailStr
    username: str
    password: str
    created_at: datetime = datetime.now()

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters")
        return v.lower()
```

### JWT Authentication

```python
from fastapi import Security
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")
SECRET_KEY = "your-secret-key"
ALGORITHM = "HS256"

async def get_current_user(token: str = Security(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return await get_user_by_id(int(user_id))
    except JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
```

## Error Handling

```python
from fastapi import Request
from fastapi.responses import JSONResponse

@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=422,
        content={"detail": str(exc)},
    )
```

## Async SQLAlchemy Integration

```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

engine = create_async_engine("sqlite+aiosqlite:///./app.db", echo=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(unique=True, index=True)
    username: Mapped[str] = mapped_column(unique=True)
```

## Best Practices

- Always use async/await for I/O operations
- Use Pydantic models for request/response validation
- Implement proper error handling with HTTPException
- Use dependency injection for shared resources (DB, auth)
- Add rate limiting for public endpoints
- Use background tasks for long-running operations
- Document all endpoints with docstrings (auto-generates OpenAPI docs)
- Version your API with path prefixes (/api/v1/)
