"""PFactory workspace management.

Snapshot copies of AIFactory specs live under
``~/.pfactory/workspaces/{project_id}/specs/{spec_id}/context/``.

See ``snapshotter`` for the read-only snapshot routine that lets the
PFactory pipeline operate on a frozen copy of an AIFactory spec
without ever mutating the upstream source.
"""

from .snapshotter import (
    SnapshotError,
    SnapshotResult,
    snapshot_aifactory_spec,
)

__all__ = [
    "SnapshotError",
    "SnapshotResult",
    "snapshot_aifactory_spec",
]
