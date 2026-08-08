"""Skip test modules whose web-server deps live in a different venv.

WHY THIS EXISTS. The repo has a split-venv layout: the web-server's deps
(FastAPI, SQLAlchemy, OpenTelemetry, ...) live in ``apps/web-server``'s venv,
not in the backend test venv that `pytest tests/` and the pre-commit hook use.
A test module that imports one of them from the wrong venv raises
``ModuleNotFoundError`` at COLLECTION, and a collection error is not a skip:
pytest aborts the entire invocation. `pytest tests/ apps/web-server/tests/` —
the exact command ci.yml runs — then reports nothing at all rather than the
4500+ tests that would have run, and the developer sees zero results with no
indication that the cause is one uninstalled optional dependency in one module.

WHY IT IS A MODULE AND NOT JUST ``tests/conftest.py``. It used to be inline in
``tests/conftest.py``, whose hooks apply to ``tests/`` and below — and NOT to
``apps/web-server/tests/``, which is a sibling tree. So the one directory whose
tests the mechanism was written to protect was the one directory it could not
reach. Both trees now register the same hook against this module (#453).

WHY THE DEP LIST IS DERIVED. Hand-writing it meant hand-maintaining exactly
what the mechanism exists to avoid. It named ``fastapi`` and ``sqlalchemy``,
was extended once, and still did not know ``opentelemetry``. The names now come
from ``apps/web-server/requirements.txt``, so adding a dep there needs no edit
here. CI installs both requirement files, which is precisely why any gap in
this list stays invisible on CI and only bites locally.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WEB_SERVER_REQS = _REPO_ROOT / "apps" / "web-server" / "requirements.txt"

# Distributions whose import name is not derivable from their package name.
# A fact about Python packaging, not about this repo, and this is the whole set
# present in the requirements file. Without it the derivation below would LOOK
# complete while still missing `jose`, `dotenv`, `multipart` and `git` — the
# same failure mode, dressed up as a general solution.
DIST_TO_IMPORT = {
    "gitpython": "git",
    "python-dotenv": "dotenv",
    "python-jose": "jose",
    "python-multipart": "multipart",
}


def importable(name: str) -> bool:
    """True when *name* resolves in this venv. Never raises."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):  # pragma: no cover - defensive
        return False


def distributions() -> list[str]:
    """Distribution names declared in the web-server requirements file."""
    try:
        lines = _WEB_SERVER_REQS.read_text(encoding="utf-8").splitlines()
    except OSError:  # pragma: no cover - the file is in-tree
        lines = ["fastapi", "sqlalchemy"]  # the two the hand-written list had
    names = []
    for line in lines:
        # Strip the comment, then the version specifier / extras / env marker.
        dist = re.split(r"[<>=!~;\[\s#]", line.strip(), maxsplit=1)[0].strip().lower()
        if dist and not dist.startswith("-"):
            names.append(dist)
    return names


def import_candidates(dist: str) -> set[str]:
    """Import names *dist* might be imported under.

    The standard normalisation is lowercase with ``-`` -> ``_``. Namespace
    distributions (``opentelemetry-api``, ``azure-identity``,
    ``google-cloud-kms``) are imported under their FIRST segment, so both are
    considered.
    """
    return {DIST_TO_IMPORT.get(dist, dist.replace("-", "_")), dist.split("-", 1)[0]}


def absent_imports() -> list[str]:
    """Top-level import names of web-server deps this venv does not have.

    Every candidate is filtered through ``find_spec``, so a name reaches the
    list only when it is genuinely absent here. That filter is what makes the
    loose derivation safe in both directions: a misderived name
    (``python-jose`` -> ``python``) cannot skip a module that would otherwise
    run, because nothing imports it; and a correctly-derived name that IS
    installed is dropped rather than skipping a runnable module.
    """
    candidates: set[str] = set()
    for dist in distributions():
        candidates |= import_candidates(dist)
    absent = sorted(n for n in candidates if not importable(n))
    # `server` is the first-party web-server package, not a distribution, so it
    # is not derivable from the requirements file. It is unimportable exactly
    # when the running venv is not the web-server's.
    if "fastapi" in absent:
        absent.append("server")
    return absent


MISSING = absent_imports()
IMPORT_RE = (
    re.compile(r"^\s*(?:import|from)\s+(?:" + "|".join(MISSING) + r")\b")
    if MISSING
    else None
)


def should_ignore(collection_path) -> bool | None:
    """pytest_ignore_collect body: True to skip a module with an absent dep.

    Matches on the module's SOURCE rather than importing it — the whole point
    is to decide without triggering the import that would abort collection.
    """
    if IMPORT_RE is None:
        return None
    p = Path(collection_path)
    if p.suffix != ".py" or not p.name.startswith("test_"):
        return None
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    if any(IMPORT_RE.match(line) for line in text.splitlines()):
        return True
    return None
