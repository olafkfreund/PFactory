"""Per-organization data-key cache + lookup.

DataKeyManager owns the in-process LRU cache of unwrapped data keys
keyed by ``org_id``. On every call to ``get_or_create_data_key(org_id)``
it returns the unwrapped 32-byte key for that org, creating a fresh
``kms_data_keys`` row on first call.

Cache invalidation: every entry tracks the ``rotated_at`` timestamp it
was minted under. On each cache hit older than ``POLL_INTERVAL_S``, we
re-query the row's current ``rotated_at`` — if it's changed, we evict
and re-unwrap. This lets a root-key rotation (P2.5) invalidate caches
across all processes by bumping ``rotated_at``.

Sync-only: this class is invoked from SQLAlchemy TypeDecorator code
paths which are themselves synchronous. We use the underlying sync
engine (``engine.sync_engine``) so the LRU lookup doesn't need to be
async-context-aware.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .kms import Backend

# KmsDataKey imported lazily inside get_or_create_data_key() to avoid a
# circular import: models.py imports EncryptedString from this package
# (for column types in P2.3 onwards), and KmsDataKey itself lives in
# models.py. Lazy import breaks the cycle.


@dataclass
class _CacheEntry:
    """One slot in the LRU cache."""

    data_key: bytes
    rotated_at: datetime
    cached_at: float  # time.monotonic() snapshot — drives the poll-interval check


class DataKeyManager:
    """Per-org data-key cache + lazy creation.

    Public method:
        get_or_create_data_key(org_id) -> bytes (32-byte AES-256 key)

    Internally:
        - On cache hit within POLL_INTERVAL_S: return cached key immediately
        - On cache hit past POLL_INTERVAL_S: re-poll rotated_at; if changed,
          re-unwrap; if unchanged, refresh cached_at
        - On cache miss: query the row; if missing, generate + insert; in
          either case, unwrap and cache

    Thread-safe: a per-org lock prevents the dogpile pattern where N
    concurrent callers create N rows for the same org.
    """

    KEY_BYTES = 32
    DEFAULT_POLL_INTERVAL_S = 60.0

    def __init__(
        self,
        sync_engine: Engine,
        backend: Backend,
        kms_key_id: str = "fernet:default",
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        self._engine = sync_engine
        self._backend = backend
        self._kms_key_id = kms_key_id
        self._poll_interval_s = poll_interval_s
        self._cache: dict[str, _CacheEntry] = {}
        # One lock per org_id to prevent race-creation; protected by _meta_lock.
        self._meta_lock = threading.Lock()
        self._per_org_locks: dict[str, threading.Lock] = {}

    def _lock_for(self, org_id: str) -> threading.Lock:
        with self._meta_lock:
            lock = self._per_org_locks.get(org_id)
            if lock is None:
                lock = threading.Lock()
                self._per_org_locks[org_id] = lock
            return lock

    def get_or_create_data_key(self, org_id: str) -> bytes:
        """Return the unwrapped 32-byte data key for ``org_id``."""
        # Lazy import to break the circular dep with database.models.
        from ..database.models import KmsDataKey

        now = time.monotonic()
        entry = self._cache.get(org_id)
        if entry is not None and (now - entry.cached_at) < self._poll_interval_s:
            return entry.data_key

        # Either cache miss or poll interval elapsed. Take the per-org lock so
        # only one thread does the DB work for this org at a time.
        with self._lock_for(org_id):
            # Re-check under lock — another thread may have refreshed already.
            entry = self._cache.get(org_id)
            now = time.monotonic()
            if entry is not None and (now - entry.cached_at) < self._poll_interval_s:
                return entry.data_key

            with Session(self._engine) as session:
                row = session.execute(
                    select(KmsDataKey).where(KmsDataKey.org_id == org_id)
                ).scalar_one_or_none()

                if row is None:
                    # First use for this org — mint a fresh data key.
                    new_key = secrets.token_bytes(self.KEY_BYTES)
                    wrapped = self._backend.encrypt(new_key)
                    row = KmsDataKey(
                        id=str(uuid.uuid4()),
                        org_id=org_id,
                        wrapped_key=wrapped,
                        kms_key_id=self._kms_key_id,
                    )
                    session.add(row)
                    session.commit()
                    session.refresh(row)
                    self._cache[org_id] = _CacheEntry(new_key, row.rotated_at, now)
                    return new_key

                # Row exists. If the cached rotated_at matches the DB's, the
                # data key bytes are unchanged — just refresh cached_at.
                if entry is not None and entry.rotated_at == row.rotated_at:
                    entry.cached_at = now
                    return entry.data_key

                # Either no cache or rotated_at moved — unwrap and cache.
                data_key = self._backend.decrypt(bytes(row.wrapped_key))
                self._cache[org_id] = _CacheEntry(data_key, row.rotated_at, now)
                return data_key

    def invalidate(self, org_id: str | None = None) -> None:
        """Drop cache entries — for tests + the rotation runbook."""
        with self._meta_lock:
            if org_id is None:
                self._cache.clear()
            else:
                self._cache.pop(org_id, None)
