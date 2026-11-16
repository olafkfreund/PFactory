"""The control plane marks itself non-dumpable and fails closed (Factory#3737)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "backend"))

from core import process_hardening as ph


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="prctl is Linux only")
def test_prctl_clears_the_dumpable_flag():
    # Run in a child: the flag is per process and would hide this one from py-spy.
    import subprocess  # noqa: PLC0415

    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from core import process_hardening as ph;"
        "assert ph.make_non_dumpable();"
        "import ctypes; assert ctypes.CDLL(None).prctl(3, 0, 0, 0, 0) == 0"
    )
    backend = str(Path(ph.__file__).parents[1])
    subprocess.run([sys.executable, "-c", code, backend], check=True, timeout=30)  # noqa: S603


def test_require_non_dumpable_fails_closed(monkeypatch):
    monkeypatch.setattr(ph, "make_non_dumpable", lambda: False)
    with pytest.raises(SystemExit):
        ph.require_non_dumpable()
