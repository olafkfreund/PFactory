"""Apply the split-venv missing-dep skip to the web-server's own test tree.

`tests/conftest.py` has had this mechanism since the split-venv layout landed,
but a conftest's hooks apply to its own directory and below — and this tree is
a SIBLING of `tests/`, not a child. So the one directory the mechanism exists
to protect was the one directory it never ran in, and
`apps/web-server/tests/test_tracing.py` raised `ModuleNotFoundError:
opentelemetry` at collection, aborting `pytest tests/ apps/web-server/tests/`
in full (#453).

The logic is shared rather than copied: two divergent copies of a skip rule is
how the list came to know `fastapi` and `sqlalchemy` and nothing else.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

# Put `apps/web-server` on sys.path for every module in this tree.
#
# Twenty-one of these modules do this insert themselves; eleven of the
# thirty-five cannot be imported without one and do not have it, so they resolve
# `server.*` only because an alphabetically-earlier module that DOES insert
# happened to be collected first. That holds for a full run and breaks on any
# order change: a single module, a `-k` filter, an xdist split, any plugin that
# shuffles collection.
#
# Four of the eleven are security tests -- test_log_forgery,
# test_project_workspace_service_credential_leak, test_route_error_leak and
# test_insights_error_leak. A collection-order change would take those out
# silently, and a suite that skipped them reports the same green as one that
# ran them.
#
# The per-module inserts stay; they are all guarded by an `in sys.path` check.
_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

_MECHANISM = Path(__file__).resolve().parents[3] / "tests" / "missing_deps.py"
_spec = importlib.util.spec_from_file_location("missing_deps", _MECHANISM)
if _spec is None or _spec.loader is None:  # pragma: no cover - the file is in-tree
    raise RuntimeError(f"cannot load the missing-dep skip mechanism from {_MECHANISM}")
missing_deps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(missing_deps)


# `config` is unused, but pytest matches hook parameters BY NAME, so it can be
# neither renamed nor dropped.
def pytest_ignore_collect(collection_path: Path, config: Any) -> bool | None:  # noqa: ARG001
    """Skip a test module that imports a web-server dep absent from this venv."""
    # Annotated local: `missing_deps` is loaded by path, so its members are Any.
    ignored: bool | None = missing_deps.should_ignore(collection_path)
    return ignored
