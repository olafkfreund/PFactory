"""#807: the GitHub CLI device flow refuses when PFactory may run as several pods.

`gh auth login` writes its credential into the HOME of the pod that ran it, so
with more than one replica a "success" would authenticate one pod only.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.routes import github as github_routes  # noqa: E402


class _SpawnedError(Exception):
    """Raised by the fake subprocess so the flow stops right after spawning."""


@pytest.fixture
def spawns(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    async def fake_exec(*args: Any, **_kwargs: Any) -> Any:
        calls.append(args)
        raise _SpawnedError

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return calls


def _start() -> Any:
    try:
        return asyncio.run(github_routes.start_github_auth())  # type: ignore[no-untyped-call]
    except _SpawnedError:
        return None


def test_several_replicas_refuse_before_spawning_gh(
    monkeypatch: pytest.MonkeyPatch, spawns: list[tuple[Any, ...]]
) -> None:
    monkeypatch.setenv("PFACTORY_REPLICA_COUNT", "2")
    result = _start()
    assert spawns == [], "gh was spawned on a multi-replica deployment"
    assert result["success"] is True
    assert result["data"]["success"] is False
    assert "GITHUB_TOKEN" in result["data"]["message"]


@pytest.mark.parametrize("value", [None, "1", "not-a-number"])
def test_one_replica_still_starts_the_flow(
    monkeypatch: pytest.MonkeyPatch, spawns: list[tuple[Any, ...]], value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("PFACTORY_REPLICA_COUNT", raising=False)
    else:
        monkeypatch.setenv("PFACTORY_REPLICA_COUNT", value)
    _start()
    assert len(spawns) == 1, "the single-replica flow did not reach gh"
