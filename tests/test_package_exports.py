"""Every name in a lazy package's ``__all__`` must actually resolve.

These packages bind their exports lazily through a module-level ``__getattr__``
(PEP 562), so a stale ``__all__`` entry is invisible until someone does
``from pkg import X`` in production. Regression guard for the CodeQL
``py/undefined-export`` cluster that found ``core.WorkspaceManager`` and
``core.ProgressTracker`` pointing at modules that no longer defined them.
"""

import importlib

import pytest

LAZY_PACKAGES = ["agents", "core", "integrations.graphiti"]


@pytest.mark.parametrize("package", LAZY_PACKAGES)
def test_all_exports_resolve(package):
    module = importlib.import_module(package)
    unresolved = []
    for name in module.__all__:
        try:
            getattr(module, name)
        except Exception as exc:  # noqa: BLE001 - report every broken export at once
            unresolved.append(f"{package}.{name}: {type(exc).__name__}: {exc}")
    assert not unresolved, "unresolvable __all__ entries:\n" + "\n".join(unresolved)
