"""
Prompt Loading Utilities
========================

Functions for loading agent prompts from markdown files.
Supports dynamic prompt assembly based on project type for context optimization.
Supports Quick Mode for simplified prompts (~70% fewer tokens).
"""

import json
import os
import re
from pathlib import Path

from .project_context import (
    detect_project_capabilities,
    get_mcp_tools_for_project,
    load_project_index,
)

# Directory containing prompt files
# prompts/ is a sibling directory of prompts_pkg/, so go up one level first
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def get_planner_prompt(spec_dir: Path) -> str:
    """
    Load the planner agent prompt with spec path injected.
    The planner creates subtask-based implementation plans.

    Args:
        spec_dir: Directory containing the spec.md file

    Returns:
        The planner prompt content with spec path
    """
    # Quick Mode: Use simplified prompt (~70% fewer tokens)
    if os.environ.get("QUICK_MODE") == "true":
        quick_prompt_file = PROMPTS_DIR / "planner_quick.md"
        if quick_prompt_file.exists():
            prompt_file = quick_prompt_file
        else:
            prompt_file = PROMPTS_DIR / "planner.md"
    else:
        prompt_file = PROMPTS_DIR / "planner.md"

    if not prompt_file.exists():
        raise FileNotFoundError(
            f"Planner prompt not found at {prompt_file}\n"
            "Make sure the pfactory/prompts/planner.md file exists."
        )

    prompt = prompt_file.read_text()

    # Inject spec directory information at the beginning
    spec_context = f"""## SPEC LOCATION

Your spec file is located at: `{spec_dir}/spec.md`

🚨 CRITICAL FILE CREATION INSTRUCTIONS 🚨

You MUST use the Write tool to create these files in the spec directory:
- `{spec_dir}/test_plan.json` - Subtask-based implementation plan (USE WRITE TOOL!)
- `{spec_dir}/build-progress.txt` - Progress notes (USE WRITE TOOL!)
- `{spec_dir}/init.sh` - Environment setup script (USE WRITE TOOL!)

DO NOT just describe what these files should contain. You MUST actually call the Write tool
with the file path and complete content to create them.

The project root is the parent of pfactory/. Implement code in the project root, not in the spec directory.

---

"""
    return spec_context + prompt


def get_coding_prompt(spec_dir: Path) -> str:
    """
    Load the coding agent prompt with spec path injected.

    Args:
        spec_dir: Directory containing the spec.md and test_plan.json

    Returns:
        The coding agent prompt content with spec path
    """
    # Quick Mode: Use simplified prompt (~70% fewer tokens)
    if os.environ.get("QUICK_MODE") == "true":
        quick_prompt_file = PROMPTS_DIR / "coder_quick.md"
        if quick_prompt_file.exists():
            prompt_file = quick_prompt_file
        else:
            prompt_file = PROMPTS_DIR / "coder.md"
    else:
        prompt_file = PROMPTS_DIR / "coder.md"

    if not prompt_file.exists():
        raise FileNotFoundError(
            f"Coding prompt not found at {prompt_file}\n"
            "Make sure the pfactory/prompts/coder.md file exists."
        )

    prompt = prompt_file.read_text()

    spec_context = f"""## SPEC LOCATION

Your spec and progress files are located at:
- Spec: `{spec_dir}/spec.md`
- Implementation plan: `{spec_dir}/test_plan.json`
- Progress notes: `{spec_dir}/build-progress.txt`
- Recovery context: `{spec_dir}/memory/attempt_history.json`

The project root is the parent of pfactory/. All code goes in the project root, not in the spec directory.

---

"""

    # Check for recovery context (stuck subtasks, retry hints)
    recovery_context = _get_recovery_context(spec_dir)
    if recovery_context:
        spec_context += recovery_context

    # Check for human input file
    human_input_file = spec_dir / "HUMAN_INPUT.md"
    if human_input_file.exists():
        human_input = human_input_file.read_text().strip()
        if human_input:
            spec_context += f"""## HUMAN INPUT (READ THIS FIRST!)

The human has left you instructions. READ AND FOLLOW THESE CAREFULLY:

{human_input}

After addressing this input, you may delete or clear the HUMAN_INPUT.md file.

---

"""

    return spec_context + prompt


