"""In-memory stand-in for ``PlanSessionStore``, to the #758 contract.

Shared by the service-level lease and stale-write tests so neither needs
Postgres. Behaves like the real store: ``upsert`` is a compare-and-set on
``expected_version`` (0 means insert-new), and the emit lease is free, held or
released by owner. Failures are injected by setting the ``*_error`` attributes.
"""

from __future__ import annotations


class FakeSessionStore:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, int]] = {}
        self.lease: dict[str, str] = {}
        self.lease_calls: list[tuple[str, str, int]] = []
        self.released: list[tuple[str, str]] = []
        self.acquire_error: Exception | None = None
        self.release_error: Exception | None = None
        self.upsert_error: Exception | None = None
        self._seq = 0

    def get(self, session_id: str) -> tuple[str, int] | None:
        return self.rows.get(session_id)

    def list_payloads(self, *, tenant_id: str | None = None) -> list[str]:
        return [payload for payload, _ in self.rows.values()]

    def upsert(
        self,
        session_id: str,
        *,
        payload: str,
        seq: int,
        tenant_id: str | None,
        expected_version: int,
    ) -> int | None:
        if self.upsert_error is not None:
            raise self.upsert_error
        current = self.rows.get(session_id)
        if expected_version == 0:
            if current is not None:
                return None
        elif current is None or current[1] != expected_version:
            return None
        version = expected_version + 1
        self.rows[session_id] = (payload, version)
        return version

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def session_ids(self) -> set[str]:
        return set(self.rows)

    def acquire_emit_lease(self, session_id: str, owner: str, ttl_seconds: int) -> bool:
        self.lease_calls.append((session_id, owner, ttl_seconds))
        if self.acquire_error is not None:
            raise self.acquire_error
        if session_id in self.lease:
            return False
        self.lease[session_id] = owner
        return True

    def release_emit_lease(self, session_id: str, owner: str) -> None:
        self.released.append((session_id, owner))
        if self.release_error is not None:
            raise self.release_error
        if self.lease.get(session_id) == owner:
            del self.lease[session_id]

    def is_ready(self) -> bool:
        return True

    def close(self) -> None:
        pass
