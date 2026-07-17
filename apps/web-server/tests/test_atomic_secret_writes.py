"""Secret JSON files must be written atomically and never be world-readable.

#298. The old `path.write_text(json.dumps(...))` + `path.chmod(0o600)` pattern on
claude-profiles.json / api-profiles.json had two defects:

1. Not atomic. write_text truncates in place, so a concurrent reader sees a torn
   file. load_profiles() swallows the JSONDecodeError and returns
   {"profiles": []} -- the reader concludes there are NO profiles, and the next
   save writes that back, permanently destroying every profile and its OAuth
   token. Reproduced against the real load/save pair inside 3s of contention.
2. A world-readable window: write_text creates at the default umask (0644) and
   only chmods to 0600 after the secrets are already on disk.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from server.paths import atomic_write_secret_json


def test_mode_is_0600_from_birth(tmp_path: Path):
    p = tmp_path / "secret.json"
    atomic_write_secret_json(p, {"token": "sk-ant-oat01-SECRET"})
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert json.loads(p.read_text())["token"] == "sk-ant-oat01-SECRET"


def test_concurrent_readers_never_see_a_torn_file(tmp_path: Path):
    """A reader must always get whole-old or whole-new -- never invalid JSON.

    This is the property the old pattern violated and that load_profiles turns
    into silent data loss.
    """
    p = tmp_path / "claude-profiles.json"
    payload = {
        "activeProfileId": "p1",
        "profiles": [
            {"id": f"p{i}", "name": f"Account {i}", "token": f"tok-{i}" * 40}
            for i in range(1, 6)
        ],
    }
    atomic_write_secret_json(p, payload)

    torn: list[str] = []
    stop = False

    def writer() -> None:
        while not stop:
            atomic_write_secret_json(p, payload)

    def reader() -> None:
        while not stop:
            try:
                data = json.loads(p.read_text())
            except json.JSONDecodeError as e:
                torn.append(str(e))
                return
            if len(data.get("profiles", [])) != 5:
                torn.append(f"partial: {len(data.get('profiles', []))} profiles")
                return

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    time.sleep(2)
    stop = True
    for t in threads:
        t.join(timeout=5)

    assert not torn, f"reader saw a torn file: {torn[:3]}"


def test_failed_write_leaves_no_temp_droppings(tmp_path: Path):
    """A serialisation failure must not litter the secrets dir with .tmp files."""
    p = tmp_path / "secret.json"

    class Unserialisable:
        pass

    try:
        atomic_write_secret_json(p, {"bad": Unserialisable()})
    except TypeError:
        pass
    assert not list(tmp_path.glob("*.tmp")), list(tmp_path.glob("*.tmp"))
    assert not p.exists(), "a failed write must not create the target"