def _get_recovery_context(spec_dir: Path) -> str:
    """
    Get recovery context if there are failed attempts or stuck subtasks.

    Args:
        spec_dir: Spec directory containing memory/

    Returns:
        Recovery context string or empty string
    """
    import json

    attempt_history_file = spec_dir / "memory" / "attempt_history.json"

    if not attempt_history_file.exists():
        return ""

    try:
        with open(attempt_history_file) as f:
            history = json.load(f)

        # Check for stuck subtasks
        stuck_subtasks = history.get("stuck_subtasks", [])
        if stuck_subtasks:
            context = """## ⚠️ RECOVERY ALERT - STUCK SUBTASKS DETECTED

Some subtasks have been attempted multiple times without success. These subtasks need:
- A COMPLETELY DIFFERENT approach
- Possibly simpler implementation
- Or escalation to human if infeasible

Stuck subtasks:
"""
            for stuck in stuck_subtasks:
                context += f"- {stuck['subtask_id']}: {stuck['reason']} ({stuck['attempt_count']} attempts)\n"

            context += "\nBefore working on any subtask, check memory/attempt_history.json for previous attempts!\n\n---\n\n"
            return context

        # Check for subtasks with multiple attempts
        subtasks_with_retries = []
        for subtask_id, subtask_data in history.get("subtasks", {}).items():
            attempts = subtask_data.get("attempts", [])
            if len(attempts) > 1 and subtask_data.get("status") != "completed":
                subtasks_with_retries.append((subtask_id, len(attempts)))

        if subtasks_with_retries:
            context = """## ⚠️ RECOVERY CONTEXT - RETRY AWARENESS

Some subtasks have been attempted before. When working on these:
1. READ memory/attempt_history.json for the specific subtask
2. See what approaches were tried
3. Use a DIFFERENT approach

Subtasks with previous attempts:
"""
            for subtask_id, attempt_count in subtasks_with_retries:
                context += f"- {subtask_id}: {attempt_count} attempts\n"

            context += "\n---\n\n"
            return context

        return ""

    except (OSError, json.JSONDecodeError):
        return ""


def get_followup_planner_prompt(spec_dir: Path) -> str:
    """
    Load the follow-up planner agent prompt with spec path and key files injected.
    The follow-up planner adds new subtasks to an existing completed implementation plan.

    Args:
        spec_dir: Directory containing the completed spec and test_plan.json

    Returns:
        The follow-up planner prompt content with paths injected
    """
    prompt_file = PROMPTS_DIR / "followup_planner.md"

    if not prompt_file.exists():
        raise FileNotFoundError(
            f"Follow-up planner prompt not found at {prompt_file}\n"
            "Make sure the pfactory/prompts/followup_planner.md file exists."
        )

    prompt = prompt_file.read_text()

    # Inject spec directory information at the beginning
    spec_context = f"""## SPEC LOCATION (FOLLOW-UP MODE)

You are adding follow-up work to a **completed** spec.

**Key files in this spec directory:**
- Spec: `{spec_dir}/spec.md`
- Follow-up request: `{spec_dir}/FOLLOWUP_REQUEST.md` (READ THIS FIRST!)
- Implementation plan: `{spec_dir}/test_plan.json` (APPEND to this, don't replace)
- Progress notes: `{spec_dir}/build-progress.txt`
- Context: `{spec_dir}/context.json`
- Memory: `{spec_dir}/memory/`

**Important paths:**
- Spec directory: `{spec_dir}`
- Project root: Parent of pfactory/ (where code should be implemented)

**Your task:**
1. Read `{spec_dir}/FOLLOWUP_REQUEST.md` to understand what to add
2. Read `{spec_dir}/test_plan.json` to see existing phases/subtasks
3. ADD new phase(s) with pending subtasks to the existing plan
4. PRESERVE all existing subtasks and their statuses

---

"""
    return spec_context + prompt


def is_first_run(spec_dir: Path) -> bool:
    """
    Check if this is the first run (no valid implementation plan with subtasks exists yet).

    The spec runner may create a skeleton test_plan.json with empty phases.
    This function checks for actual phases with subtasks, not just file existence.

    Args:
        spec_dir: Directory containing spec files

    Returns:
        True if test_plan.json doesn't exist or has no subtasks
    """
    plan_file = spec_dir / "test_plan.json"

    if not plan_file.exists():
        return True

    try:
        with open(plan_file) as f:
            plan = json.load(f)

        # Check if there are any phases with subtasks
        phases = plan.get("phases", [])
        if not phases:
            return True

        # Check if any phase has subtasks
        total_subtasks = sum(len(phase.get("subtasks", [])) for phase in phases)
        return total_subtasks == 0
    except (OSError, json.JSONDecodeError):
        # If we can't read the file, treat as first run
        return True


