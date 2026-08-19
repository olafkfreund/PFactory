"""audit_logs.resource_id must hold a composite task id, not just a UUID.

The column was String(36) -- exactly a UUID's length, copied from the `id`
columns beside it. It carries no foreign key and sits next to
`resource_type: String(255)`: it is a free-form pointer into whichever table
`resource_type` names, and those keys are not all UUIDs.

The task pipeline builds "{project_id}:pending-{hex8}". For a GitHub-backed
project that is 53 characters, so every audited task action raised
StringDataRightTruncationError -- and it raised it AFTER the route had already
returned {"success": true}, so the caller was told a task existed when no row
had been written. `audit_logs` was empty in production when this was found,
which is the evidence that no audited action had ever been recorded.

This asserts the declared width against a REAL id built the way the route
builds it, rather than against a hard-coded number, so it keeps meaning if the
id format changes.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from sqlalchemy import String  # noqa: E402

from server.database.models import AuditLog  # noqa: E402


def _declared_width(column: str) -> int:
    """The declared max length of an AuditLog column.

    Narrowed rather than ignored: ``TypeEngine`` has no ``length``, so the
    isinstance check is what makes this strict-clean AND what makes the test
    fail loudly if the column ever stops being a String.
    """
    col_type = AuditLog.__table__.columns[column].type
    assert isinstance(col_type, String), f"{column} is not a String column"
    width = col_type.length
    assert width is not None, f"{column} has no declared length"
    return width


def test_resource_id_fits_a_github_backed_composite_task_id() -> None:
    """The exact shape execution.py builds, for the longest real project id."""
    project_id = "olafkfreund-factory-ladder-py-simple"
    task_id = f"{project_id}:pending-{uuid.uuid4().hex[:8]}"

    assert len(task_id) > 36, (
        "if the id format shrank below 36 this test no longer covers the defect"
    )
    assert len(task_id) <= _declared_width("resource_id"), (
        f"resource_id is {_declared_width('resource_id')} chars but a "
        f"real task id is {len(task_id)}; the audit insert fails AFTER the route "
        "has already answered success"
    )


def test_resource_id_is_at_least_as_wide_as_resource_type() -> None:
    """It points into whatever resource_type names, so it cannot be narrower."""
    assert _declared_width("resource_id") >= _declared_width("resource_type")
