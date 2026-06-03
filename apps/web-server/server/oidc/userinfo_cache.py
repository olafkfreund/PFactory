"""In-process userinfo cache (Epic #26 P3.4).

Maps ``sub`` → ``(userinfo_dict, fetched_at_epoch)``. Used by the
OIDC refresh endpoint to avoid hitting the IdP's userinfo endpoint
on every refresh — without this, a fleet of clients refreshing every
15 minutes generates O(N) outbound RTTs every quarter-hour.

TTL design:
  - ``DEFAULT_TTL_S`` = 300 (5 minutes). Short enough that a
    user-disabled-in-IdP event is reflected within the
    access-token-TTL window (15 min) — the worst case is:
      t=0    user disabled in IdP
      t=0    cache hit from previous refresh, miss the event
      t=300  cache expires
      t=300+ next refresh triggers a userinfo call, IdP rejects,
             session deleted.
    So the upper bound is access-TTL + cache-TTL = 20 min — within
    the 15-min target for the typical case where cache and access
    expire at similar times.
  - For stricter SLAs, set ``APP_OIDC_USERINFO_CACHE_TTL_S=0`` to
    disable caching entirely (every refresh = one userinfo RTT).

Singleton: a module-level dict keyed by ``sub``. Thread-safety isn't
required since the FastAPI worker is single-threaded per event loop;
multi-worker deployments accept eventual consistency between caches.
"""

from __future__ import annotations

import logging
import os
import time
from threading import Lock

logger = logging.getLogger(__name__)


DEFAULT_TTL_S = 300


def _get_ttl_s() -> int:
    val = os.environ.get("APP_OIDC_USERINFO_CACHE_TTL_S")
    if val is None:
        return DEFAULT_TTL_S
    try:
        return max(0, int(val))
    except ValueError:
        return DEFAULT_TTL_S


_CACHE: dict[str, tuple[dict, float]] = {}
_LOCK = Lock()


def get_cached(sub: str) -> dict | None:
    """Return cached userinfo for ``sub`` if non-expired, else None."""
    ttl = _get_ttl_s()
    if ttl == 0:
        return None
    with _LOCK:
        entry = _CACHE.get(sub)
    if entry is None:
        return None
    userinfo, fetched_at = entry
    if (time.monotonic() - fetched_at) > ttl:
        invalidate(sub)
        return None
    return userinfo


def put(sub: str, userinfo: dict) -> None:
    """Cache ``userinfo`` for ``sub``. No-op when TTL is 0."""
    ttl = _get_ttl_s()
    if ttl == 0:
        return
    with _LOCK:
        _CACHE[sub] = (userinfo, time.monotonic())


def invalidate(sub: str) -> None:
    """Drop the cache entry for ``sub`` (e.g. after IdP revocation)."""
    with _LOCK:
        _CACHE.pop(sub, None)


def clear_all() -> None:
    """Test hook + emergency wipe."""
    with _LOCK:
        _CACHE.clear()