def _load_prompt_file(filename: str) -> str:
    """
    Load a prompt file from the prompts directory.

    Args:
        filename: Relative path to prompt file (e.g., "qa_reviewer.md" or "mcp_tools/playwright_browser.md")

    Returns:
        Content of the prompt file

    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    prompt_file = PROMPTS_DIR / filename
    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_file}")
    return prompt_file.read_text()


def get_qa_reviewer_prompt(spec_dir: Path, project_dir: Path) -> str:
    """
    Load the QA reviewer prompt with project-specific MCP tools dynamically injected.

    This function:
    1. Loads the base QA reviewer prompt
    2. Detects project capabilities from project_index.json
    3. Injects only relevant MCP tool documentation (Electron, Puppeteer, DB, API)

    This saves context window by excluding irrelevant tool docs.
    For example, a CLI Python project won't get Electron validation docs.

    Args:
        spec_dir: Directory containing the spec files
        project_dir: Root directory of the project

    Returns:
        The QA reviewer prompt with project-specific tools injected
    """
    # Quick Mode: Use simplified prompt (~70% fewer tokens)
    # Note: Quick mode uses simplified prompt without MCP tool injection
    if os.environ.get("QUICK_MODE") == "true":
        quick_prompt_file = PROMPTS_DIR / "qa_reviewer_quick.md"
        if quick_prompt_file.exists():
            base_prompt = quick_prompt_file.read_text()
            # Add basic spec context and return (skip MCP tool injection for speed)
            spec_context = f"""## SPEC LOCATION

Your spec and progress files are located at:
- Spec: `{spec_dir}/spec.md`
- Implementation plan: `{spec_dir}/test_plan.json`
- QA report output: `{spec_dir}/qa_report.md`

The project root is: `{project_dir}`

---

"""
            return spec_context + base_prompt

    # Load base QA reviewer prompt (full mode with MCP tools)
    base_prompt = _load_prompt_file("qa_reviewer.md")

    # Load project index and detect capabilities
    project_index = load_project_index(project_dir)
    capabilities = detect_project_capabilities(project_index)

    # Get list of MCP tool doc files to include
    mcp_tool_files = get_mcp_tools_for_project(capabilities)

    # Load and assemble MCP tool sections
    mcp_sections = []
    for tool_file in mcp_tool_files:
        try:
            section = _load_prompt_file(tool_file)
            mcp_sections.append(section)
        except FileNotFoundError:
            # Skip missing files gracefully
            pass

    # Inject spec context at the beginning
    spec_context = f"""## SPEC LOCATION

Your spec and progress files are located at:
- Spec: `{spec_dir}/spec.md`
- Implementation plan: `{spec_dir}/test_plan.json`
- Progress notes: `{spec_dir}/build-progress.txt`
- QA report output: `{spec_dir}/qa_report.md`
- Fix request output: `{spec_dir}/QA_FIX_REQUEST.md`

The project root is: `{project_dir}`

---

## PROJECT CAPABILITIES DETECTED

"""

    # Add capability summary for transparency
    active_caps = [k for k, v in capabilities.items() if v]
    if active_caps:
        spec_context += "Based on project analysis, the following capabilities were detected:\n"
        for cap in active_caps:
            cap_name = cap.replace("is_", "").replace("has_", "").replace("_", " ").title()
            spec_context += f"- {cap_name}\n"
        spec_context += "\nRelevant validation tools have been included below.\n\n"
    else:
        spec_context += "No special project capabilities detected. Using standard validation.\n\n"

    spec_context += "---\n\n"

    # Find injection point in base prompt (after PHASE 4, before PHASE 5)
    injection_marker = "<!-- PROJECT-SPECIFIC VALIDATION TOOLS WILL BE INJECTED HERE -->"

    if mcp_sections and injection_marker in base_prompt:
        # Replace marker with actual MCP tool sections
        mcp_content = "\n\n---\n\n## PROJECT-SPECIFIC VALIDATION TOOLS\n\n"
        mcp_content += (
            "The following validation tools are available based on your project type:\n\n"
        )
        mcp_content += "\n\n---\n\n".join(mcp_sections)
        mcp_content += "\n\n---\n"

        # Replace the multi-line marker comment block
        marker_pattern = r"<!-- PROJECT-SPECIFIC VALIDATION TOOLS WILL BE INJECTED HERE -->.*?<!-- - API validation \(for projects with API endpoints\) -->"
        base_prompt = re.sub(marker_pattern, mcp_content, base_prompt, flags=re.DOTALL)
    elif mcp_sections:
        # Fallback: append at the end if marker not found
        base_prompt += "\n\n---\n\n## PROJECT-SPECIFIC VALIDATION TOOLS\n\n"
        base_prompt += "\n\n---\n\n".join(mcp_sections)

    return spec_context + base_prompt


def get_qa_fixer_prompt(spec_dir: Path, project_dir: Path) -> str:
    """
    Load the QA fixer prompt with spec paths injected.

    Args:
        spec_dir: Directory containing the spec files
        project_dir: Root directory of the project

    Returns:
        The QA fixer prompt content with paths injected
    """
    base_prompt = _load_prompt_file("qa_fixer.md")

    spec_context = f"""## SPEC LOCATION

