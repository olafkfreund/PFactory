"""
Insight Extractor
=================

Automatically extracts structured insights from completed coding sessions.
Runs after each session to capture rich, actionable knowledge for Graphiti memory.

Uses the Claude Agent SDK (same as the rest of the system) for extraction.
Falls back to generic insights if extraction fails (never blocks the build).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Check for Claude SDK availability
try:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    ClaudeAgentOptions = None
    ClaudeSDKClient = None

from agents.output_envelope import OutputEnvelopeError, extract_json
from core.auth import ensure_claude_code_oauth_token, get_auth_token

# Default model for insight extraction (fast and cheap)
DEFAULT_EXTRACTION_MODEL = "claude-3-5-haiku-latest"

# Maximum diff size to send to the LLM (avoid context limits)
MAX_DIFF_CHARS = 15000

# Maximum attempt history entries to include
MAX_ATTEMPTS_TO_INCLUDE = 3


def is_extraction_enabled() -> bool:
    """Check if insight extraction is enabled."""
    # Extraction requires Claude SDK and authentication token
    if not SDK_AVAILABLE:
        return False
    if not get_auth_token():
        return False
    enabled_str = os.environ.get("INSIGHT_EXTRACTION_ENABLED", "true").lower()
    return enabled_str in ("true", "1", "yes")


def get_extraction_model() -> str:
    """Get the model to use for insight extraction."""
    return os.environ.get("INSIGHT_EXTRACTOR_MODEL", DEFAULT_EXTRACTION_MODEL)


# =============================================================================
# Git Helpers
# =============================================================================


def get_session_diff(
    project_dir: Path,
    commit_before: str | None,
    commit_after: str | None,
) -> str:
    """
    Get the git diff between two commits.

    Args:
        project_dir: Project root directory
        commit_before: Commit hash before session (or None)
        commit_after: Commit hash after session (or None)

    Returns:
        Diff text (truncated if too large)
    """
    if not commit_before or not commit_after:
        return "(No commits to diff)"

    if commit_before == commit_after:
        return "(No changes - same commit)"

    try:
        result = subprocess.run(
            ["git", "diff", commit_before, commit_after],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        diff = result.stdout

        if len(diff) > MAX_DIFF_CHARS:
            # Truncate and add note
            diff = (
                diff[:MAX_DIFF_CHARS] + f"\n\n... (truncated, {len(diff)} chars total)"
            )

        return diff if diff else "(Empty diff)"

    except subprocess.TimeoutExpired:
        logger.warning("Git diff timed out")
        return "(Git diff timed out)"
    except Exception as e:
        logger.warning(f"Failed to get git diff: {e}")
        return f"(Failed to get diff: {e})"


def get_changed_files(
    project_dir: Path,
    commit_before: str | None,
    commit_after: str | None,
) -> list[str]:
    """
    Get list of files changed between two commits.

    Args:
        project_dir: Project root directory
        commit_before: Commit hash before session
        commit_after: Commit hash after session

    Returns:
        List of changed file paths
    """
    if not commit_before or not commit_after or commit_before == commit_after:
        return []

    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", commit_before, commit_after],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
        return files

    except Exception as e:
        logger.warning(f"Failed to get changed files: {e}")
        return []


def get_commit_messages(
    project_dir: Path,
    commit_before: str | None,
    commit_after: str | None,
) -> str:
    """Get commit messages between two commits."""
    if not commit_before or not commit_after or commit_before == commit_after:
        return "(No commits)"

    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"{commit_before}..{commit_after}"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() if result.stdout.strip() else "(No commits)"

    except Exception as e:
        logger.warning(f"Failed to get commit messages: {e}")
        return f"(Failed: {e})"


# =============================================================================
# Input Gathering
# =============================================================================


def gather_extraction_inputs(
    spec_dir: Path,
    project_dir: Path,
    subtask_id: str,
    session_num: int,
    commit_before: str | None,
    commit_after: str | None,
    success: bool,
    recovery_manager: Any,
) -> dict:
    """
    Gather all inputs needed for insight extraction.

    Args:
        spec_dir: Spec directory
        project_dir: Project root
        subtask_id: The subtask that was worked on
        session_num: Session number
        commit_before: Commit before session
        commit_after: Commit after session
        success: Whether session succeeded
        recovery_manager: Recovery manager with attempt history

    Returns:
        Dict with all inputs for the extractor
    """
    # Get subtask description from implementation plan
    subtask_description = _get_subtask_description(spec_dir, subtask_id)

    # Get git diff
    diff = get_session_diff(project_dir, commit_before, commit_after)

    # Get changed files
    changed_files = get_changed_files(project_dir, commit_before, commit_after)

    # Get commit messages
    commit_messages = get_commit_messages(project_dir, commit_before, commit_after)

    # Get attempt history
    attempt_history = _get_attempt_history(recovery_manager, subtask_id)

    return {
        "subtask_id": subtask_id,
        "subtask_description": subtask_description,
        "session_num": session_num,
        "success": success,
        "diff": diff,
        "changed_files": changed_files,
        "commit_messages": commit_messages,
        "attempt_history": attempt_history,
    }


def _get_subtask_description(spec_dir: Path, subtask_id: str) -> str:
    """Get subtask description from implementation plan."""
    plan_file = spec_dir / "test_plan.json"
    if not plan_file.exists():
        return f"Subtask: {subtask_id}"

    try:
        with open(plan_file) as f:
            plan = json.load(f)

        # Search through phases for the subtask
        for phase in plan.get("phases", []):
            for subtask in phase.get("subtasks", []):
                if subtask.get("id") == subtask_id:
                    return subtask.get("description", f"Subtask: {subtask_id}")

        return f"Subtask: {subtask_id}"

    except Exception as e:
        logger.warning(f"Failed to load subtask description: {e}")
        return f"Subtask: {subtask_id}"


def _get_attempt_history(recovery_manager: Any, subtask_id: str) -> list[dict]:
    """Get previous attempt history for this subtask."""
    if not recovery_manager:
        return []

    try:
        history = recovery_manager.get_subtask_history(subtask_id)
        attempts = history.get("attempts", [])

        # Limit to recent attempts
        return attempts[-MAX_ATTEMPTS_TO_INCLUDE:]

    except Exception as e:
        logger.warning(f"Failed to get attempt history: {e}")
        return []


# =============================================================================
# LLM Extraction
# =============================================================================


def _build_extraction_prompt(inputs: dict) -> str:
    """Build the prompt for insight extraction."""
    prompt_file = Path(__file__).parent / "prompts" / "insight_extractor.md"

    if prompt_file.exists():
        base_prompt = prompt_file.read_text()
    else:
        # Fallback if prompt file missing
        base_prompt = """Extract structured insights from this coding session.
