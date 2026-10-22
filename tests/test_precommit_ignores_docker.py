"""The pre-commit hook must not run docker-marked tests (#840).

They build and inspect an image under a fixed shared tag on the host daemon, so
a concurrent build can make them fail, and the hook's `-x` turns that into a
rejected commit. CI's docker job is what gates the image.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_HOOK = _REPO / ".husky" / "pre-commit"
_DOCKER_DIR = (_REPO / "tests" / "docker").resolve()


def _ignored_paths() -> set[Path]:
    block = re.search(r'IGNORE_TESTS="(.*?)"', _HOOK.read_text(), re.S)
    assert block, "IGNORE_TESTS not found in .husky/pre-commit"
    # The hook runs pytest from apps/backend, so --ignore paths are relative to it.
    cwd = _REPO / "apps" / "backend"
    return {(cwd / p).resolve() for p in re.findall(r"--ignore=(\S+)", block.group(1))}


def test_hook_ignores_docker_tests() -> None:
    assert _DOCKER_DIR in _ignored_paths()


def test_every_docker_marked_test_is_under_the_ignored_dir() -> None:
    outside = [
        p
        for p in (_REPO / "tests").rglob("test_*.py")
        if re.search(
            r"^\s*(@|pytestmark\s*=\s*)pytest\.mark\.docker\b", p.read_text(errors="ignore"), re.M
        )
        and _DOCKER_DIR not in p.resolve().parents
    ]
    assert outside == []
