"""Swift and Kotlin through the planning pipeline (RFC-0005 paved road).

The three sites that used to hardcode languages now read the vendored
``plan/languages/*.yaml`` descriptors:

- ``detect/target_classifier``: descriptor aliases score as software signals.
- ``emit/tfactory_block``: a descriptor-declared plan language produces lanes
  from what the descriptor has PROVEN runnable, and everything it refuses lands
  in ``unavailable_lanes`` with its mandatory reason.
- ``emit/environment_block``: lane commands, proof and network come from the
  descriptor; the ``"python" if pytest else "typescript"`` binary (which
  silently labelled every non-pytest plan TypeScript) is gone, and a language
  the descriptors cannot resolve is REFUSED rather than mislabelled.

Each block below carries its negative control: the descriptor path unwired must
change the observable result, or these tests would pass over decoration.
"""

from __future__ import annotations

import pytest

from plan.decompose.models import ChildIssue, EpicPlan
from plan.detect import target_classifier
from plan.emit import environment_block, tfactory_block
from plan.emit.environment_block import derive_environment
from plan.emit.tfactory_block import build_tfactory
from plan.models import NormalizedPlan


def _plan(text: str) -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-native",
        title="Native app work",
        description=text,
        source_format="markdown",
    ).with_hash()


def _epic() -> EpicPlan:
    return EpicPlan(
        plan_id="001-native",
        epic_title="Native app work",
        children=[ChildIssue(key="C1", title="a", kind="feature")],
    )


# ── target_classifier ───────────────────────────────────────────────────────


def test_swift_ios_text_classifies_as_software() -> None:
    result = target_classifier.classify_text(
        "Build the iOS client in Swift with offline sync for the profile screens"
    )
    assert result.kind == "software"
    assert "swift" in result.matched or "ios" in result.matched


def test_kotlin_android_text_classifies_as_software() -> None:
    result = target_classifier.classify_text(
        "Implement the Android onboarding flow in Kotlin with camera permissions"
    )
    assert result.kind == "software"


def test_negative_control_classifier_without_descriptors(monkeypatch) -> None:
    """Unwire the descriptor signals; the same text must stop classifying.

    Proves the classification above comes FROM the descriptors, not from the
    static lexicon happening to cover the words.
    """
    monkeypatch.setattr(
        target_classifier, "_software_signals", lambda: dict(target_classifier._SOFTWARE_SIGNALS)
    )
    result = target_classifier.classify_text(
        "Build the iOS client in Swift with offline sync for the profile screens"
    )
    assert result.kind != "software"


# ── tfactory_block ──────────────────────────────────────────────────────────


def test_swift_plan_gets_xctest_unit_lane_and_language() -> None:
    block = build_tfactory(_plan("A Swift SPM library for the iOS app's matching logic"), _epic())
    assert block["language"] == "swift"
    assert block["lanes"] == ["unit"]
    assert block["frameworks"]["unit"] == "xctest"


def test_swift_browser_signal_is_refused_with_a_reason() -> None:
    """A Swift plan whose text implies a browser lane must NOT get one — the
    descriptor refuses it, and the refusal travels with its reason."""
    block = build_tfactory(
        _plan("Swift iOS app; the marketing frontend pages show live data"), _epic()
    )
    assert "browser" not in block["lanes"]
    assert "browser" in block.get("unavailable_lanes", {})
    assert block["unavailable_lanes"]["browser"]


def test_kotlin_api_plan_gets_junit_lanes() -> None:
    block = build_tfactory(
        _plan("Kotlin Android app with a companion REST api endpoint layer"), _epic()
    )
    assert block["language"] == "kotlin"
    assert block["lanes"] == ["unit", "api"]
    assert block["frameworks"] == {"unit": "junit", "api": "junit"}


def test_python_plans_keep_the_existing_path() -> None:
    block = build_tfactory(_plan("A FastAPI python service with pytest coverage"), _epic())
    assert block["language"] == "python"
    assert block["frameworks"]["unit"] == "pytest"


def test_negative_control_tfactory_without_descriptors(monkeypatch) -> None:
    """Unwire the descriptors; the swift plan must fall to the legacy path
    (and be labelled by the old binary) — proving the native branch is what
    produced the xctest block above."""
    monkeypatch.setattr(tfactory_block, "_native_descriptor", lambda _plan: None)
    block = build_tfactory(_plan("A Swift SPM library for the iOS app"), _epic())
    assert block["frameworks"]["unit"] != "xctest"


# ── environment_block ───────────────────────────────────────────────────────


def _contract_for(language: str, lanes: list[str], frameworks: dict[str, str]) -> dict:
    return {"tfactory": {"language": language, "lanes": lanes, "frameworks": frameworks}}


def test_swift_environment_runs_swift_test() -> None:
    env = derive_environment(_contract_for("swift", ["unit"], {"unit": "xctest"}))
    assert env is not None
    assert env["language"] == "swift"
    assert env["verify_commands"] == ["swift test"]
    assert env["proof"] == {"verify": ["swift --version"]}
    # SPM resolves dependencies over git at build time.
    assert env["network"] == "restricted"
    assert env["provisioning"]["method"] == "nix"


def test_kotlin_environment_dedupes_the_gradle_invocation() -> None:
    env = derive_environment(
        _contract_for("kotlin", ["unit", "api"], {"unit": "junit", "api": "junit"})
    )
    assert env is not None
    assert env["language"] == "kotlin"
    # unit and api share one gradle test run; twice would double the wall
    # clock for zero extra evidence.
    assert env["verify_commands"] == ["gradle test --no-daemon --console=plain"]
    assert env["network"] == "restricted"


def test_the_old_typescript_binary_bug_stays_dead() -> None:
    """THE bug this change removes: a non-pytest framework used to make the
    whole environment TypeScript. A swift block must never emit node."""
    env = derive_environment(_contract_for("swift", ["unit"], {"unit": "xctest"}))
    assert env is not None
    assert env["language"] != "typescript"
    assert "npm test" not in env["verify_commands"]


def test_legacy_blocks_without_language_keep_working() -> None:
    env = derive_environment({"tfactory": {"lanes": ["unit"], "frameworks": {"unit": "jest"}}})
    assert env is not None
    assert env["language"] == "typescript"
    assert env["verify_commands"] == ["npm test"]


def test_negative_control_unresolvable_language_is_refused(monkeypatch) -> None:
    """Descriptors unwired + a native language named -> loud refusal.

    Falling back to the pytest/jest binary here would silently re-create the
    original defect (a Swift plan shipping a TypeScript environment)."""
    monkeypatch.setattr(environment_block, "_descriptor_environment", lambda *_a: None)
    with pytest.raises(ValueError, match="no ") as exc:
        derive_environment(_contract_for("swift", ["unit"], {"unit": "xctest"}))
    assert "swift" in str(exc.value)
