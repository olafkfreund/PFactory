"""RFC-0007 (#86 PR-c): cost-guarded provisioning with mandatory teardown."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from plan.ephemeral_target import (  # noqa: E402
    CostGuard,
    CostGuardError,
    make_liveness_check,
    provisioned,
)


class _FakeTarget:
    def __init__(self, *, live=True, teardown_raises=False):
        self._live = live
        self._teardown_raises = teardown_raises
        self.torn_down = False

    def live(self):
        return self._live

    def teardown(self):
        self.torn_down = True
        if self._teardown_raises:
            raise RuntimeError("teardown boom")


# ---- CostGuard ------------------------------------------------------------- #


def test_no_ceiling_is_denied():
    assert CostGuard(estimate_usd=1.0, max_usd=None).allowed() is False


def test_over_ceiling_denied_within_allowed():
    assert CostGuard(estimate_usd=5.0, max_usd=2.0).allowed() is False
    assert CostGuard(estimate_usd=2.0, max_usd=2.0).allowed() is True


# ---- provisioned (cost guard + mandatory teardown) ------------------------- #


def test_denied_guard_never_provisions():
    created = []

    def factory():
        created.append(1)
        return _FakeTarget()

    with pytest.raises(CostGuardError):
        with provisioned(factory, cost_guard=CostGuard(1.0, None)):
            pass
    assert created == []  # factory never called when the guard denies


def test_teardown_runs_on_normal_exit():
    t = _FakeTarget()
    with provisioned(lambda: t, cost_guard=CostGuard(1.0, 5.0)) as target:
        assert target is t
    assert t.torn_down is True


def test_teardown_runs_even_on_exception():
    t = _FakeTarget()
    with pytest.raises(ValueError):
        with provisioned(lambda: t, cost_guard=CostGuard(1.0, 5.0)):
            raise ValueError("body failed")
    assert t.torn_down is True  # never leaked


def test_teardown_failure_is_swallowed_not_raised():
    t = _FakeTarget(teardown_raises=True)
    # The body succeeds; a teardown error must not surface as the context's error.
    with provisioned(lambda: t, cost_guard=CostGuard(1.0, 5.0)):
        pass
    assert t.torn_down is True


# ---- liveness adapter ------------------------------------------------------ #


def test_liveness_check_reflects_target_and_is_safe():
    assert make_liveness_check(_FakeTarget(live=True))(None) is True
    assert make_liveness_check(_FakeTarget(live=False))(None) is False

    class _Boom:
        def live(self):
            raise RuntimeError("probe down")

    assert make_liveness_check(_Boom())(None) is False  # probe failure => not live
