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
from pathlib import Path
from typing import Any

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
