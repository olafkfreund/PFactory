"""Evidence retention enforcer — Task 16 / #32 sub-task 16.5.

Prunes evidence files from the ``findings/evidence/`` directory tree
under a PFactory spec workspace according to the configured retention
policy.

Supported policy keys (mirroring ``.pfactory.yml`` ``evidence_policy.retention``):

    failures  — ``"forever"`` | ``"<N>_days"``   (default: ``"forever"``)
    flagged   — ``"<N>_days"``                    (default: ``"90_days"``)
    passing   — ``"<N>_days"``                    (default: ``"7_days"``)
    size_cap_per_task — ``"<N>MB"`` | ``"<N>GB"`` (default: ``"500MB"``)

Usage::

    from agents.evidence.retention import enforce_retention
    from pathlib import Path
    from datetime import datetime, timezone

    spec_dir = Path("~/.pfactory/workspaces/p1/specs/s1").expanduser()
    policy = {
        "failures": "forever",
        "flagged": "30_days",
        "passing": "7_days",
        "size_cap_per_task": "500MB",
    }
    stats = enforce_retention(spec_dir, policy)
    print(stats.pruned_by_age_count, stats.bytes_freed)

Evidence directory layout (from ``layout.py``)::

    <spec_dir>/findings/evidence/
        <test_id>/
            screenshots/
            video.webm
            trace.zip
            network.har

Verdicts live in ``<spec_dir>/findings/verdicts.json``.  The enforcer
reads the verdict for each ``<test_id>`` to determine which retention
bucket applies:

* ``accept``  → **passing** bucket
* ``reject``  → **failures** bucket  (rejected = failed)
* ``flag``    → **flagged** bucket
* (unknown)   → **failures** bucket  (conservative default)

The ``<test_id>`` directory's **mtime** is used as the age reference
for per-age pruning.  The ``now`` parameter (defaults to UTC now) is
injected so tests can freeze time without requiring ``freezegun``.

The size-cap sweep runs AFTER the age sweep.  It sorts surviving test
directories by mtime ascending (oldest first) and removes whole
``<test_id>`` directories until the total size is under the cap.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# ─── RetentionStats ──────────────────────────────────────────────────────────


@dataclasses.dataclass
class RetentionStats:
    """Summary of what the retention enforcer pruned.

    Attributes:
        pruned_by_age_count: Number of test_id directories removed because
            their verdict bucket's retention window had expired.
        pruned_by_size_count: Number of test_id directories removed because
            the spec exceeded the ``size_cap_per_task`` limit.
        bytes_freed: Total bytes reclaimed (both sweeps combined).
        retained_count: Number of test_id directories that survived.
        errors: List of ``(test_id, str)`` tuples for non-fatal errors
            (e.g. permission denied on a file).
    """

    pruned_by_age_count: int = 0
    pruned_by_size_count: int = 0
    bytes_freed: int = 0
    retained_count: int = 0
    errors: list[tuple[str, str]] = dataclasses.field(default_factory=list)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _parse_days(window: str) -> int | None:
    """Return number of days from ``"<N>_days"``; ``None`` for ``"forever"``."""
    if window.lower() == "forever":
        return None
    if window.endswith("_days"):
        try:
            return int(window[: -len("_days")])
        except ValueError:
            pass
    raise ValueError(f"invalid retention window: {window!r}")


def _parse_size_bytes(cap: str | None) -> int | None:
    """Return the cap in bytes, or ``None`` if *cap* is ``None``."""
    if cap is None:
        return None
    cap = cap.strip()
    for suffix, multiplier in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if cap.upper().endswith(suffix):
            try:
                n = float(cap[: -len(suffix)])
                return int(n * multiplier)
            except ValueError:
                pass
    raise ValueError(f"invalid size cap: {cap!r}")


def _dir_size_bytes(path: Path) -> int:
    """Return total byte size of all files under *path* (recursive)."""
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def _load_verdicts(spec_dir: Path) -> dict[str, str]:
    """Load ``findings/verdicts.json`` and return ``{test_id: verdict}``."""
    vpath = spec_dir / "findings" / "verdicts.json"
    if not vpath.exists():
        return {}
    try:
        data: Any = json.loads(vpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    verdicts_list = data.get("verdicts", [])
    if not isinstance(verdicts_list, list):
        return {}
    result: dict[str, str] = {}
    for item in verdicts_list:
        if isinstance(item, dict) and "test_id" in item and "verdict" in item:
            result[str(item["test_id"])] = str(item["verdict"])
    return result


def _verdict_to_bucket(verdict: str) -> str:
    """Map an Evaluator verdict string to a retention policy bucket key."""
    if verdict == "accept":
        return "passing"
    if verdict == "flag":
        return "flagged"
    return "failures"  # reject + unknown → failures (conservative)


# ─── Public API ──────────────────────────────────────────────────────────────


def enforce_retention(
    spec_dir: Path,
    policy: dict[str, Any],
    *,
    now: datetime | None = None,
) -> RetentionStats:
    """Prune evidence files based on *policy*.

    Performs two sweeps in order:

    1. **Age sweep** — removes ``<test_id>`` directories whose retention
       window (keyed by verdict bucket) has expired relative to *now*.
    2. **Size-cap sweep** — removes the oldest surviving ``<test_id>``
       directories until the total evidence size is within
       ``size_cap_per_task``.

    Args:
        spec_dir: Path to the PFactory workspace spec directory.
        policy: Retention policy dict.  Expected keys:

            * ``"failures"`` — retention window for failed/rejected tests.
            * ``"flagged"``  — retention window for flagged tests.
            * ``"passing"``  — retention window for accepted/passing tests.
            * ``"size_cap_per_task"`` — total size cap (``"<N>MB"`` /
              ``"<N>GB"`` / ``None`` to disable).

        now: Reference timestamp for age calculations.  Defaults to
            ``datetime.now(timezone.utc)``.  Inject a fixed value in
            tests to avoid flakiness.

    Returns:
        :class:`RetentionStats` summarising what was pruned.
    """
    if now is None:
        now = datetime.now(UTC)

    stats = RetentionStats()
    evidence_root = spec_dir / "findings" / "evidence"

    if not evidence_root.exists() or not evidence_root.is_dir():
        return stats

    # ── Collect test_id directories ─────────────────────────────────────
    test_dirs: list[Path] = [p for p in sorted(evidence_root.iterdir()) if p.is_dir()]
    if not test_dirs:
        return stats

    # ── Load verdicts for verdict→bucket mapping ────────────────────────
    verdicts = _load_verdicts(spec_dir)

    # ── Parse policy windows ────────────────────────────────────────────
    bucket_windows: dict[str, int | None] = {}
    for bucket in ("failures", "flagged", "passing"):
        raw = policy.get(bucket, "forever" if bucket == "failures" else "7_days")
        bucket_windows[bucket] = _parse_days(str(raw))

    # ── Sweep 1: age-based pruning ──────────────────────────────────────
    surviving: list[Path] = []
    for test_dir in test_dirs:
        test_id = test_dir.name
        verdict = verdicts.get(test_id, "reject")  # default conservative
        bucket = _verdict_to_bucket(verdict)
        max_days = bucket_windows.get(bucket)  # None = forever

        if max_days is not None:
            try:
                mtime = test_dir.stat().st_mtime
            except OSError:
                surviving.append(test_dir)
                continue
            age_dt = datetime.fromtimestamp(mtime, tz=UTC)
            cutoff = now - timedelta(days=max_days)
            if age_dt < cutoff:
                # Directory is older than the allowed window → prune
                size = _dir_size_bytes(test_dir)
                try:
                    shutil.rmtree(test_dir)
                    stats.pruned_by_age_count += 1
                    stats.bytes_freed += size
                except OSError as exc:
                    stats.errors.append((test_id, str(exc)))
                    surviving.append(test_dir)  # failed to delete; keep
                continue

        surviving.append(test_dir)

    # ── Sweep 2: size-cap pruning ────────────────────────────────────────
    cap_bytes = _parse_size_bytes(policy.get("size_cap_per_task"))
    if cap_bytes is not None and surviving:
        total_bytes = sum(_dir_size_bytes(d) for d in surviving)

        if total_bytes > cap_bytes:
            # Sort oldest first (by mtime) to prune oldest evidence first
            surviving_with_mtime: list[tuple[float, Path]] = []
            for d in surviving:
                try:
                    mt = d.stat().st_mtime
                except OSError:
                    mt = 0.0
                surviving_with_mtime.append((mt, d))
            surviving_with_mtime.sort(key=lambda x: x[0])  # oldest first

            after_cap: list[Path] = []
            for mt, test_dir in surviving_with_mtime:
                if total_bytes <= cap_bytes:
                    after_cap.append(test_dir)
                    continue
                size = _dir_size_bytes(test_dir)
                test_id = test_dir.name
                try:
                    shutil.rmtree(test_dir)
                    stats.pruned_by_size_count += 1
                    stats.bytes_freed += size
                    total_bytes -= size
                except OSError as exc:
                    stats.errors.append((test_id, str(exc)))
                    after_cap.append(test_dir)
            surviving = after_cap

    stats.retained_count = len(surviving)
    return stats
