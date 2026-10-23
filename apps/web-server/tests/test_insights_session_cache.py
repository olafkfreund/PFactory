"""Two pods share the insights session files; neither may drop the other's messages (#912)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from server.services import insights_service
from server.services.insights_service import InsightsMessage, InsightsService


def _say(service: InsightsService, project: Path, text: str) -> None:
    session = service.get_current_session(project, "proj")
    session.messages.append(InsightsMessage(id=text, role="user", content=text, timestamp="t"))
    service._save_session(project, session)


def test_alternating_pods_keep_every_message(tmp_path: Path) -> None:
    pod_a, pod_b = InsightsService(), InsightsService()
    session_id = pod_a.get_current_session(tmp_path, "proj").id
    pod_b.get_current_session(tmp_path, "proj")  # B has seen the session before A writes

    _say(pod_a, tmp_path, "m1")
    _say(pod_b, tmp_path, "m2")
    _say(pod_a, tmp_path, "m3")

    fresh = InsightsService()._load_session(tmp_path, session_id)
    assert fresh is not None
    assert [m.content for m in fresh.messages] == ["m1", "m2", "m3"]


def test_rename_during_reply_survives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replying, other_pod = InsightsService(), InsightsService()
    session_id = replying.get_current_session(tmp_path, "proj").id

    class Provider:
        async def send_message(self, **_kwargs: Any) -> str:
            other_pod.rename_session(tmp_path, session_id, "Renamed")
            return "answer"

    monkeypatch.setattr(insights_service, "get_provider", lambda _pid: Provider())

    async def no_broadcast(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(insights_service, "broadcast_event", no_broadcast)
    asyncio.run(replying.send_message(tmp_path, "proj", "hello"))

    fresh = InsightsService()._load_session(tmp_path, session_id)
    assert fresh is not None
    assert fresh.title == "Renamed"
    assert [m.content for m in fresh.messages] == ["hello", "answer"]
