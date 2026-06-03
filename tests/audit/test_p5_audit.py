"""P5 — Audit hardening acceptance tests.

Seven tests map to the five acceptance bullets in Epic #26 issue #32:

  1. test_hash_chain_links_rows                — AC: chain on write
  2. test_tampered_row_breaks_chain             — AC: tampered detected
  3. test_export_roundtrip_json                 — AC: JSON export round-trip
  4. test_export_csv                            — AC: CSV export
  5. test_external_verify_script_round_trip     — AC: external verify
  6. test_erasure_deletes_pii_but_chain_still_verifies  — AC: GDPR
  7. test_retention_deletes_expired             — AC: retention job
"""

from __future__ import annotations

import pytest


def _write_three_events(SessionLocal):
    """Helper: write 3 audit events via log_audit_event; return their ids."""
    import asyncio

    from server.services.audit_service import log_audit_event

    ids: list[str] = []
    async def _go():
        async with SessionLocal() as session:
            for i in range(3):
                await log_audit_event(
                    db=session,
                    action=f"test.action.{i}",
                    resource_type="test",
                    resource_id=f"r{i}",
                    user_id=None,
                    org_id=None,
                    details={"i": i},
                )
            await session.commit()
            # Fetch the inserted rows ordered.
            from server.database.models import AuditLog
            from sqlalchemy import select
            result = await session.execute(
                select(AuditLog).order_by(AuditLog.created_at.asc())
            )
            for row in result.scalars():
                ids.append(row.id)
    asyncio.new_event_loop().run_until_complete(_go())
    return ids


@pytest.mark.audit
def test_hash_chain_links_rows(fresh_db) -> None:
    """First row's prev_hash = GENESIS; each subsequent row's prev_hash =
    compute_hash(previous row). The full chain verifies via verify_chain."""
    import asyncio

    from server.database.models import AuditLog
    from server.services.audit_chain import (
        GENESIS,
        compute_hash,
        row_as_mapping,
        verify_chain,
    )
    from sqlalchemy import select

    engine, SessionLocal = fresh_db
    _write_three_events(SessionLocal)

    async def _fetch():
        async with SessionLocal() as s:
            result = await s.execute(
                select(AuditLog).order_by(AuditLog.created_at.asc())
            )
            return [row_as_mapping(r) for r in result.scalars()]
    rows = asyncio.new_event_loop().run_until_complete(_fetch())

    # First row's prev_hash is genesis.
    assert rows[0]["prev_hash"] == GENESIS, (
        f"first row's prev_hash must be GENESIS; got {rows[0]['prev_hash']!r}"
    )
    # Each subsequent row's prev_hash chains to the previous row.
    for i in range(1, len(rows)):
        expected = compute_hash(rows[i - 1]["prev_hash"], rows[i - 1])
        assert rows[i]["prev_hash"] == expected, (
            f"row {i} prev_hash mismatch: stored={rows[i]['prev_hash']!r} "
            f"expected={expected!r}"
        )

    # End-to-end verification.
    ok, bad_idx, reason = verify_chain(rows)
    assert ok, f"chain verification failed at row {bad_idx}: {reason}"


@pytest.mark.audit
def test_tampered_row_breaks_chain(fresh_db) -> None:
    """Mutating any row's protected content (action, details_json, etc.)
    makes verify_chain return False at the row AFTER the mutation."""
    import asyncio

    from server.database.models import AuditLog
    from server.services.audit_chain import row_as_mapping, verify_chain
    from sqlalchemy import select

    engine, SessionLocal = fresh_db
    _write_three_events(SessionLocal)

    async def _fetch_and_tamper():
        async with SessionLocal() as s:
            result = await s.execute(
                select(AuditLog).order_by(AuditLog.created_at.asc())
            )
            audit_rows = list(result.scalars())
            # Tamper the middle row's action.
            audit_rows[1].action = "tampered.action"
            await s.commit()
            # Re-fetch.
            result2 = await s.execute(
                select(AuditLog).order_by(AuditLog.created_at.asc())
            )
            return [row_as_mapping(r) for r in result2.scalars()]

    rows = asyncio.new_event_loop().run_until_complete(_fetch_and_tamper())

    ok, bad_idx, reason = verify_chain(rows)
    assert not ok, "tampered row should fail verification"
    # The chain breaks at row 2 — the row AFTER the tampered row,
    # because row 2's stored prev_hash was computed against the
    # untampered content of row 1.
    assert bad_idx == 2, (
        f"expected mismatch at row 2 (after tampered row 1); got {bad_idx} — {reason}"
    )


