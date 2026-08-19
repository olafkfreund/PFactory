"""The workspace lock files must not be world-readable (CodeQL py/overly-permissive-file).

`MergeLock` and `SpecNumberLock` both create their lock file with `os.open(...,
O_CREAT | O_EXCL, mode)`. The mode used to be 0o644, which publishes the holder's
PID to every other local user sharing the workspace root. These assert the mode
is owner-only, so a future edit that widens it again fails here rather than in a
scan weeks later.
"""

import stat

import pytest

from core.workspace.models import MergeLock, SpecNumberLock


@pytest.mark.parametrize(
    ("make_lock", "lock_name"),
    [
        (lambda d: MergeLock(d, "spec-1"), "merge-spec-1.lock"),
        (SpecNumberLock, "spec-numbering.lock"),
    ],
    ids=["merge", "spec-number"],
)
def test_lock_file_is_owner_only(tmp_path, make_lock, lock_name):
    lock = make_lock(tmp_path)
    with lock:
        lock_file = tmp_path / ".pfactory" / ".locks" / lock_name
        assert lock_file.exists(), f"{lock_name} was not created"
        mode = stat.S_IMODE(lock_file.stat().st_mode)
        assert mode == 0o600, f"{lock_name} mode is {mode:#o}, expected 0o600"
