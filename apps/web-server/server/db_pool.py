"""Issue #961: cap database pools within the 19-connection per-pod budget."""

import os


def pool_kwargs(url: str, prefix: str, size: int, overflow: int) -> dict[str, int]:
    """SQLAlchemy pool kwargs for one engine; ``{}`` for SQLite (keeps its own pool)."""
    if url.startswith("sqlite"):
        return {}
    return {
        "pool_size": int(os.environ.get(f"{prefix}_POOL_SIZE", str(size))),
        "max_overflow": int(os.environ.get(f"{prefix}_MAX_OVERFLOW", str(overflow))),
        "pool_timeout": int(os.environ.get("DB_POOL_TIMEOUT", "10")),
    }