@pytest.mark.audit
def test_export_roundtrip_json(fresh_db) -> None:
    """JSON (NDJSON) export contains every row + prev_hash; verifier round-trips."""
    import asyncio
    import json

    from server.services.audit_chain import verify_chain
    from server.services.audit_export import stream_json

    engine, SessionLocal = fresh_db
    _write_three_events(SessionLocal)

    async def _collect():
        async with SessionLocal() as s:
            chunks = []
            async for chunk in stream_json(s):
                chunks.append(chunk)
            return b"".join(chunks)
    payload = asyncio.new_event_loop().run_until_complete(_collect())

    # Parse NDJSON.
    lines = [line for line in payload.decode("utf-8").splitlines() if line.strip()]
    rows = [json.loads(line) for line in lines]
    assert len(rows) == 3, f"expected 3 rows, got {len(rows)}"
    for r in rows:
        assert "prev_hash" in r, "exported row missing prev_hash"
        assert "id" in r and "action" in r and "created_at" in r

    # Re-run the chain verifier on the exported (deserialized) rows.
    ok, bad_idx, reason = verify_chain(rows)
    assert ok, f"exported chain failed verification at {bad_idx}: {reason}"


@pytest.mark.audit
def test_export_csv(fresh_db) -> None:
    """CSV export contains the right columns including prev_hash."""
    import asyncio
    import csv
    import io

    from server.services.audit_export import CSV_COLUMNS, stream_csv

    engine, SessionLocal = fresh_db
    _write_three_events(SessionLocal)

    async def _collect():
        async with SessionLocal() as s:
            chunks = []
            async for chunk in stream_csv(s):
                chunks.append(chunk)
            return b"".join(chunks)
    payload = asyncio.new_event_loop().run_until_complete(_collect())

    reader = csv.reader(io.StringIO(payload.decode("utf-8")))
    header = next(reader)
    assert header == CSV_COLUMNS, (
        f"CSV header mismatch:\nexpected {CSV_COLUMNS}\ngot {header}"
    )
    body = list(reader)
    assert len(body) == 3, f"expected 3 data rows, got {len(body)}"
    # Every row should have a prev_hash filled.
    prev_hash_idx = CSV_COLUMNS.index("prev_hash")
    for row in body:
        assert row[prev_hash_idx], "prev_hash empty in CSV row"


