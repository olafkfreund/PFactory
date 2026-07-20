"""Path-traversal guards for caller-supplied spec/task identifiers (#335).

Identifiers arriving in a request body are joined onto trusted project roots
and then read from and written to. ``Path`` joins collapse traversal silently
-- ``Path("/srv/specs") / "../../etc"`` is ``/etc`` -- so a component has to be
rejected before it is joined, never inspected afterwards.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WEB = Path(__file__).resolve().parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))

pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402

from server.routes import tasks as tasks_mod  # noqa: E402
from server.routes.pfactory_tasks import _validate_spec_id  # noqa: E402
from server.routes.tasks import split_task_id  # noqa: E402
from server.services.git_utils import safe_spec_component  # noqa: E402

TRAVERSAL = [
    "..",  # the whole point: resolves to the parent of the root
    ".",
    "../etc",
    "../../../../etc/passwd",
    "/etc/passwd",  # absolute paths replace the root entirely
    "a/b",  # separators make it more than one component
    "a\\b",
    "",
    "spec\x00id",  # null byte truncation
    "x" * 256,  # length bound
]


@pytest.mark.parametrize("value", TRAVERSAL)
def test_traversal_components_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        safe_spec_component(value)


@pytest.mark.parametrize(
    "value", ["001-add-a-helper", "spec_1.2", "A-B_c.d", "058-feature", "x" * 255]
)
def test_real_identifiers_still_pass(value: str) -> None:
    assert safe_spec_component(value) == value


def test_dot_dot_cannot_reach_a_parent_directory(tmp_path: Path) -> None:
    """The concrete escape this exists to stop."""
    root = tmp_path / "project" / ".pfactory" / "specs"
    root.mkdir(parents=True)
    # Unguarded, this is the traversal:
    assert (root / "..").resolve() == root.parent
    # Guarded, it never gets joined:
    with pytest.raises(ValueError):
        safe_spec_component("..")


@pytest.mark.parametrize("value", TRAVERSAL)
def test_route_validator_rejects_the_same_set(value: str) -> None:
    """pfactory_tasks shares the check, so the two cannot drift apart.

    Its own regex previously admitted "." and ".." -- both match
    ``^[A-Za-z0-9._-]+$`` -- so this is a real tightening, not a restatement.
    """
    with pytest.raises(HTTPException) as exc:
        _validate_spec_id(value)
    assert exc.value.status_code == 400


# ── phase 2: routes/tasks.py choke points (#335) ────────────────────────


@pytest.mark.parametrize("value", TRAVERSAL)
def test_split_task_id_rejects_traversal_in_the_spec_half(value: str) -> None:
    """The 18 handlers taking a task id all route through this one splitter."""
    with pytest.raises(HTTPException) as exc:
        split_task_id(f"project-uuid:{value}")
    assert exc.value.status_code == 400


def test_split_task_id_returns_both_halves_for_a_real_id() -> None:
    assert split_task_id("proj-uuid:001-add-helper") == ("proj-uuid", "001-add-helper")


def test_split_task_id_rejects_an_id_with_no_spec_half() -> None:
    """Preserves the old behaviour: `"x".split(":", 1)` raised too."""
    with pytest.raises(HTTPException):
        split_task_id("no-colon-here")


@pytest.mark.parametrize(
    "helper",
    ["get_worktree_spec_dir", "sync_worktree_to_main_spec", "get_plan_with_worktree_sync"],
)
def test_path_building_helpers_reject_traversal(helper: str, tmp_path: Path) -> None:
    """Guarded inside each helper, so every caller is covered rather than each join."""
    fn = getattr(tasks_mod, helper)
    with pytest.raises(ValueError):
        fn(tmp_path, "../../../../etc")
