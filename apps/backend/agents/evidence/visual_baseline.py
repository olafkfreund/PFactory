"""Visual-regression baseline store (#109).

The Evidence subsystem captures screenshots; visual regression adds the missing
half — a *baseline* a generated Playwright ``toHaveScreenshot`` assertion can be
compared against, versioned **per target** so a UI regression actually fails a
test instead of needing a human to eyeball the media.

Pixel comparison itself happens in the Playwright runner (``toHaveScreenshot``
diffs in-process); this module owns the *storage* side the portal Evidence tab
drives: where a target's baselines live, whether one exists for a snapshot, and
the accept/update flow that promotes a freshly-captured screenshot to the new
baseline.

Layout (parallel to ``findings/evidence/``)::

    <spec_dir>/findings/visual_baselines/<target>/<snapshot>.png

Pure-Python + filesystem only — no image library, no network.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "BaselineEntry",
    "accept_baseline",
    "baseline_dir",
    "baseline_path",
    "baseline_status",
    "has_baseline",
    "list_baselines",
]

_BASELINES_SUBDIR = "visual_baselines"


class VisualBaselineError(ValueError):
    """Raised for an unsafe target or snapshot name."""


def _safe_component(value: str, *, kind: str) -> str:
    """Return ``value`` if it is a safe single path component, else raise.

    Guards against path traversal (``..``) and separators so a malicious or
    malformed target/snapshot name can't escape the baselines directory.
    """
    if not value or not value.strip():
        raise VisualBaselineError(f"{kind} must not be empty")
    if value != value.strip():
        raise VisualBaselineError(
            f"{kind} {value!r} must not have surrounding whitespace"
        )
    if "/" in value or "\\" in value or value in {".", ".."} or "\x00" in value:
        raise VisualBaselineError(f"{kind} {value!r} is not a safe path component")
    return value


@dataclass(frozen=True)
class BaselineEntry:
    """One stored baseline image."""

    snapshot: str  # filename, e.g. "homepage.png"
    path: Path
    size_bytes: int


# ── locations ────────────────────────────────────────────────────────────────


def baseline_dir(spec_dir: Path, target: str) -> Path:
    """Directory holding ``target``'s baselines (not created)."""
    return (
        Path(spec_dir)
        / "findings"
        / _BASELINES_SUBDIR
        / _safe_component(target, kind="target")
    )


def baseline_path(spec_dir: Path, target: str, snapshot: str) -> Path:
    """Absolute path to one snapshot's baseline image (not created)."""
    return baseline_dir(spec_dir, target) / _safe_component(snapshot, kind="snapshot")


def has_baseline(spec_dir: Path, target: str, snapshot: str) -> bool:
    """True if a baseline image exists for ``snapshot`` under ``target``."""
    return baseline_path(spec_dir, target, snapshot).is_file()


def list_baselines(spec_dir: Path, target: str) -> list[BaselineEntry]:
    """All baselines stored for ``target``, sorted by snapshot name.

    Returns an empty list if the target has no baselines yet.
    """
    d = baseline_dir(spec_dir, target)
    if not d.is_dir():
        return []
    return [
        BaselineEntry(snapshot=p.name, path=p, size_bytes=p.stat().st_size)
        for p in sorted(d.iterdir())
        if p.is_file()
    ]


# ── accept / update ──────────────────────────────────────────────────────────


def accept_baseline(
    spec_dir: Path,
    target: str,
    snapshot: str,
    source: Path | bytes,
) -> Path:
    """Promote a captured screenshot to ``target``'s baseline for ``snapshot``.

    This is the accept/update-baseline flow: overwrite (or create) the stored
    baseline with ``source`` — either an existing image ``Path`` (copied) or raw
    ``bytes`` (written). Parent directories are created as needed.

    Returns:
        The baseline path now holding the image.

    Raises:
        VisualBaselineError: for an unsafe target/snapshot name.
        FileNotFoundError: if ``source`` is a path that does not exist.
    """
    dest = baseline_path(spec_dir, target, snapshot)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(source, bytes):
        dest.write_bytes(source)
    else:
        src = Path(source)
        if not src.is_file():
            raise FileNotFoundError(f"baseline source image not found: {src}")
        shutil.copyfile(src, dest)
    return dest


# ── status ───────────────────────────────────────────────────────────────────


def baseline_status(
    spec_dir: Path, target: str, captured_snapshots: list[str]
) -> dict[str, str]:
    """Classify each captured snapshot against the stored baselines.

    Returns a ``{snapshot: status}`` map where status is:
      * ``"tracked"`` — a baseline already exists (the runner diffed against it)
      * ``"new"``     — captured but no baseline yet (needs an accept to track it)
    """
    return {
        snap: ("tracked" if has_baseline(spec_dir, target, snap) else "new")
        for snap in captured_snapshots
    }