@pytest.mark.audit
def test_external_verify_script_round_trip(fresh_db, tmp_path) -> None:
    """`python -m server.audit verify-chain <exported.ndjson>` exits 0 on
    a valid export and non-zero on a tampered one."""
    import asyncio
    import subprocess
    import sys

    from server.services.audit_export import stream_json

    from tests.audit.conftest import WEB_SERVER_ROOT

    engine, SessionLocal = fresh_db
    _write_three_events(SessionLocal)

    # Write the export to disk.
    out = tmp_path / "audit.ndjson"
    async def _dump():
        async with SessionLocal() as s:
            with open(out, "wb") as f:
                async for chunk in stream_json(s):
                    f.write(chunk)
    asyncio.new_event_loop().run_until_complete(_dump())

    # Verify (should pass).
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(WEB_SERVER_ROOT)}
    result = subprocess.run(
        [sys.executable, "-m", "server.audit", "verify-chain", str(out)],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert result.returncode == 0, (
        f"verify exited {result.returncode}; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "OK" in result.stdout or "ok" in result.stdout.lower()

    # Tamper a row and verify again — should now fail.
    lines = out.read_text().splitlines()
    import json as _json
    row = _json.loads(lines[1])
    row["action"] = "tampered.from.disk"
    lines[1] = _json.dumps(row)
    out.write_text("\n".join(lines) + "\n")

    result = subprocess.run(
        [sys.executable, "-m", "server.audit", "verify-chain", str(out)],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert result.returncode != 0, "tampered export should fail verification"


@pytest.mark.audit
def test_erasure_deletes_pii_but_chain_still_verifies(fresh_db) -> None:
    """After GDPR erasure:
      - users.email / users.name / users.avatar_url are NULL.
      - users.gdpr_erased_at is set.
      - audit_logs rows for the user have user_id = sha256(original)[:36].
      - audit_logs details_json has no plaintext PII.
      - The full audit chain still verifies via verify_chain.
    """
    import asyncio
    import uuid

    from server.database.models import AuditLog, User
    from server.services.audit_chain import row_as_mapping, verify_chain
    from server.services.audit_service import log_audit_event
    from server.services.gdpr import _hash_user_id, erase_user
    from sqlalchemy import select

    engine, SessionLocal = fresh_db

    async def _setup_and_erase():
        async with SessionLocal() as s:
            # Create the user we'll erase + a second user (control).
            erasee = User(
                id=str(uuid.uuid4()),
                email="erasee@example.com",
                name="To Be Erased",
                password_hash="x",
                role="member",
                is_active=True,
            )
            other = User(
                id=str(uuid.uuid4()),
                email="other@example.com",
                name="Other",
                password_hash="x",
                role="member",
                is_active=True,
            )
            s.add_all([erasee, other])
            await s.commit()

            # 5 audit events: 3 from erasee, 2 from other (interleaved).
            for actor in [erasee, other, erasee, other, erasee]:
                await log_audit_event(
                    db=s,
                    user_id=actor.id,
                    action="test.event",
                    resource_type="test",
                    details={"actor_email": actor.email, "note": "before erasure"},
                )
            await s.commit()

            # Erase.
            await erase_user(s, erasee.id)

            # Re-fetch state for assertions.
            fresh = await s.execute(select(User).where(User.id == erasee.id))
            fresh_user = fresh.scalar_one()
            audit_result = await s.execute(
                select(AuditLog).order_by(AuditLog.created_at.asc())
            )
            audit_rows = list(audit_result.scalars())
            return fresh_user, audit_rows, erasee.id

    fresh_user, audit_rows, original_uid = (
        asyncio.new_event_loop().run_until_complete(_setup_and_erase())
    )

    # 1. PII on user row is NULL.
    assert fresh_user.email is None, "email must be NULL after erasure"
    assert fresh_user.name is None, "name must be NULL after erasure"
    assert fresh_user.avatar_url is None
    assert fresh_user.gdpr_erased_at is not None, "gdpr_erased_at must be set"

    # 2. audit_logs user_id for erasee rows is the SHA-256 hash.
    hashed = _hash_user_id(original_uid)
    erasee_audit = [r for r in audit_rows if r.user_id == hashed]
    assert len(erasee_audit) == 3, (
        f"expected 3 audit rows for erasee with hashed user_id; got {len(erasee_audit)}"
    )
    # Make sure no row still references the original UUID.
    assert not any(r.user_id == original_uid for r in audit_rows), (
        "no audit row may retain the original user_id"
    )

    # 3. details_json has been redacted — no plaintext email.
    for row in erasee_audit:
        assert "erasee@example.com" not in (row.details_json or ""), (
            f"plaintext email leaked into details_json: {row.details_json}"
        )

    # 4. The chain still verifies end-to-end.
    rows_for_verify = [row_as_mapping(r) for r in audit_rows]
    ok, bad_idx, reason = verify_chain(rows_for_verify)
    assert ok, (
        f"post-erasure chain failed verification at row {bad_idx}: {reason}"
    )


@pytest.mark.audit
def test_retention_deletes_expired(fresh_db) -> None:
    """Rows past retention_until are deleted by run_retention; in-window
    rows are preserved. Returns a summary {deleted, remaining}."""
    import asyncio
    from datetime import datetime, timedelta

    from server.database.models import AuditLog
    from server.jobs.audit_retention import run_retention
    from server.services.audit_service import log_audit_event
    from sqlalchemy import select

    engine, SessionLocal = fresh_db

    async def _setup_and_prune():
        async with SessionLocal() as s:
            # Write 4 audit events — log_audit_event sets retention_until
            # to now + 395 days. We then manually backdate 2 of them to
            # the past so the retention job deletes them.
            for i in range(4):
                await log_audit_event(
                    db=s, action=f"test.{i}", resource_type="test",
                    user_id=None, org_id=None,
                )
            await s.commit()

            # Backdate the first 2 rows.
            result = await s.execute(
                select(AuditLog).order_by(AuditLog.created_at.asc()).limit(2)
            )
            for row in result.scalars():
                row.retention_until = datetime.utcnow() - timedelta(days=1)
            await s.commit()

            summary = await run_retention(s)

            # Count remaining.
            count_result = await s.execute(select(AuditLog))
            remaining_rows = list(count_result.scalars())
            return summary, remaining_rows

    summary, remaining = asyncio.new_event_loop().run_until_complete(
        _setup_and_prune()
    )

    assert summary["deleted"] == 2, (
        f"expected 2 expired rows deleted; got {summary['deleted']}"
    )
    assert summary["remaining"] == 2
    assert len(remaining) == 2, (
        f"expected 2 surviving rows; got {len(remaining)}"
    )
