"""Tests for the Gen-Functional flake-risk lint — Task 6 (#7) commit 3.

Covers each of the five anti-patterns with positive (should fire) and
negative (should NOT fire) cases, plus end-to-end scenarios and the
syntax-error early return.
"""

from __future__ import annotations

import pytest
from agents.flake_risk_lint import (
    FlakeRiskResult,
    flake_risk_lint,
)


def _hits_by_pattern(res: FlakeRiskResult, pattern: str) -> list:
    return [h for h in res.hits if h.pattern == pattern]


# ── Pattern 1: dict iteration order ─────────────────────────────────────


def test_dict_keys_compare_to_list_is_rejected() -> None:
    src = "def t():\n    d = {1: 'a'}\n    assert d.keys() == [1]\n"
    res = flake_risk_lint(src)
    assert not res.ok
    hits = _hits_by_pattern(res, "dict_iteration_order")
    assert len(hits) == 1
    assert hits[0].severity == "high"


def test_list_of_dict_items_compare_is_rejected() -> None:
    src = "def t():\n    d = {1: 'a'}\n    assert list(d.items()) == [(1, 'a')]\n"
    res = flake_risk_lint(src)
    assert not res.ok
    assert _hits_by_pattern(res, "dict_iteration_order")


def test_list_of_dict_values_compare_is_rejected() -> None:
    src = "def t():\n    d = {1: 'a'}\n    assert list(d.values()) == ['a']\n"
    res = flake_risk_lint(src)
    assert not res.ok
    assert _hits_by_pattern(res, "dict_iteration_order")


def test_tuple_of_dict_keys_compare_is_rejected() -> None:
    src = "def t():\n    d = {1: 'a'}\n    assert tuple(d.keys()) == (1,)\n"
    res = flake_risk_lint(src)
    assert not res.ok
    assert _hits_by_pattern(res, "dict_iteration_order")


def test_sorted_dict_keys_compare_is_NOT_flagged() -> None:
    """sorted() makes the order deterministic — no flake risk."""
    src = "def t():\n    d = {1: 'a', 2: 'b'}\n    assert sorted(d.keys()) == [1, 2]\n"
    res = flake_risk_lint(src)
    # sorted() wraps d.keys() so the iteration-order risk is mitigated;
    # the lint doesn't fire because it only matches list/tuple-wrappers.
    assert res.ok


def test_set_comparison_of_dict_keys_is_NOT_flagged() -> None:
    """`set(d.keys()) == {1, 2}` is fine — sets are order-insensitive."""
    src = "def t():\n    d = {1: 'a', 2: 'b'}\n    assert set(d.keys()) == {1, 2}\n"
    res = flake_risk_lint(src)
    assert res.ok


# ── Pattern 2: set iteration order ──────────────────────────────────────


def test_list_of_set_literal_compare_is_rejected() -> None:
    src = "def t():\n    assert list({1, 2, 3}) == [1, 2, 3]\n"
    res = flake_risk_lint(src)
    assert not res.ok
    assert _hits_by_pattern(res, "set_iteration_order")


def test_list_of_set_variable_compare_is_rejected() -> None:
    """Variable bound to a set literal in the same scope is caught."""
    src = (
        "def t():\n"
        "    s = {1, 2, 3}\n"
        "    assert list(s) == [1, 2, 3]\n"
    )
    res = flake_risk_lint(src)
    assert not res.ok
    assert _hits_by_pattern(res, "set_iteration_order")


def test_list_of_set_via_unknown_variable_is_NOT_flagged() -> None:
    """When we can't tell if x is a set (no local binding), don't flag.
    Downstream stability re-runs catch the false negative."""
    src = "def t(x):\n    assert list(x) == [1, 2, 3]\n"  # x could be anything
    res = flake_risk_lint(src)
    # No set_iteration_order hit because we can't prove x is a set.
    assert not _hits_by_pattern(res, "set_iteration_order")


# ── Pattern 3: time.sleep ───────────────────────────────────────────────


def test_time_sleep_is_flagged_not_rejected() -> None:
    src = "import time\ndef t():\n    time.sleep(0.1)\n"
    res = flake_risk_lint(src)
    hits = _hits_by_pattern(res, "time_sleep")
    assert len(hits) == 1
    assert hits[0].severity == "medium"
    # Flag-only means .ok is True (no high-severity hit alone here)
    assert res.ok


def test_bare_sleep_is_flagged() -> None:
    src = "from time import sleep\ndef t():\n    sleep(0.05)\n"
    res = flake_risk_lint(src)
    assert _hits_by_pattern(res, "time_sleep")
    assert res.ok  # medium-only


# ── Pattern 4: datetime.now without freezing ────────────────────────────


def test_datetime_now_without_freeze_is_flagged() -> None:
    src = "import datetime\ndef t():\n    x = datetime.datetime.now()\n"
    res = flake_risk_lint(src)
    hits = _hits_by_pattern(res, "datetime_now_no_freeze")
    assert len(hits) == 1
    assert hits[0].severity == "medium"


def test_datetime_utcnow_without_freeze_is_flagged() -> None:
    src = "from datetime import datetime\ndef t():\n    x = datetime.utcnow()\n"
    res = flake_risk_lint(src)
    assert _hits_by_pattern(res, "datetime_now_no_freeze")


