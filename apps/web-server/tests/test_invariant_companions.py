"""Every ``*_invariants`` companion registers, even after the migrations ran (#911)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

WEB_SERVER = Path(__file__).resolve().parents[1]

# Runs in a subprocess: importing every companion in-process would pollute
# sys.modules for the rest of the suite.
_PROBE = """
import importlib.util, sys
from pathlib import Path
# As in the app: the routes put apps/backend on sys.path before init_db() runs the migrations.
sys.path[:0] = [str(Path("../backend").resolve()), str(Path(".").resolve())]
mig = next(Path("server/database/alembic/versions").glob("*_encrypt_credentials.py"))
spec = importlib.util.spec_from_file_location("probe_migration", mig)
spec.loader.exec_module(importlib.util.module_from_spec(spec))
bad = [p for p in sys.path if p.rstrip("/").endswith("web-server/server")]
assert not bad, f"migration put {bad} on sys.path"
from server.background import invariants
roots = invariants._ROOTS
names = [f"{p.parent.name}._invariants" for r in roots for p in r.glob("*/_invariants.py")]
names += [p.stem for r in roots for p in r.glob("*_invariants.py")]
loaded = invariants.load_companions()
assert "services._invariants" in names
missing = sorted(set(names) - set(loaded))
assert not missing, f"companions failed to register: {missing}"
"""


def test_every_companion_registers_after_migrations() -> None:
    r = subprocess.run(  # noqa: S603  (fixed argv: this interpreter and a literal probe)
        [sys.executable, "-c", _PROBE],
        cwd=WEB_SERVER,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert r.returncode == 0, r.stderr[-2000:]
