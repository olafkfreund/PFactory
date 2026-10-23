"""Two pods share the insights session files; neither may drop the other's messages (#912)."""

from __future__ import annotations

from pathlib import Path

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