Your spec and progress files are located at:
- Spec: `{spec_dir}/spec.md`
- Implementation plan: `{spec_dir}/test_plan.json`
- QA fix request: `{spec_dir}/QA_FIX_REQUEST.md` (READ THIS FIRST!)
- QA report: `{spec_dir}/qa_report.md`

The project root is: `{project_dir}`

---

"""
    return spec_context + base_prompt


# ---------------------------------------------------------------------------
# PFactory Planner prompt helpers — Task 5 (#6), commit 3 of 6.
#
# These are distinct from the inherited get_planner_prompt /
# get_followup_planner_prompt helpers above, which target AIFactory's
# coder-pipeline planner. The PFactory variants load the new
# test-oriented prompts in apps/backend/prompts/planner.md +
# planner_replan.md, then prepend a SPEC CONTEXT block with the
# concrete spec_dir + project_dir paths.
# ---------------------------------------------------------------------------


def _build_framework_registry_block() -> str:
    """Return a FRAMEWORK REGISTRY summary block for the planner prompt.

    Deferred import of framework_registry avoids circular imports at
    module level (the same pattern Task 4 uses for pfactory_yml).
    If the registry is unavailable (e.g. frameworks/ dir absent) the
    block is replaced by a minimal placeholder so the helper stays
    non-fatal — the validator in planner.py degrades gracefully too.

    Returns:
        Markdown string starting with '## FRAMEWORK REGISTRY'.
    """
    try:
        from framework_registry import load_registry  # deferred: not on hot path

        registry = load_registry()
        if not registry:
            return "## FRAMEWORK REGISTRY\n(no frameworks registered)\n"
        lines = ["## FRAMEWORK REGISTRY"]
        for name, desc in sorted(registry.items()):
            lane_vals = ", ".join(
                ln.value if hasattr(ln, "value") else str(ln) for ln in desc.lanes
            )
            lines.append(
                f"- {name}: language={desc.language},"
                f" lanes=[{lane_vals}],"
                f" image={desc.runtime.image}"
            )
        return "\n".join(lines) + "\n"
    except Exception:  # noqa: BLE001
        return (
            "## FRAMEWORK REGISTRY\n"
            "(registry unavailable — use pytest for Python, jest for TypeScript)\n"
        )


def _build_tests_catalog_block(spec_dir: Path) -> str:
    """Return a TESTS CATALOG summary block for the planner prompt.

    Reads the frozen ``context/tests_catalog.json`` written by Task 4's
    snapshotter (if present).  We load it directly from the JSON rather
    than calling ``load_catalog(repo_root)`` because the snapshotter
    already wrote a frozen copy scoped to this workspace.

    Returns:
        Markdown string starting with '## TESTS CATALOG'.
    """
    import json as _json

    catalog_path = spec_dir / "context" / "tests_catalog.json"
    if not catalog_path.exists():
        return (
            "## TESTS CATALOG\n(no catalog at this repo yet — every subtask uses intent: create)\n"
        )
    try:
        from tests_catalog import TestsCatalog  # deferred: not on hot path

        data = _json.loads(catalog_path.read_text())
        catalog = TestsCatalog.from_dict(data)
        if not catalog.tests:
            return (
                "## TESTS CATALOG\n"
                "(catalog present but empty — every subtask uses intent: create)\n"
            )
        lines = [
            "## TESTS CATALOG (existing tests in this repo)",
            "Existing entries (use intent: update if your AC matches an existing covers_acs):",
        ]
        for entry in catalog.tests:
            acs_preview = "; ".join(entry.covers_acs[:2])
            if len(entry.covers_acs) > 2:
                acs_preview += f" (+ {len(entry.covers_acs) - 2} more)"
            locked = " [operator_locked]" if entry.operator_locked else ""
            lines.append(
                f"- {entry.test_id}: framework={entry.framework},"
                f" lane={entry.lane},"
                f" test_file={entry.test_file},"
                f" covers_acs=[{acs_preview!r}]{locked}"
            )
        lines.append(f"(catalog has {len(catalog.tests)} entries total)")
        return "\n".join(lines) + "\n"
    except Exception:  # noqa: BLE001
        return "## TESTS CATALOG\n(catalog present but could not be parsed — treating as absent)\n"


def get_pfactory_planner_prompt(spec_dir: Path, project_dir: Path) -> str:
    """Assemble the initial-mode Planner prompt for PFactory.

    Loads apps/backend/prompts/planner.md and prepends a SPEC CONTEXT
    block that names the concrete paths the agent will read + write,
    followed by a FRAMEWORK REGISTRY block (Task 5, #21) summarising
    the available frameworks, and a TESTS CATALOG block (Task 5, #21)
    listing existing tests so the agent can set intent: update/skip.

    The template uses `{spec_dir}` / `{project_dir}` placeholders that
    survive to the agent verbatim — the agent uses the Read tool to
    fetch the files, so the placeholders in the prompt body are
    illustrative; the SPEC CONTEXT block we prepend here has the
    real paths.

    Args:
        spec_dir: Absolute path to the PFactory workspace spec dir.
        project_dir: Absolute path to the AIFactory project root.

    Returns:
        Full system prompt ready to hand to run_agent_session.

    Raises:
        FileNotFoundError: if planner.md is missing.
    """
    prompt_file = PROMPTS_DIR / "planner.md"
    if not prompt_file.exists():
        raise FileNotFoundError(
            f"planner.md missing at {prompt_file}. "
            "PFactory's Task 5 (#6) authors this prompt; check the working tree."
        )

    body = prompt_file.read_text()

    # Registry + catalog blocks (Task 5, #21). Deferred imports inside
    # helper functions avoid circular imports at module level.
    registry_block = _build_framework_registry_block()
    catalog_block = _build_tests_catalog_block(spec_dir)

    context = (
        "## SPEC CONTEXT (PFactory Planner — initial mode)\n\n"
        f"Concrete paths for this run:\n\n"
        f"- spec_dir: `{spec_dir}`\n"
        f"- project_dir: `{project_dir}` (read-only via Glob/Grep)\n\n"
        "Files Task 3's snapshotter already populated:\n\n"
        f"- `{spec_dir / 'context' / 'aifactory_spec.md'}`\n"
        f"- `{spec_dir / 'context' / 'aifactory_plan.json'}` (may not exist)\n"
        f"- `{spec_dir / 'context' / 'diff.patch'}` (may not exist if git skipped)\n"
        f"- `{spec_dir / 'context' / 'source.json'}` (snapshot warnings — read first)\n"
        f"- `{spec_dir / 'context' / 'pfactory_yml.json'}` (may not exist)\n"
        f"- `{spec_dir / 'context' / 'tests_catalog.json'}` (may not exist)\n\n"
        f"Emit `test_plan.json` via Write at: `{spec_dir / 'test_plan.json'}`\n\n"
        "---\n\n"
        f"{registry_block}\n"
        f"{catalog_block}\n"
        "---\n\n"
    )
    return context + body


def get_pfactory_planner_replan_prompt(spec_dir: Path, project_dir: Path) -> str:
    """Assemble the replan-mode Planner prompt for PFactory.

    Loads apps/backend/prompts/planner_replan.md and prepends a
    REPLAN CONTEXT block naming the spec_dir + project_dir.

    The replan_request.json file (read by the agent) is written by
    Gen-Functional (Task 6) before this helper is called; this
    function does NOT validate its presence — the agent surfaces a
    clear error if the file's missing.

    Args:
        spec_dir: Absolute path to the PFactory workspace spec dir.
        project_dir: Absolute path to the AIFactory project root.

    Returns:
        Full system prompt ready to hand to run_agent_session.

    Raises:
        FileNotFoundError: if planner_replan.md is missing.
    """
    prompt_file = PROMPTS_DIR / "planner_replan.md"
    if not prompt_file.exists():
        raise FileNotFoundError(
            f"planner_replan.md missing at {prompt_file}. "
            "PFactory's Task 5 (#6) authors this prompt; check the working tree."
        )

    body = prompt_file.read_text()
    context = (
        "## REPLAN CONTEXT (PFactory Planner — replan mode)\n\n"
        f"Concrete paths for this run:\n\n"
        f"- spec_dir: `{spec_dir}`\n"
        f"- project_dir: `{project_dir}` (read-only via Glob/Grep)\n\n"
        "Files you'll read:\n\n"
        f"- `{spec_dir / 'context' / 'replan_request.json'}` — **read first**\n"
        f"- `{spec_dir / 'test_plan.json'}` — the existing plan to append to\n"
        f"- `{spec_dir / 'context' / 'aifactory_spec.md'}` — original spec\n"
        f"- `{spec_dir / 'context' / 'diff.patch'}` — code surface (may be absent)\n\n"
        f"Write the updated plan back to: `{spec_dir / 'test_plan.json'}`\n\n"
        "---\n\n"
    )
    return context + body


def get_pfactory_gen_functional_prompt(
    spec_dir: Path,
    project_dir: Path,
    subtask,
    framework_descriptor=None,
) -> str:
    """Assemble the per-subtask Gen-Functional prompt — Task 6 (#22).

    v0.2: accepts an optional ``framework_descriptor`` (a
    ``FrameworkDescriptor`` instance) that triggers the generic prompt
    path.  When ``framework_descriptor`` is ``None`` the v0.1 legacy
    prompt path is used (with a ``DeprecationWarning``).

    **v0.2 prompt assembly order:**

    1. SUBTASK CONTEXT block — concrete paths + subtask fields
    2. FRAMEWORK CONTEXT block — injected from
       ``framework_descriptor.context_block``
    3. Generic prompt body from ``gen_functional.md``

    **v0.1 legacy path (framework_descriptor=None):**

    Loads ``gen_functional-v01-legacy.md`` and prepends the same SUBTASK
    CONTEXT block.  Issues a ``DeprecationWarning`` to guide migration.

    Args:
        spec_dir: PFactory workspace spec dir.
        project_dir: AIFactory project root path (Glob/Grep target).
        subtask: A Subtask dataclass instance from the loaded
            ImplementationPlan.  Duck-typed: also accepts a plain dict
            with the same keys (post-JSON-load shape).
        framework_descriptor: A ``FrameworkDescriptor`` instance from
            ``framework_registry.get_descriptor(subtask.framework)``.
            Pass ``None`` to use the v0.1 legacy prompt (warns).

    Returns:
        Full system prompt ready for ``run_agent_session``.

    Raises:
        FileNotFoundError: if the resolved prompt file is missing.
    """
    import warnings

    # Duck-typed accessor for dataclass-or-dict subtask shapes.
    def _get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    target = _get(subtask, "target", "?")
    rationale = _get(subtask, "rationale", "?")
    files_to_create = _get(subtask, "files_to_create", []) or []
    description = _get(subtask, "description", "?")
    subtask_id = _get(subtask, "id", "?")
    language = _get(subtask, "language", None) or "?"
    framework = _get(subtask, "framework", None) or "?"
    intent = _get(subtask, "intent", "create") or "create"

    # Verification carries the pytest/jest/playwright command for the Executor.
    # The planner prompt tells the LLM to emit ``"command"``; the Verification
    # dataclass uses ``run``. Both shapes are accepted here.
    verification = _get(subtask, "verification", None)
    if verification is None:
        verification_cmd = "?"
    elif isinstance(verification, dict):
        verification_cmd = verification.get("command") or verification.get("run") or "?"
    else:
        verification_cmd = (
            getattr(verification, "command", None) or getattr(verification, "run", None) or "?"
        )

    write_path = files_to_create[0] if files_to_create else "?"

    # SUBTASK CONTEXT block (shared by both the v0.1 and v0.2 paths).
    context = (
        "## SUBTASK CONTEXT (PFactory Gen-Functional)\n\n"
        f"Subtask: `{subtask_id}` — {description}\n\n"
        f"- target:            `{target}`\n"
        f"- rationale:         {rationale}\n"
        f"- language:          {language}\n"
        f"- framework:         {framework}\n"
        f"- intent:            {intent}\n"
        f"- write the file at: `{spec_dir / write_path}`\n"
        f"- verification:      `{verification_cmd}`\n\n"
        f"Concrete paths for this run:\n\n"
        f"- spec_dir:    `{spec_dir}`\n"
        f"- project_dir: `{project_dir}` (read-only via Glob/Grep)\n\n"
        "Snapshot files Task 3 populated:\n\n"
        f"- `{spec_dir / 'context' / 'aifactory_spec.md'}`\n"
        f"- `{spec_dir / 'context' / 'diff.patch'}` (may not exist)\n"
        f"- `{spec_dir / 'test_plan.json'}` (the full Planner output)\n\n"
        "---\n\n"
    )

    if framework_descriptor is None:
        # v0.1 legacy path — warn and load the legacy prompt.
        warnings.warn(
            "get_pfactory_gen_functional_prompt: framework_descriptor not provided; "
            "falling back to v0.1 legacy prompt path. "
            "Pass framework_descriptor; v0.1 legacy path will be removed in v0.3.",
            DeprecationWarning,
            stacklevel=2,
        )
        legacy_file = PROMPTS_DIR / "gen_functional-v01-legacy.md"
        if not legacy_file.exists():
            raise FileNotFoundError(
                f"gen_functional-v01-legacy.md missing at {legacy_file}. "
                "The v0.1 legacy prompt must be present for this fallback path."
            )
        body = legacy_file.read_text()
        return context + body

    # v0.2 generic path — inject FRAMEWORK CONTEXT then load the generic body.
    framework_name = getattr(framework_descriptor, "name", str(framework_descriptor))
    context_block_text = getattr(framework_descriptor, "context_block", "")
    framework_context = (
        f"## FRAMEWORK CONTEXT ({framework_name})\n\n{context_block_text}\n\n---\n\n"
    )

    prompt_file = PROMPTS_DIR / "gen_functional.md"
    if not prompt_file.exists():
        raise FileNotFoundError(
            f"gen_functional.md missing at {prompt_file}. "
            "PFactory's Task 6 (#22) commit 1 authors this prompt; check the working tree."
        )
    body = prompt_file.read_text()

    # Final assembly: SUBTASK CONTEXT → FRAMEWORK CONTEXT → generic body
    return context + framework_context + body


# ─── Task 7 (#8) — Evaluator helper ──────────────────────────────────────


def _format_signal_value(obj, *keys, default="?"):
    """Duck-typed accessor: try each key/attr in order; return the first
    non-None value. Lets the helper accept dataclass-shaped OR dict-shaped
    signal inputs (commit 5 will pass dataclasses; future callers may
    pass dicts post-JSON-load)."""
    for key in keys:
        if isinstance(obj, dict):
            val = obj.get(key)
        else:
            val = getattr(obj, key, None)
        if val is not None:
            return val
    return default


def _format_evaluator_per_test_block(bundle) -> str:
    """Render one per-test signal sub-block for the EVALUATOR CONTEXT.

    Duck-typed: ``bundle`` is either an EvaluatorSignals dataclass (commit
    5) or a dict with the same keys. Each field is formatted compactly —
    the LLM reads the prose body for the decision rules; this block is
    just the numeric handles.
    """
    test_id = _format_signal_value(bundle, "test_id")
    test_file = _format_signal_value(bundle, "test_file", "test_file_path")
    target = _format_signal_value(bundle, "target")
    rationale = _format_signal_value(bundle, "rationale")

    coverage = _format_signal_value(bundle, "coverage_delta", "coverage", default=None)
    if coverage is not None:
        # coverage is a CoverageDelta dataclass (or dict); render numeric signals.
        new_lines = _format_signal_value(coverage, "new_lines", default=frozenset())
        new_lines_count = len(new_lines) if hasattr(new_lines, "__len__") else 0
        delta_pct = _format_signal_value(coverage, "delta_pct", default=0.0)
        new_files = _format_signal_value(coverage, "new_files", default=0)
        coverage_line = (
            f"coverage: delta_pct={delta_pct:+.2f}, "
            f"new_lines={new_lines_count}, new_files={new_files}"
        )
    else:
        # coverage_delta is None — either the framework explicitly skips
        # coverage measurement (Browser lane / Playwright, Decision 11) or
        # the coverage XML was absent for this run.
        #
        # In both cases, render "N/A (browser lane)" so the Evaluator LLM
        # does NOT interpret this as "0% coverage" and issue a spurious
        # reject.  The evaluator.md verdict-priority section instructs the
        # LLM to skip the coverage rule when it sees "N/A".
        coverage_line = "coverage: N/A (browser lane)"

    stability = _format_signal_value(bundle, "stability", default=None)
    if stability is not None:
        verdict = _format_signal_value(stability, "verdict", default="?")
        # Verdict is an enum (StabilityVerdict) — `.value` is the string
        verdict_str = getattr(verdict, "value", verdict)
        rerun_count = _format_signal_value(stability, "rerun_count", default="?")
        stability_line = f"stability: {verdict_str} ({rerun_count} runs)"
    else:
        stability_line = "stability: not computed"

    mutation = _format_signal_value(bundle, "mutation", default=None)
    if mutation is not None:
        m_verdict = _format_signal_value(mutation, "verdict", default="?")
        m_verdict_str = getattr(m_verdict, "value", m_verdict)
        m_op = _format_signal_value(mutation, "mutation", default=None)
        op_str = _format_signal_value(m_op, "operator", default="?") if m_op else "?"
        mutation_line = f"mutation: {m_verdict_str} (op={op_str})"
    else:
        mutation_line = "mutation: not computed"

    lint = _format_signal_value(bundle, "lint_promotion", "promotion", default=None)
    if lint is not None:
        summary_fn = getattr(lint, "summary", None)
        if callable(summary_fn):
            lint_summary = summary_fn()
        else:
            lint_summary = (
                f"high={_format_signal_value(lint, 'high_count', default=0)}, "
                f"promoted={_format_signal_value(lint, 'promoted_count', default=0)}, "
                f"medium={_format_signal_value(lint, 'medium_count', default=0)}"
            )
        lint_line = f"lint_promotion: {lint_summary}"
    else:
        lint_line = "lint_promotion: not computed"

    # Cross-run flip-rate (#37): historical flakiness across separate
    # pipeline runs — a chronically flaky test should be flagged/rejected
    # even when this run's 3× stability happened to come up STABLE.
    flaky = _format_signal_value(bundle, "flaky_history", default=None)
    if flaky is not None and _format_signal_value(flaky, "runs", default=0):
        f_class = _format_signal_value(flaky, "classification", default="?")
        f_class_str = getattr(f_class, "value", f_class)
        f_rate = _format_signal_value(flaky, "flip_rate", default=0.0)
        f_runs = _format_signal_value(flaky, "runs", default=0)
        flaky_line = f"flaky_history: {f_class_str} (flip_rate={f_rate:.2f} over {f_runs} runs)"
    else:
        flaky_line = "flaky_history: no prior runs"

    return (
        f"### Test `{test_id}`\n\n"
        f"- file: `{test_file}`\n"
        f"- target: `{target}`\n"
        f"- rationale: {rationale}\n"
        f"- {coverage_line}\n"
        f"- {stability_line}\n"
        f"- {mutation_line}\n"
        f"- {lint_line}\n"
        f"- {flaky_line}\n"
    )


def get_pfactory_evaluator_prompt(
    spec_dir: Path,
    project_dir: Path,
    signal_bundles,
) -> str:
    """Assemble the Evaluator system prompt for PFactory (Task 7 / #8).

    Loads ``apps/backend/prompts/evaluator.md`` and prepends an
    EVALUATOR CONTEXT block that names the concrete paths AND emits
    one sub-block per generated test with the four pre-computed
    numeric signals (coverage / stability / mutation / lint_promotion).
    The fifth signal — semantic relevance — is the LLM's call.

    Args:
        spec_dir: PFactory workspace spec dir.
        project_dir: AIFactory project root (read-only for the agent).
        signal_bundles: Iterable of bundles (dataclass OR dict) carrying
            per-test signals. Each bundle exposes (as attribute OR key):
              - ``test_id``, ``test_file`` / ``test_file_path``
              - ``target``, ``rationale``
              - ``coverage_delta`` (or ``coverage``) — a CoverageDelta
              - ``stability`` — a StabilityResult
              - ``mutation`` — a MutationResult
              - ``lint_promotion`` (or ``promotion``) — a PromotionResult

            Any missing field is rendered as "?" or "not computed".

    Returns:
        Full system prompt ready to hand to ``run_agent_session``.

    Raises:
        FileNotFoundError: if evaluator.md is missing.
    """
    prompt_file = PROMPTS_DIR / "evaluator.md"
    if not prompt_file.exists():
        raise FileNotFoundError(
            f"evaluator.md missing at {prompt_file}. "
            "PFactory's Task 7 (#8) commit 4 authors this prompt; check the working tree."
        )

    body = prompt_file.read_text()

    bundles = list(signal_bundles)
    per_test_blocks = "\n".join(_format_evaluator_per_test_block(b) for b in bundles)
    if not per_test_blocks:
        per_test_blocks = "*(no tests in this batch — emit an empty verdicts array.)*\n"

    verdicts_path = spec_dir / "findings" / "verdicts.json"
    context = (
        "## EVALUATOR CONTEXT (PFactory Evaluator — Python)\n\n"
        f"Concrete paths for this run:\n\n"
        f"- spec_dir:    `{spec_dir}`\n"
        f"- project_dir: `{project_dir}` (read-only via Glob/Grep)\n"
        f"- write verdicts to: `{verdicts_path}`\n\n"
        f"Number of generated tests to evaluate: {len(bundles)}\n\n"
        "Files you can read:\n\n"
        f"- `{spec_dir / 'context' / 'aifactory_spec.md'}`\n"
        f"- `{spec_dir / 'context' / 'diff.patch'}` (may not exist)\n"
        f"- `{spec_dir / 'test_plan.json'}` (the full Planner output)\n"
        f"- each test file listed below (under `{spec_dir / 'tests'}`)\n\n"
        "## Pre-computed signals per test\n\n"
        f"{per_test_blocks}\n"
        "---\n\n"
    )
    return context + body