def test_datetime_now_WITH_freezegun_is_NOT_flagged() -> None:
    src = (
        "from freezegun import freeze_time\n"
        "import datetime\n"
        "@freeze_time('2026-01-01')\n"
        "def t():\n"
        "    x = datetime.datetime.now()\n"
    )
    res = flake_risk_lint(src)
    assert not _hits_by_pattern(res, "datetime_now_no_freeze")


def test_datetime_now_WITH_time_machine_is_NOT_flagged() -> None:
    src = (
        "import time_machine\n"
        "import datetime\n"
        "def t():\n"
        "    with time_machine.travel('2026-01-01'):\n"
        "        x = datetime.datetime.now()\n"
    )
    res = flake_risk_lint(src)
    assert not _hits_by_pattern(res, "datetime_now_no_freeze")


# ── Pattern 5: random without seed ──────────────────────────────────────


def test_random_choice_without_seed_is_rejected() -> None:
    src = "import random\ndef t():\n    x = random.choice([1, 2, 3])\n"
    res = flake_risk_lint(src)
    assert not res.ok
    hits = _hits_by_pattern(res, "random_no_seed")
    assert hits and hits[0].severity == "high"


def test_random_randint_without_seed_is_rejected() -> None:
    src = "import random\ndef t():\n    x = random.randint(0, 9)\n"
    res = flake_risk_lint(src)
    assert _hits_by_pattern(res, "random_no_seed")


def test_random_shuffle_without_seed_is_rejected() -> None:
    src = "import random\ndef t():\n    random.shuffle([1, 2, 3])\n"
    res = flake_risk_lint(src)
    assert _hits_by_pattern(res, "random_no_seed")


def test_random_WITH_seed_is_NOT_rejected() -> None:
    src = (
        "import random\n"
        "random.seed(42)\n"
        "def t():\n"
        "    x = random.choice([1, 2, 3])\n"
    )
    res = flake_risk_lint(src)
    assert not _hits_by_pattern(res, "random_no_seed")


def test_random_with_pytest_randomly_is_NOT_rejected() -> None:
    """pytest-randomly auto-seeds — presence of the import bypasses."""
    src = (
        "import pytest_randomly  # noqa\n"
        "import random\n"
        "def t():\n"
        "    x = random.choice([1, 2, 3])\n"
    )
    res = flake_risk_lint(src)
    assert not _hits_by_pattern(res, "random_no_seed")


def test_random_SystemRandom_is_not_flagged() -> None:
    """SystemRandom uses OS entropy; deterministic seeding doesn't apply."""
    src = (
        "import random\n"
        "def t():\n"
        "    sr = random.SystemRandom()\n"
        "    x = sr.choice([1, 2, 3])\n"
    )
    res = flake_risk_lint(src)
    # No random.* CALL at the module-level random.* — the entropy method
    # is invoked on the SystemRandom instance which we don't track.
    assert not _hits_by_pattern(res, "random_no_seed")


# ── End-to-end ─────────────────────────────────────────────────────────


def test_clean_test_passes_lint() -> None:
    """A well-behaved test source should produce no hits."""
    src = (
        "import pytest\n"
        "from app.auth import login_user\n"
        "\n"
        "def test_login_returns_session_with_expires_at():\n"
        "    s = login_user('a@b.com', 'pw')\n"
        "    assert s.email == 'a@b.com'\n"
        "    assert s.expires_at is not None\n"
    )
    res = flake_risk_lint(src)
    assert res.ok
    assert res.hits == []
    assert "OK" in res.summary()


def test_kitchen_sink_test_collects_all_patterns() -> None:
    """A test that hits every anti-pattern — proves no patterns miss."""
    src = (
        "import time\n"
        "import random\n"
        "import datetime\n"
        "\n"
        "def test_kitchen_sink():\n"
        "    d = {1: 'a', 2: 'b'}\n"
        "    assert list(d.keys()) == [1, 2]\n"
        "    s = {3, 4}\n"
        "    assert list(s) == [3, 4]\n"
        "    time.sleep(0.1)\n"
        "    now = datetime.datetime.now()\n"
        "    x = random.choice([1, 2, 3])\n"
    )
    res = flake_risk_lint(src)
    patterns_hit = {h.pattern for h in res.hits}
    assert patterns_hit == {
        "dict_iteration_order",
        "set_iteration_order",
        "time_sleep",
        "datetime_now_no_freeze",
        "random_no_seed",
    }
    # 3 reject (dict, set, random) + 2 flag (sleep, datetime)
    assert len(res.rejected) == 3
    assert len(res.flagged) == 2
    assert res.ok is False  # any high-severity hit blocks


def test_summary_distinguishes_reject_and_flag() -> None:
    src = (
        "import time\n"
        "def test_x():\n"
        "    time.sleep(0.1)  # medium only\n"
    )
    res = flake_risk_lint(src)
    assert res.ok is True
    assert "1 flag" in res.summary()
    assert "reject" not in res.summary()


def test_syntax_error_returns_early() -> None:
    res = flake_risk_lint("def \n")
    assert not res.ok
    assert res.syntax_error is not None
    assert "SyntaxError" in res.syntax_error
    assert res.hits == []


def test_empty_source_is_ok() -> None:
    res = flake_risk_lint("")
    assert res.ok
    assert res.hits == []


def test_lineno_reported_correctly() -> None:
    src = (
        "import random\n"        # line 1
        "\n"                      # line 2
        "def t():\n"              # line 3
        "    x = random.choice([1, 2])\n"  # line 4 ← the hit
    )
    res = flake_risk_lint(src)
    hits = _hits_by_pattern(res, "random_no_seed")
    assert hits[0].lineno == 4
