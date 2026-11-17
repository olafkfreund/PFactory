"""Cross-process lock for a memory directory (RFC-0021 follow-up, #3796).

One ``flock`` on a sidecar file guards every read-modify-write of
``confirmed.json``, ``scratchpad.json`` and the gotchas files. The wait is
bounded; on timeout :class:`LockTimeoutError` (an ``OSError``) is raised and the
caller does not write, which is how memory fails: a lost write is acceptable,
a corrupt or stalled one is not.
"""

import contextlib
import threading
import time
from collections.abc import Iterator
from pathlib import Path

try:
    import fcntl
except ImportError:  # not a supported pod platform; no cross-process lock there
    fcntl = None  # type: ignore[assignment]

LOCK_NAME = ".memory.lock"
WAIT = 2.0  # seconds
_held = threading.local()


class LockTimeoutError(OSError):
    """The lock stayed busy for the whole wait."""


@contextlib.contextmanager
def locked(memory_dir: Path, wait: float | None = None) -> Iterator[None]:
    # Re-entrant per thread: a second descriptor on the same file would block
    # against our own first one (promote -> confirm).
    held = _held.__dict__.setdefault("dirs", set())
    key = str(memory_dir)
    if fcntl is None or key in held:
        yield
        return
    memory_dir.mkdir(parents=True, exist_ok=True)
    with (memory_dir / LOCK_NAME).open("a") as handle:
        deadline = time.monotonic() + (WAIT if wait is None else wait)
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockTimeoutError(f"memory lock busy: {memory_dir}") from None
                time.sleep(0.02)
        held.add(key)
        try:
            yield
        finally:
            held.discard(key)  # closing the handle releases the flock