Output ONLY valid JSON with: file_insights, patterns_discovered, gotchas_discovered, approach_outcome, recommendations"""

    # Build session context
    session_context = f"""
---

## SESSION DATA

### Subtask
- **ID**: {inputs["subtask_id"]}
- **Description**: {inputs["subtask_description"]}
- **Session Number**: {inputs["session_num"]}
- **Outcome**: {"SUCCESS" if inputs["success"] else "FAILED"}

### Files Changed
{chr(10).join(f"- {f}" for f in inputs["changed_files"]) if inputs["changed_files"] else "(No files changed)"}

### Commit Messages
{inputs["commit_messages"]}

### Git Diff
```diff
{inputs["diff"]}
```

### Previous Attempts
{_format_attempt_history(inputs["attempt_history"])}

---

Now analyze this session and output ONLY the JSON object.
"""

    return base_prompt + session_context


def _format_attempt_history(attempts: list[dict]) -> str:
    """Format attempt history for the prompt."""
    if not attempts:
        return "(First attempt - no previous history)"

    lines = []
    for i, attempt in enumerate(attempts, 1):
        success = "SUCCESS" if attempt.get("success") else "FAILED"
        approach = attempt.get("approach", "Unknown approach")
        error = attempt.get("error", "")
        lines.append(f"**Attempt {i}** ({success}): {approach}")
        if error:
            lines.append(f"  Error: {error}")

    return "\n".join(lines)


async def run_insight_extraction(
    inputs: dict, project_dir: Path | None = None
) -> dict | None:
    """
    Run the insight extraction using Claude Agent SDK.

    Args:
        inputs: Gathered session inputs
        project_dir: Project directory for SDK context (optional)

    Returns:
        Extracted insights dict or None if failed
    """
    if not SDK_AVAILABLE:
        logger.warning("Claude SDK not available, skipping insight extraction")
        return None

    if not get_auth_token():
        logger.warning("No authentication token found, skipping insight extraction")
        return None

    # Ensure SDK can find the token
    ensure_claude_code_oauth_token()

    model = get_extraction_model()
    prompt = _build_extraction_prompt(inputs)

    # Use current directory if project_dir not specified
    cwd = str(project_dir.resolve()) if project_dir else os.getcwd()

    try:
        # Use simple_client for insight extraction
        from pathlib import Path

        from core.simple_client import create_simple_client

        client = create_simple_client(
            agent_type="insights",
            model=model,
            system_prompt=(
                "You are an expert code analyst. You extract structured insights from coding sessions. "
                "Always respond with valid JSON only, no markdown formatting or explanations."
            ),
            cwd=Path(cwd) if cwd else None,
        )

        # Use async context manager
        async with client:
            await client.query(prompt)

            # Collect the response
            response_text = ""
            message_count = 0
            async for msg in client.receive_response():
                message_count += 1
                msg_type = type(msg).__name__
                if msg_type == "AssistantMessage" and hasattr(msg, "content"):
                    for block in msg.content:
                        if hasattr(block, "text"):
                            response_text += block.text

        # Validate we got a response before parsing
        if not response_text:
            logger.warning(
                f"SDK returned empty response for insight extraction "
                f"(received {message_count} messages)"
            )
            return None

        # Parse JSON from response
        return parse_insights(response_text)

    except Exception as e:
        logger.warning(f"Insight extraction failed: {e}")
        return None


def parse_insights(response_text: str) -> dict | None:
    """
    Parse the LLM response into structured insights.

    Args:
        response_text: Raw LLM response

    Returns:
        Parsed insights dict or None if parsing failed
    """
    # Extract JSON from the response via the shared tolerant envelope (#96):
    # handles empty output, ```json fences, trailing prose and brace-matching
    # in one place, so this call site no longer hand-rolls its own salvage.
    try:
        insights, salvaged = extract_json(response_text)
    except OutputEnvelopeError as exc:
        logger.warning(
            f"Failed to parse insights JSON ({exc}). "
            f"Response length: {len(response_text or '')} chars"
        )
        if response_text:
            logger.debug(f"Response text preview: {response_text.strip()[:200]}...")
        return None

    if salvaged:
        logger.debug("Extracted insights JSON via the tolerant envelope fallback")

    # Validate structure
    if not isinstance(insights, dict):
        logger.warning("Insights is not a dict")
        return None

    # Ensure required keys exist with defaults
    insights.setdefault("file_insights", [])
    insights.setdefault("patterns_discovered", [])
    insights.setdefault("gotchas_discovered", [])
    insights.setdefault("approach_outcome", {})
    insights.setdefault("recommendations", [])

    return insights


# =============================================================================
# Main Entry Point
# =============================================================================


async def extract_session_insights(
    spec_dir: Path,
    project_dir: Path,
    subtask_id: str,
    session_num: int,
    commit_before: str | None,
    commit_after: str | None,
    success: bool,
    recovery_manager: Any,
) -> dict:
    """
    Extract insights from a completed coding session.

    This is the main entry point called from post_session_processing().
    Falls back to generic insights if extraction fails.

    Args:
        spec_dir: Spec directory
        project_dir: Project root
        subtask_id: Subtask that was worked on
        session_num: Session number
        commit_before: Commit before session
        commit_after: Commit after session
        success: Whether session succeeded
        recovery_manager: Recovery manager with attempt history

    Returns:
        Insights dict (rich if extraction succeeded, generic if failed)
    """
    # Check if extraction is enabled
    if not is_extraction_enabled():
        logger.info("Insight extraction disabled")
        return _get_generic_insights(subtask_id, success)

    # Check for no changes
    if commit_before == commit_after:
        logger.info("No changes to extract insights from")
        return _get_generic_insights(subtask_id, success)

    try:
        # Gather inputs
        inputs = gather_extraction_inputs(
            spec_dir=spec_dir,
            project_dir=project_dir,
            subtask_id=subtask_id,
            session_num=session_num,
            commit_before=commit_before,
            commit_after=commit_after,
            success=success,
            recovery_manager=recovery_manager,
        )

        # Run extraction
        extracted = await run_insight_extraction(inputs, project_dir=project_dir)

        if extracted:
            # Add metadata
            extracted["subtask_id"] = subtask_id
            extracted["session_num"] = session_num
            extracted["success"] = success
            extracted["changed_files"] = inputs["changed_files"]

            logger.info(
                f"Extracted insights: {len(extracted.get('file_insights', []))} file insights, "
                f"{len(extracted.get('patterns_discovered', []))} patterns, "
                f"{len(extracted.get('gotchas_discovered', []))} gotchas"
            )
            return extracted
        else:
            logger.warning("Extraction returned no results, using generic insights")
            return _get_generic_insights(subtask_id, success)

    except Exception as e:
        logger.warning(f"Insight extraction failed: {e}, using generic insights")
        return _get_generic_insights(subtask_id, success)


def _get_generic_insights(subtask_id: str, success: bool) -> dict:
    """Return generic insights when extraction fails or is disabled."""
    return {
        "file_insights": [],
        "patterns_discovered": [],
        "gotchas_discovered": [],
        "approach_outcome": {
            "success": success,
            "approach_used": f"Implemented subtask: {subtask_id}",
            "why_it_worked": None,
            "why_it_failed": None,
            "alternatives_tried": [],
        },
        "recommendations": [],
        "subtask_id": subtask_id,
        "success": success,
        "changed_files": [],
    }


# =============================================================================
# Bulk extraction via Anthropic Message Batches API (Issue #11)
# =============================================================================
#
# This entry point is **opt-in** and currently has NO production callers.
# It ships as a primitive for a future end-of-build "insight sweep" worker
# that defers insight extraction off the per-session critical path and
# batches N completed-subtask extractions into one Message Batches call
# for the 50% batch-tier discount.
#
# Today's per-subtask call site (agents/session.py) continues to use the
# sequential `extract_session_insights()` path unchanged.


def _bulk_min_jobs() -> int:
    """Minimum number of completions before we engage the batch path."""
    try:
        return int(os.environ.get("PFACTORY_BATCH_MIN_JOBS", "2"))
    except ValueError:
        return 2


def _bulk_timeout() -> float:
    """Hard timeout for the batch poll loop, seconds."""
    try:
        return float(os.environ.get("PFACTORY_BATCH_TIMEOUT", "120"))
    except ValueError:
        return 120.0


def _bulk_disabled() -> bool:
    """Kill-switch — force the legacy sequential path even for ≥N jobs."""
    return os.environ.get("PFACTORY_BATCH_DISABLE", "").lower() in ("1", "true", "yes")


async def extract_session_insights_bulk(
    completions: list[dict[str, Any]],
    *,
    project_dir: Path,
    spec_dir: Path,
    api_key: str | None = None,
) -> dict[str, dict]:
    """Extract insights for multiple completed subtasks in one Anthropic batch.

    Each entry in ``completions`` is a dict matching the signature of
    ``extract_session_insights``:

        {
          "subtask_id": str,
          "session_num": int,
          "commit_before": str | None,
          "commit_after": str | None,
          "success": bool,
          "recovery_manager": Any,
        }

    Returns ``{subtask_id: insights_dict}`` covering every entry. Failures
    (batch errored / timeout / no API key) fall back per-entry to the
    existing ``_get_generic_insights`` so the caller's contract matches the
    per-session ``extract_session_insights`` function.

    Caller must set ``ANTHROPIC_API_KEY`` (or pass ``api_key``) — the Claude
    Agent SDK uses ``CLAUDE_CODE_OAUTH_TOKEN``; the raw client batch API
    needs a separate key. If neither is available, every entry falls back
    to generic insights.

    Engages the batch path only when ``len(completions) >= PFACTORY_BATCH_MIN_JOBS``
    (default 2) and ``PFACTORY_BATCH_DISABLE`` is unset.

    NOTE: This is a primitive shipped as part of Issue #11 — it has no
    production caller yet. The first real consumer will likely be an
    end-of-build insight sweep worker.
    """
    if not completions:
        return {}

    # Below threshold or kill-switched → fall back to per-entry sequential path.
    if len(completions) < _bulk_min_jobs() or _bulk_disabled():
        logger.debug(
            "bulk extraction skipping batch path (n=%d, threshold=%d, disabled=%s)",
            len(completions),
            _bulk_min_jobs(),
            _bulk_disabled(),
        )
        results: dict[str, dict] = {}
        for c in completions:
            results[c["subtask_id"]] = await extract_session_insights(
                spec_dir=spec_dir,
                project_dir=project_dir,
                subtask_id=c["subtask_id"],
                session_num=c["session_num"],
                commit_before=c.get("commit_before"),
                commit_after=c.get("commit_after"),
                success=c["success"],
                recovery_manager=c.get("recovery_manager"),
            )
        return results

    # Lazy import — keeps the module loadable when anthropic isn't installed.
    try:
        from core.batch import (
            BatchRequest,
            await_batch,
            extract_savings,
            submit_batch,
        )
    except ImportError as exc:
        logger.warning(
            "core.batch unavailable (%s); falling back to per-entry sequential extraction",
            exc,
        )
        return await _bulk_sequential_fallback(
            completions, project_dir=project_dir, spec_dir=spec_dir
        )

    # Build N BatchRequest entries. Each one gathers the same per-subtask
    # inputs the sequential path does and embeds them inline in the prompt
    # (the extraction prompt is fully self-contained text — no tool loop).
    model = get_extraction_model()
    system_prompt = (
        "You are an expert code analyst. You extract structured insights from "
        "coding sessions. Always respond with valid JSON only, no markdown "
        "formatting or explanations."
    )
    requests: list[BatchRequest] = []
    entries_by_id: dict[str, dict[str, Any]] = {}

    for c in completions:
        subtask_id = c["subtask_id"]
        # If a per-entry pre-flight check fails, skip batching this one;
        # we'll resolve it from the generic fallback in the result merge.
        if c.get("commit_before") == c.get("commit_after"):
            entries_by_id[subtask_id] = {"skip_reason": "no_changes"}
            continue
        try:
            inputs = gather_extraction_inputs(
                spec_dir=spec_dir,
                project_dir=project_dir,
                subtask_id=subtask_id,
                session_num=c["session_num"],
                commit_before=c.get("commit_before"),
                commit_after=c.get("commit_after"),
                success=c["success"],
                recovery_manager=c.get("recovery_manager"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "gather_extraction_inputs failed for %s: %s", subtask_id, exc
            )
            entries_by_id[subtask_id] = {"skip_reason": "gather_failed"}
            continue

        prompt = _build_extraction_prompt(inputs)
        requests.append(
            BatchRequest(
                custom_id=subtask_id,
                model=model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
                system=system_prompt,
            )
        )
        entries_by_id[subtask_id] = {"inputs": inputs, "success": c["success"]}

    # If nothing was batchable, return generic insights for every entry.
    if not requests:
        return {
            c["subtask_id"]: _get_generic_insights(c["subtask_id"], c["success"])
            for c in completions
        }

    # Submit + poll.
    try:
        batch_id = await submit_batch(requests, api_key=api_key)
        logger.info(
            "Bulk extraction batch submitted: %s (%d requests)", batch_id, len(requests)
        )
        batch_results = await await_batch(
            batch_id, api_key=api_key, timeout=_bulk_timeout()
        )
    except RuntimeError as exc:  # missing API key
        logger.warning("Batch path unavailable: %s — falling back to sequential", exc)
        return await _bulk_sequential_fallback(
            completions, project_dir=project_dir, spec_dir=spec_dir
        )
    except TimeoutError:
        logger.warning(
            "Batch %s did not complete within %.0fs — falling back to sequential",
            batch_id,
            _bulk_timeout(),
        )
        return await _bulk_sequential_fallback(
            completions, project_dir=project_dir, spec_dir=spec_dir
        )

    savings = extract_savings(batch_results)
    logger.info(
        "Batch %s ended: succeeded=%d errored=%d service_tiers=%s saving=%.0f%%",
        batch_id,
        savings["succeeded"],
        savings["errored"],
        savings["service_tiers"],
        savings["estimated_saving_pct"] * 100,
    )

    # Merge: parse each batch result; per-entry failures → generic fallback.
    out: dict[str, dict] = {}
    for r in batch_results:
        entry = entries_by_id.get(r.custom_id)
        success = bool(entry and entry.get("success", True))

        if r.status == "succeeded" and r.content:
            parsed = parse_insights(r.content)
            if parsed:
                if entry and "inputs" in entry:
                    parsed["subtask_id"] = r.custom_id
                    parsed["session_num"] = entry["inputs"].get("session_num", 0)
                    parsed["success"] = success
                    parsed["changed_files"] = entry["inputs"].get("changed_files", [])
                out[r.custom_id] = parsed
                continue
            logger.warning(
                "Batch entry %s: succeeded but parse_insights returned None",
                r.custom_id,
            )

        # Errored, expired, canceled, or unparseable → generic fallback.
        out[r.custom_id] = _get_generic_insights(r.custom_id, success)
        if r.status == "errored":
            logger.warning("Batch entry %s errored: %s", r.custom_id, r.error)

    # Handle entries that were pre-skipped (no changes / gather failure).
    for c in completions:
        if c["subtask_id"] not in out:
            out[c["subtask_id"]] = _get_generic_insights(c["subtask_id"], c["success"])

    return out


async def _bulk_sequential_fallback(
    completions: list[dict[str, Any]],
    *,
    project_dir: Path,
    spec_dir: Path,
) -> dict[str, dict]:
    """Fallback path that runs the legacy sequential extractor for each entry."""
    results: dict[str, dict] = {}
    for c in completions:
        results[c["subtask_id"]] = await extract_session_insights(
            spec_dir=spec_dir,
            project_dir=project_dir,
            subtask_id=c["subtask_id"],
            session_num=c["session_num"],
            commit_before=c.get("commit_before"),
            commit_after=c.get("commit_after"),
            success=c["success"],
            recovery_manager=c.get("recovery_manager"),
        )
    return results


# =============================================================================
# CLI for Testing
# =============================================================================

if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Test insight extraction")
    parser.add_argument("--spec-dir", type=Path, required=True, help="Spec directory")
    parser.add_argument(
        "--project-dir", type=Path, required=True, help="Project directory"
    )
    parser.add_argument(
        "--commit-before", type=str, required=True, help="Commit before session"
    )
    parser.add_argument(
        "--commit-after", type=str, required=True, help="Commit after session"
    )
    parser.add_argument(
        "--subtask-id", type=str, default="test-subtask", help="Subtask ID"
    )

    args = parser.parse_args()

    async def main():
        insights = await extract_session_insights(
            spec_dir=args.spec_dir,
            project_dir=args.project_dir,
            subtask_id=args.subtask_id,
            session_num=1,
            commit_before=args.commit_before,
            commit_after=args.commit_after,
            success=True,
            recovery_manager=None,
        )
        print(json.dumps(insights, indent=2))

    asyncio.run(main())
