"""Fixtures for the rmux wrapper tests.

The ``rmux`` mark gates tests that require a real rmux binary on PATH
(integration round-trip).  CI installs the binary on the runner; locally
they run if the binary is present and skip otherwise — same posture as
``-m docker`` / ``-m postgres``.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

# Web-server source root on PYTHONPATH so ``from server.rmux ...`` resolves.
_WEB_SERVER = Path(__file__).resolve().parents[2] / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))


def pytest_collection_modifyitems(config, items):
    """Auto-skip ``-m rmux`` items if the binary isn't on PATH.

    CI runners get rmux installed via the workflow; locally, devs without
    rmux installed still get the unit tests (no mark) to pass.
    """
    if shutil.which("rmux") is not None:
        return
    skip_marker = pytest.mark.skip(reason="rmux binary not installed on PATH")
    for item in items:
        if "rmux" in item.keywords:
            item.add_marker(skip_marker)
