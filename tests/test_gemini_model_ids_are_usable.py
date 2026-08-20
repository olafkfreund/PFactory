"""Guard: Gemini 3.x catalog ids must carry an effort suffix.

The Antigravity CLI rejects a bare family name outright:

    $ agy --model gemini-3.7-flash -p "hi"
    Error: invalid model selection (--model "gemini-3.7-flash" --effort ""):
    --model gemini-3.7-flash requires --effort (available: low, medium, high)

    $ agy --model gemini-3.7-flash-medium -p "hi"
    ok

The factory passes only ``--model`` and never ``--effort``
(``antigravity_agentic._build_command``), so a bare id is unusable here. It was
shipped that way once -- extracted from strings in the CLI binary, which
contains both forms -- and a catalog entry that cannot run is worse than a
missing one: the model is selectable, the run fails, and the failure looks like
the model rather than the id.

Scoped to the 3.6/3.7 families ON PURPOSE. Those exist ONLY in the real
Antigravity CLI, so any catalog entry for them must be in the form that CLI
accepts. The older bare ids (gemini-3.5-flash, gemini-3.1-flash-lite,
gemini-2.5-*) are served by @google/gemini-cli -- which the image still aliases
as `antigravity` -- and work there; agy rejects them, but nothing routes to agy
yet. Widening this guard to those would fail entries that are correct today.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EFFORTS = ("-low", "-medium", "-high")

CATALOGS = (
    REPO / "apps/frontend-web/src/shared/constants/models.ts",
    REPO / "apps/web-server/server/services/insights_providers/antigravity_provider.py",
    REPO / "apps/web-server/server/services/insights_providers/gemini_provider.py",
)

# Only the families the real Antigravity CLI introduced (3.6 / 3.7).
ID_RE = re.compile(r"""["'](gemini-3\.[67]-(?:flash|pro)[a-z0-9.-]*)["']""")


def _catalog_ids() -> dict[Path, set[str]]:
    found: dict[Path, set[str]] = {}
    for p in CATALOGS:
        if p.exists():
            found[p] = set(ID_RE.findall(p.read_text(encoding="utf-8")))
    return found


def test_at_least_one_catalog_was_scanned() -> None:
    """A guard that matched nothing proves nothing."""
    scanned = _catalog_ids()
    assert scanned, f"no catalog files found under {REPO}"
    assert any(ids for ids in scanned.values()), (
        "no gemini-3.6/3.7 ids matched in any catalog -- the regex or the file "
        "layout changed, so this guard is measuring nothing"
    )


def test_every_gemini_36_37_id_carries_an_effort_suffix() -> None:
    bad: list[str] = []
    for path, ids in _catalog_ids().items():
        for model_id in sorted(ids):
            # "-preview" ids are a separate, still-valid naming from gemini-cli
            if model_id.endswith("-preview") or "-preview-" in model_id:
                continue
            if not model_id.endswith(EFFORTS):
                bad.append(f"{path.relative_to(REPO)}: {model_id}")
    assert not bad, (
        "gemini-3.6/3.7 ids must end in -low/-medium/-high; the Antigravity CLI "
        "rejects a bare family name because the factory passes no --effort:\n  "
        + "\n  ".join(bad)
    )
