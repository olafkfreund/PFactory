"""Every invariant added by the Factory#818 expansion must FIRE (PFactory#818).

The pilot proved the mechanism on one first-party package. This file covers the
three seams the expansion added -- the SSRF guard, the vendored surface, and the
sanitisers -- plus the registry's own guard, which was previously the one
package the ownership checker did not look at.

An invariant that never fires under mutation is decoration, so there is one
firing test per registered check. They mutate the module attribute the check
reads at call time rather than the file on disk: ``factory_common`` is vendored
byte-identically from the hub and a test that edits it and crashes leaves the
drift gate red for everyone.

The source-level mutation table (17 mutations, every one of them observed to
fire and every vendored file restored byte-identically) is in the PR body.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for _root in (REPO / "apps" / "backend", REPO / "apps" / "web-server"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

import factory_common  # noqa: E402
import factory_common_invariants  # noqa: E402,F401  -- registers
import factory_invariants as fi  # noqa: E402
import factory_invariants._invariants  # noqa: E402,F401  -- registers
from factory_common import client_errors, http, logsafe, secrets, url_safety  # noqa: E402

# Every module name this expansion declares. Kept as a literal list rather than
# read back out of the registry: a list derived from the thing under test agrees
# with it by construction and cannot notice a declaration that went missing.
NEW_DECLARATIONS = (
    "factory_common.__init__",
    "factory_common.client_errors",
    "factory_common.http",
    "factory_common.logsafe",
    "factory_common.secrets",
    "factory_common.url_safety",
    "factory_invariants.__init__",
)


@pytest.fixture
def live() -> fi.InvariantRegistry:
    """The process registry with checks enabled, restored afterwards."""
    was = fi.registry.enabled
    fi.registry.enabled = True
    yield fi.registry
    fi.registry.enabled = was


def _details(live: fi.InvariantRegistry, module: str) -> list[str]:
    return [v.detail for v in live.check_all() if v.module == module]


def test_every_new_seam_is_registered_and_executable() -> None:
    missing = [m for m in NEW_DECLARATIONS if m not in fi.registry.executable()]
    assert not missing, f"declared but not executable: {missing}"


def test_the_vendored_seams_are_clean_today(live: fi.InvariantRegistry) -> None:
    assert live.check_all() == []


@pytest.mark.parametrize("module", NEW_DECLARATIONS)
def test_a_placeholder_reason_is_refused_for_each_new_module(module: str) -> None:
    """The declared-empty guard applies to the new namespace too.

    This expansion writes no declared-empty entries -- every module in both
    covered packages owned a real relation -- so this is what stands in for
    them: a future author cannot convert one of these to the empty form with a
    placeholder.
    """
    probe = fi.InvariantRegistry()
    with pytest.raises(ValueError, match="declared-empty reason"):
        probe.register(module, reason="TBD — nobody has looked at what this module owns yet")


# ------------------------------------------------------- each invariant FIRES


def test_the_ssrf_guard_fires_when_the_metadata_block_is_narrowed(
    live: fi.InvariantRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Narrowing _METADATA_NETS leaves the strict posture green and the
    permissive one wide open onto the instance-credentials endpoint."""
    import ipaddress

    monkeypatch.setattr(url_safety, "_METADATA_NETS", (ipaddress.ip_network("192.0.2.0/24"),))
    assert any("allow_private=True" in d for d in _details(live, "factory_common.url_safety"))


def test_the_ssrf_guard_fires_when_the_hop_limit_leaves_its_band(
    live: fi.InvariantRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(url_safety, "MAX_REDIRECT_HOPS", 500)
    assert any("MAX_REDIRECT_HOPS is 500" in d for d in _details(live, "factory_common.url_safety"))


def test_the_vendored_surface_fires_on_a_module_nothing_imports(
    live: fi.InvariantRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """logsafe.py on disk, absent from the package namespace: exactly the state
    it and url_safety.py were actually in (Factory#717, #734)."""
    monkeypatch.delattr(factory_common, "logsafe")
    assert any(
        "logsafe.py is vendored but __init__.py never imports it" in d
        for d in _details(live, "factory_common.__init__")
    )


def test_the_vendored_surface_fires_on_a_renamed_ssrf_barrier(
    live: fi.InvariantRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CodeQL barrier is registered BY NAME in four repos."""
    monkeypatch.setattr(
        factory_common,
        "__all__",
        [n for n in factory_common.__all__ if n != "assert_safe_outbound_url"],
    )
    assert any(
        "no longer exported under that exact name" in d
        for d in _details(live, "factory_common.__init__")
    )


def test_the_secret_table_fires_when_it_is_empty(
    live: fi.InvariantRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A zero-item scan is named as such, never reported as clean."""
    monkeypatch.setattr(secrets, "SECRET_PATTERNS", ())
    assert any("SECRET_PATTERNS is empty" in d for d in _details(live, "factory_common.secrets"))


def test_the_secret_table_fires_when_its_functions_disagree(
    live: fi.InvariantRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(secrets, "contains_secret", lambda _text: False)
    assert any(
        "contains_secret() says clean" in d for d in _details(live, "factory_common.secrets")
    )


def test_the_log_sanitiser_fires_when_it_stops_escaping(
    live: fi.InvariantRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(logsafe, "sanitize_log", lambda value, max_length=None: str(value))
    assert any(
        "a record separator survived sanitize_log()" in d
        for d in _details(live, "factory_common.logsafe")
    )


def test_the_http_client_fires_on_the_bot_blocked_user_agent(
    live: fi.InvariantRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(http, "DEFAULT_USER_AGENT", "Python-urllib/3.12")
    assert any("403s the stdlib agent as a bot" in d for d in _details(live, "factory_common.http"))


def test_the_caller_safe_error_fires_when_it_leaves_the_valueerror_tree(
    live: fi.InvariantRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every `except ValueError` around a guard call site, in four repos."""

    class NotAValueError(Exception):  # noqa: N818 - mirrors the name under test
        def __init__(self, client_message: str) -> None:
            super().__init__(client_message)
            self.client_message = client_message

    monkeypatch.setattr(client_errors, "InputRejectedError", NotAValueError)
    assert any(
        "no longer subclasses ValueError" in d
        for d in _details(live, "factory_common.client_errors")
    )


def test_the_registry_guard_fires_when_the_reason_floor_is_dropped(
    live: fi.InvariantRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard that keeps declared-empty an architectural conclusion."""
    monkeypatch.setattr(fi, "_EMPTY_REASON_MIN_WORDS", 0)
    assert any(
        "minimum-length floor no longer applies" in d
        for d in _details(live, "factory_invariants.__init__")
    )


def test_the_registry_guard_fires_when_the_placeholder_pattern_stops_matching(
    live: fi.InvariantRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    import re

    monkeypatch.setattr(fi, "_PLACEHOLDER", re.compile(r"^\s*(zzzzzznomatch)\b", re.I))
    assert any(
        "was ACCEPTED: the empty form is a generated placeholder again" in d
        for d in _details(live, "factory_invariants.__init__")
    )
