"""Tests for the Gen-Functional prompt assembly helper — Task 6 (#7 v0.1 / #22 v0.2).

The helper assembles a per-subtask system prompt by loading
``apps/backend/prompts/gen_functional.md`` and prepending a SUBTASK
CONTEXT block. Covered:

  - Path injection (spec_dir, project_dir, files_to_create resolved
    against spec_dir for the Write target)
  - Subtask-field injection (id, description, target, rationale,
    verification command)
  - Both shapes: Subtask dataclass AND raw dict (post-JSON-load shape)
  - Verification command discovery handles both schema variants
    (``run`` from the dataclass, ``command`` from the planner prompt)
  - FileNotFoundError when gen_functional.md is missing
  - Body content sanity (mentions key rules + tools + anti-patterns)

  v0.2 additions (Task 6 / #22):
  - FRAMEWORK CONTEXT injection via FrameworkDescriptor.context_block
  - v0.1 legacy path uses gen_functional-v01-legacy.md + DeprecationWarning
  - SUBTASK CONTEXT precedes FRAMEWORK CONTEXT precedes generic body
  - intent: update subtask write-path mention
  - Combined prompt size guard (< 15KB)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from prompts_pkg.prompts import get_pfactory_gen_functional_prompt
from test_plan import Lane, Subtask, Verification, VerificationType

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def subtask_dataclass() -> Subtask:
    return Subtask(
        id="ac1-login-expiry",
        description="Verify login_user sets expires_at to +24h",
        lane=Lane.UNIT,
        target="app/auth/login.py::login_user",
        rationale="AC#1: login_user returns session with expires_at=+24h",
        files_to_create=["tests/test_login_expiry.py"],
        verification=Verification(
            type=VerificationType.COMMAND,
            run="pytest tests/test_login_expiry.py",
        ),
    )


@pytest.fixture
def subtask_dict() -> dict:
    """The shape that ImplementationPlan.load() produces from JSON."""
    return {
        "id": "ac1-login-expiry",
        "description": "Verify login_user sets expires_at to +24h",
        "lane": "functional",
        "target": "app/auth/login.py::login_user",
        "rationale": "AC#1: login_user returns session with expires_at=+24h",
        "files_to_create": ["tests/test_login_expiry.py"],
        "verification": {
            "type": "command",
            "command": "pytest tests/test_login_expiry.py",
        },
    }


# ── Path + field injection ──────────────────────────────────────────────


def test_includes_spec_dir(subtask_dataclass: Subtask) -> None:
    p = get_pfactory_gen_functional_prompt(
        Path("/ws/demo/001"), Path("/proj"), subtask_dataclass,
    )
    assert "/ws/demo/001" in p


def test_includes_project_dir(subtask_dataclass: Subtask) -> None:
    p = get_pfactory_gen_functional_prompt(
        Path("/ws/demo/001"), Path("/proj"), subtask_dataclass,
    )
    assert "/proj" in p


def test_includes_subtask_id_and_description(subtask_dataclass: Subtask) -> None:
    p = get_pfactory_gen_functional_prompt(
        Path("/ws/x"), Path("/p"), subtask_dataclass,
    )
    assert "ac1-login-expiry" in p
    assert "expires_at" in p  # from description


def test_includes_target(subtask_dataclass: Subtask) -> None:
    p = get_pfactory_gen_functional_prompt(
        Path("/ws/x"), Path("/p"), subtask_dataclass,
    )
    assert "app/auth/login.py::login_user" in p


def test_includes_rationale(subtask_dataclass: Subtask) -> None:
    p = get_pfactory_gen_functional_prompt(
        Path("/ws/x"), Path("/p"), subtask_dataclass,
    )
    assert "AC#1" in p


def test_write_path_resolves_against_spec_dir(subtask_dataclass: Subtask) -> None:
    p = get_pfactory_gen_functional_prompt(
        Path("/ws/demo/001"), Path("/p"), subtask_dataclass,
    )
    # The agent should see the full absolute path it must Write to.
    assert "/ws/demo/001/tests/test_login_expiry.py" in p


def test_includes_verification_command_from_dataclass(subtask_dataclass: Subtask) -> None:
    p = get_pfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dataclass,
    )
    assert "pytest tests/test_login_expiry.py" in p


# ── Dict shape (post-JSON-load) ─────────────────────────────────────────


def test_accepts_dict_shape(subtask_dict: dict) -> None:
    p = get_pfactory_gen_functional_prompt(
        Path("/ws/demo/001"), Path("/proj"), subtask_dict,
    )
    assert "ac1-login-expiry" in p
    assert "app/auth/login.py::login_user" in p


def test_dict_verification_uses_command_key(subtask_dict: dict) -> None:
    """Planner emits ``"command"`` in JSON; helper finds it."""
    p = get_pfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dict,
    )
    assert "pytest tests/test_login_expiry.py" in p


def test_dict_verification_falls_back_to_run_key() -> None:
    """The Verification.to_dict() shape uses ``run`` — helper supports that too."""
    sd = {
        "id": "x", "description": "y", "lane": "functional",
        "target": "f.py::g", "rationale": "AC#X",
        "files_to_create": ["tests/x.py"],
        "verification": {"type": "command", "run": "pytest tests/x.py"},
    }
    p = get_pfactory_gen_functional_prompt(Path("/w"), Path("/p"), sd)
    assert "pytest tests/x.py" in p


# ── Body content sanity ────────────────────────────────────────────────


def test_mentions_guardrails(subtask_dataclass: Subtask) -> None:
    p = get_pfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dataclass,
    )
    # The prompt body should describe the two guardrails by name.
    assert "Pre-flight" in p or "pre-flight" in p.lower()
    assert "Flake-risk" in p or "flake" in p.lower()


def test_mentions_tool_grants(subtask_dataclass: Subtask) -> None:
    p = get_pfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dataclass,
    )
    for tool in ("Read", "Write", "Glob", "Grep"):
        assert tool in p
    # Negative: no Bash / Edit
    assert "Bash" in p  # mentioned in the "do NOT have" list
    assert "do NOT have" in p or "no bash" in p.lower()


def test_mentions_all_five_flake_patterns(subtask_dataclass: Subtask) -> None:
    p = get_pfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dataclass,
    )
    # The flake-lint patterns the agent must avoid.
    assert "dict" in p.lower()
    assert "set" in p.lower()
    assert "random" in p.lower()
    assert "time.sleep" in p
    assert "datetime.now" in p or "datetime.now()" in p


def test_lists_anti_patterns(subtask_dataclass: Subtask) -> None:
    p = get_pfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dataclass,
    )
    assert "Anti-patterns" in p or "anti-pattern" in p.lower()


# ── Size sanity ────────────────────────────────────────────────────────


def test_total_size_in_range(subtask_dataclass: Subtask) -> None:
    p = get_pfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dataclass,
    )
    # Body ~7KB + context block (~1KB). Combined: 5-15 KB.
    assert 5_000 < len(p) < 15_000, f"unexpected size {len(p)}"


# ── Failure mode ───────────────────────────────────────────────────────


def test_raises_when_md_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, subtask_dataclass: Subtask,
) -> None:
    """v0.2 path: when the generic gen_functional.md is missing, raise FileNotFoundError."""
    import prompts_pkg.prompts as mod

    class FakeDesc:
        name = "pytest"
        context_block = "pytest block"

    monkeypatch.setattr(mod, "PROMPTS_DIR", tmp_path)  # empty dir — generic md absent
    with pytest.raises(FileNotFoundError, match="gen_functional.md"):
        get_pfactory_gen_functional_prompt(
            Path("/ws"), Path("/p"), subtask_dataclass, framework_descriptor=FakeDesc(),
        )


def test_raises_when_legacy_md_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, subtask_dataclass: Subtask,
) -> None:
    """v0.1 legacy path: when gen_functional-v01-legacy.md is missing, raise FileNotFoundError."""
    import prompts_pkg.prompts as mod

    monkeypatch.setattr(mod, "PROMPTS_DIR", tmp_path)  # empty dir — legacy md absent
    import warnings
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        with pytest.raises(FileNotFoundError, match="gen_functional-v01-legacy.md"):
            get_pfactory_gen_functional_prompt(
                Path("/ws"), Path("/p"), subtask_dataclass,
            )


def test_handles_missing_files_to_create_gracefully(subtask_dict: dict) -> None:
    """If the planner emits a subtask with no files_to_create, we degrade
    to a "?" placeholder rather than crashing."""
    subtask_dict["files_to_create"] = []
    import warnings

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        p = get_pfactory_gen_functional_prompt(
            Path("/ws"), Path("/p"), subtask_dict,
        )
    # Falls back to "?"; agent will surface the issue rather than write
    # to a real-looking but wrong path.
    assert "write the file at:" in p


# ── v0.2: FRAMEWORK CONTEXT injection (Task 6 / #22) ────────────────────


class _PytestDesc:
    name = "pytest"
    context_block = "pytest context: use @pytest.fixture and assert statements"


class _JestDesc:
    name = "jest"
    context_block = "jest context: use describe/it blocks and expect().toBe()"


class _PlaywrightDesc:
    name = "playwright"
    context_block = (
        "playwright context: use page.getByRole selectors and auto-wait expectations"
    )


def test_helper_with_pytest_descriptor_includes_framework_context_block(
    subtask_dataclass: Subtask,
) -> None:
    """v0.2: pytest descriptor → FRAMEWORK CONTEXT (pytest) section in prompt."""
    p = get_pfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dataclass, framework_descriptor=_PytestDesc(),
    )
    assert "## FRAMEWORK CONTEXT (pytest)" in p
    assert "pytest context:" in p
    assert "@pytest.fixture" in p


def test_helper_with_jest_descriptor_includes_jest_context(
    subtask_dataclass: Subtask,
) -> None:
    """v0.2: jest descriptor → FRAMEWORK CONTEXT (jest) section in prompt."""
    p = get_pfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dataclass, framework_descriptor=_JestDesc(),
    )
    assert "## FRAMEWORK CONTEXT (jest)" in p
    assert "jest context:" in p
    assert "describe/it" in p


def test_helper_with_playwright_descriptor_includes_playwright_context(
    subtask_dataclass: Subtask,
) -> None:
    """v0.2: playwright descriptor → FRAMEWORK CONTEXT (playwright) section in prompt."""
    p = get_pfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dataclass, framework_descriptor=_PlaywrightDesc(),
    )
    assert "## FRAMEWORK CONTEXT (playwright)" in p
    assert "playwright context:" in p
    assert "page.getByRole" in p


def test_helper_without_descriptor_uses_legacy_prompt_with_warning(
    subtask_dataclass: Subtask,
) -> None:
    """v0.1 path: framework_descriptor=None → DeprecationWarning + legacy prompt body."""
    import warnings

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        p = get_pfactory_gen_functional_prompt(
            Path("/ws"), Path("/p"), subtask_dataclass,
        )
    depr = [x for x in w if issubclass(x.category, DeprecationWarning)]
    assert depr, "expected a DeprecationWarning when framework_descriptor is None"
    assert "v0.1 legacy" in str(depr[0].message).lower() or "v0.1" in str(depr[0].message)
    # Legacy prompt body contains pytest-specific wording
    assert "pytest" in p


def test_helper_with_descriptor_omits_legacy_prompt(
    subtask_dataclass: Subtask,
) -> None:
    """v0.2 path: when descriptor provided, the v0.1 legacy body is NOT in the prompt."""
    p = get_pfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dataclass, framework_descriptor=_PytestDesc(),
    )
    # The legacy prompt opens with "DEPRECATED: v0.1 legacy Gen-Functional prompt".
    # The generic prompt does NOT contain this string.
    assert "DEPRECATED" not in p
    assert "v0.1 legacy Gen-Functional prompt" not in p


def test_subtask_context_block_appears_first(subtask_dataclass: Subtask) -> None:
    """v0.2 assembly order: SUBTASK CONTEXT → FRAMEWORK CONTEXT → generic body."""
    p = get_pfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dataclass, framework_descriptor=_PytestDesc(),
    )
    subtask_idx = p.index("## SUBTASK CONTEXT")
    framework_idx = p.index("## FRAMEWORK CONTEXT")
    # Generic body starts with "# PFactory Gen-Functional — generic"
    body_idx = p.index("# PFactory Gen-Functional")
    assert subtask_idx < framework_idx < body_idx, (
        "prompt sections out of order: SUBTASK CONTEXT must precede "
        "FRAMEWORK CONTEXT which must precede the generic body"
    )


def test_intent_update_subtask_write_path_mentioned() -> None:
    """intent: update subtask — write-path in SUBTASK CONTEXT is the existing test file."""
    subtask = {
        "id": "t1",
        "description": "update login test",
        "lane": "browser",
        "target": "app/login.ts::LoginPage",
        "rationale": "AC#2",
        "intent": "update",
        "files_to_create": ["tests/e2e/login.spec.ts"],
        "verification": {"type": "command", "run": "npx playwright test tests/e2e/login.spec.ts"},
    }
    p = get_pfactory_gen_functional_prompt(
        Path("/ws/demo"), Path("/proj"), subtask, framework_descriptor=_PlaywrightDesc(),
    )
    assert "/ws/demo/tests/e2e/login.spec.ts" in p
    assert "update" in p  # intent is visible in SUBTASK CONTEXT


def test_language_and_framework_appear_in_subtask_context(
    subtask_dataclass: Subtask,
) -> None:
    """v0.2: SUBTASK CONTEXT includes language + framework fields."""
    subtask_dataclass.language = "typescript"
    subtask_dataclass.framework = "jest"
    p = get_pfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dataclass, framework_descriptor=_JestDesc(),
    )
    assert "typescript" in p
    assert "jest" in p.lower()


def test_helper_with_descriptor_size_in_range(subtask_dataclass: Subtask) -> None:
    """Combined prompt (v0.2) must be < 15KB to stay within comfortable context budget."""
    p = get_pfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dataclass, framework_descriptor=_PytestDesc(),
    )
    assert len(p) < 15_000, (
        f"prompt too large: {len(p)} bytes. "
        "Trim gen_functional.md or the context_block."
    )


def test_v02_prompt_includes_all_universal_anti_patterns(
    subtask_dataclass: Subtask,
) -> None:
    """v0.2 generic prompt body must list the universal anti-patterns."""
    p = get_pfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dataclass, framework_descriptor=_PytestDesc(),
    )
    assert "Anti-patterns" in p or "anti-pattern" in p.lower()
    # Universal guards present in the generic body
    assert "timeout" in p.lower()
    assert "replan" in p.lower() or "replan_request" in p.lower() or "test_plan.json" in p
