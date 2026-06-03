"""
Agent Session Management
========================

Handles running agent sessions and post-session processing including
memory updates and recovery tracking.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import ClaudeSDKClient
from core.error_utils import (
    is_authentication_error,
    is_rate_limit_error,
    is_tool_concurrency_error,
    safe_receive_messages,
)
from debug import debug, debug_detailed, debug_error, debug_section, debug_success
from insight_extractor import extract_session_insights
from progress import (
    count_subtasks_detailed,
    is_build_complete,
)
from recovery import RecoveryManager
from security.tool_input_validator import get_safe_tool_input
from task_logger import (
    LogEntryType,
    LogPhase,
    get_task_logger,
)
from ui import (
    StatusManager,
    muted,
    print_key_value,
    print_status,
)

from .base import sanitize_error_message
from .memory_manager import save_session_memory
from .utils import (
    find_subtask_in_plan,
    get_commit_count,
    get_latest_commit,
    load_test_plan,
    sync_plan_to_source,
)

logger = logging.getLogger(__name__)


def _append_build_progress(
    spec_dir: Path, subtask_id: str, subtask: dict, session_num: int, commit_hash: str | None
) -> None:
    """Append a completed subtask line to build-progress.txt."""
    progress_file = spec_dir / "build-progress.txt"
    desc = subtask.get("title", subtask.get("description", subtask_id))
    commit_ref = f" ({commit_hash[:8]})" if commit_hash else ""
    line = f"  [x] {subtask_id}: {desc}{commit_ref}\n"
    try:
        with open(progress_file, "a") as f:
            f.write(line)
    except OSError:
        pass


async def post_session_processing(
    spec_dir: Path,
    project_dir: Path,
    subtask_id: str,
    session_num: int,
    commit_before: str | None,
    commit_count_before: int,
    recovery_manager: RecoveryManager,
    status_manager: StatusManager | None = None,
    source_spec_dir: Path | None = None,
) -> bool:
    """
    Process session results and update memory automatically.

    This runs in Python (100% reliable) instead of relying on agent compliance.

    Args:
        spec_dir: Spec directory containing memory/
        project_dir: Project root for git operations
        subtask_id: The subtask that was being worked on
        session_num: Current session number
        commit_before: Git commit hash before session
        commit_count_before: Number of commits before session
        recovery_manager: Recovery manager instance
        status_manager: Optional status manager for ccstatusline
        source_spec_dir: Original spec directory (for syncing back from worktree)

    Returns:
        True if subtask was completed successfully
    """
    print()
    print(muted("--- Post-Session Processing ---"))

    # Sync implementation plan back to source (for worktree mode)
    if sync_plan_to_source(spec_dir, source_spec_dir):
        print_status("Implementation plan synced to main project", "success")

    # Check if implementation plan was updated
    plan = load_test_plan(spec_dir)
    if not plan:
        print("  Warning: Could not load implementation plan")
        return False

    subtask = find_subtask_in_plan(plan, subtask_id)
    if not subtask:
        print(f"  Warning: Subtask {subtask_id} not found in plan")
        return False

    subtask_status = subtask.get("status", "pending")

    # Check for new commits
    commit_after = get_latest_commit(project_dir)
    commit_count_after = get_commit_count(project_dir)
    new_commits = commit_count_after - commit_count_before

    print_key_value("Subtask status", subtask_status)
    print_key_value("New commits", str(new_commits))

    # Fallback: if agent didn't update status but made commits, force-mark as completed
    if subtask_status == "pending" and new_commits > 0:
        print_status(
            f"Agent didn't update status but made {new_commits} commit(s) — marking completed",
            "info",
        )
        subtask["status"] = "completed"
        subtask["updated_at"] = datetime.now(timezone.utc).isoformat()
        subtask["notes"] = f"Auto-completed: {new_commits} commit(s) detected"
        plan_file = spec_dir / "test_plan.json"
        plan["last_updated"] = datetime.now(timezone.utc).isoformat()
        with open(plan_file, "w") as f:
            json.dump(plan, f, indent=2)
        sync_plan_to_source(spec_dir, source_spec_dir)
        subtask_status = "completed"

    if subtask_status == "completed":
        # Success! Record the attempt and good commit
        print_status(f"Subtask {subtask_id} completed successfully", "success")

        # Update status file
        if status_manager:
            subtasks = count_subtasks_detailed(spec_dir)
            status_manager.update_subtasks(
                completed=subtasks["completed"],
                total=subtasks["total"],
                in_progress=0,
            )

        # Record successful attempt
        recovery_manager.record_attempt(
            subtask_id=subtask_id,
            session=session_num,
            success=True,
            approach=f"Implemented: {subtask.get('description', 'subtask')[:100]}",
        )

        # Record good commit for rollback safety
        if commit_after and commit_after != commit_before:
            recovery_manager.record_good_commit(commit_after, subtask_id)
            print_status(f"Recorded good commit: {commit_after[:8]}", "success")

        # Extract rich insights from session (LLM-powered analysis)
        try:
            extracted_insights = await extract_session_insights(
                spec_dir=spec_dir,
                project_dir=project_dir,
                subtask_id=subtask_id,
                session_num=session_num,
                commit_before=commit_before,
                commit_after=commit_after,
                success=True,
                recovery_manager=recovery_manager,
            )
            insight_count = len(extracted_insights.get("file_insights", []))
            pattern_count = len(extracted_insights.get("patterns_discovered", []))
            if insight_count > 0 or pattern_count > 0:
                print_status(
                    f"Extracted {insight_count} file insights, {pattern_count} patterns",
                    "success",
                )
        except Exception as e:
            logger.warning(f"Insight extraction failed: {e}")
            extracted_insights = None

        # Save session memory (Graphiti=primary, file-based=fallback)
        try:
            save_success, storage_type = await save_session_memory(
                spec_dir=spec_dir,
                project_dir=project_dir,
                subtask_id=subtask_id,
                session_num=session_num,
                success=True,
                subtasks_completed=[subtask_id],
                discoveries=extracted_insights,
            )
            if save_success:
                if storage_type == "graphiti":
                    print_status("Session saved to Graphiti memory", "success")
                else:
                    print_status(
                        "Session saved to file-based memory (fallback)", "info"
                    )
            else:
                print_status("Failed to save session memory", "warning")
        except Exception as e:
            logger.warning(f"Error saving session memory: {e}")
            print_status("Memory save failed", "warning")

        # Append to build-progress.txt for frontend visibility
        _append_build_progress(spec_dir, subtask_id, subtask, session_num, commit_after)
        if source_spec_dir:
            _append_build_progress(source_spec_dir, subtask_id, subtask, session_num, commit_after)

        return True

    elif subtask_status == "in_progress":
        # Session ended without completion
        print_status(f"Subtask {subtask_id} still in progress", "warning")

        recovery_manager.record_attempt(
            subtask_id=subtask_id,
            session=session_num,
            success=False,
            approach="Session ended with subtask in_progress",
            error="Subtask not marked as completed",
        )

        # Still record commit if one was made (partial progress)
        if commit_after and commit_after != commit_before:
            recovery_manager.record_good_commit(commit_after, subtask_id)
            print_status(
                f"Recorded partial progress commit: {commit_after[:8]}", "info"
            )

        # Extract insights even from failed sessions (valuable for future attempts)
        try:
            extracted_insights = await extract_session_insights(
                spec_dir=spec_dir,
                project_dir=project_dir,
                subtask_id=subtask_id,
                session_num=session_num,
                commit_before=commit_before,
                commit_after=commit_after,
                success=False,
                recovery_manager=recovery_manager,
            )
        except Exception as e:
            logger.debug(f"Insight extraction failed for incomplete session: {e}")
            extracted_insights = None

        # Save failed session memory (to track what didn't work)
        try:
            await save_session_memory(
                spec_dir=spec_dir,
                project_dir=project_dir,
                subtask_id=subtask_id,
                session_num=session_num,
                success=False,
                subtasks_completed=[],
                discoveries=extracted_insights,
            )
        except Exception as e:
            logger.debug(f"Failed to save incomplete session memory: {e}")

        return False

    else:
        # Subtask still pending or failed
        print_status(
            f"Subtask {subtask_id} not completed (status: {subtask_status})", "error"
        )

        recovery_manager.record_attempt(
            subtask_id=subtask_id,
            session=session_num,
            success=False,
            approach="Session ended without progress",
            error=f"Subtask status is {subtask_status}",
        )

        # Extract insights even from completely failed sessions
        try:
            extracted_insights = await extract_session_insights(
                spec_dir=spec_dir,
                project_dir=project_dir,
                subtask_id=subtask_id,
                session_num=session_num,
                commit_before=commit_before,
                commit_after=commit_after,
                success=False,
                recovery_manager=recovery_manager,
            )
        except Exception as e:
            logger.debug(f"Insight extraction failed for failed session: {e}")
            extracted_insights = None

        # Save failed session memory (to track what didn't work)
        try:
            await save_session_memory(
                spec_dir=spec_dir,
                project_dir=project_dir,
                subtask_id=subtask_id,
                session_num=session_num,
                success=False,
                subtasks_completed=[],
                discoveries=extracted_insights,
            )
        except Exception as e:
            logger.debug(f"Failed to save failed session memory: {e}")

        return False


async def run_agent_session(
    client: ClaudeSDKClient,
    message: str,
    spec_dir: Path,
    verbose: bool = False,
    phase: LogPhase = LogPhase.CODING,
) -> tuple[str, str, dict]:
    """
    Run a single agent session using Claude Agent SDK.

    Args:
        client: Claude SDK client
        message: The prompt to send
        spec_dir: Spec directory path
        verbose: Whether to show detailed output
        phase: Current execution phase for logging

    Returns:
        (status, response_text, error_info) where:
        - status: "continue", "complete", or "error"
        - response_text: Agent's response text
        - error_info: Dict with error details (empty if no error):
            - "type": "tool_concurrency", "rate_limit", "authentication", or "other"
            - "message": Sanitized error message string
            - "exception_type": Exception class name string
    """
    debug_section("session", f"Agent Session - {phase.value}")
    debug(
        "session",
        "Starting agent session",
        spec_dir=str(spec_dir),
        phase=phase.value,
        prompt_length=len(message),
        prompt_preview=message[:200] + "..." if len(message) > 200 else message,
    )
    print("Sending prompt to Claude Agent SDK...\n")

    # Get task logger for this spec
    task_logger = get_task_logger(spec_dir)
    current_tool = None
    message_count = 0
    tool_count = 0

    try:
        # Send the query
        debug("session", "Sending query to Claude SDK...")
        await client.query(message)
        debug_success("session", "Query sent successfully")

        # Collect response text and show tool use
        response_text = ""
        # Running totals for prompt-cache usage — emitted as a single
        # logger.info at session end (per-turn lines are debug-level below).
        _cache_read_total = 0
        _cache_write_total = 0
        _last_session_id: str | None = None
        debug("session", "Starting to receive response stream...")
        async for msg in safe_receive_messages(client, caller="session"):
            msg_type = type(msg).__name__
            message_count += 1
            debug_detailed(
                "session",
                f"Received message #{message_count}",
                msg_type=msg_type,
            )

            # Handle AssistantMessage (text and tool use)
            if msg_type == "AssistantMessage" and hasattr(msg, "content"):
                for block in msg.content:
                    block_type = type(block).__name__

                    if block_type == "TextBlock" and hasattr(block, "text"):
                        response_text += block.text
                        print(block.text, end="", flush=True)
                        # Log text to task logger (persist without double-printing)
                        if task_logger and block.text.strip():
                            task_logger.log(
                                block.text,
                                LogEntryType.TEXT,
                                phase,
                                print_to_console=False,
                            )
                    elif block_type == "ToolUseBlock" and hasattr(block, "name"):
                        tool_name = block.name
                        tool_input_display = None
                        tool_count += 1

                        # Safely extract tool input (handles None, non-dict, etc.)
                        inp = get_safe_tool_input(block)

                        # Extract meaningful tool input for display
                        if inp:
                            if "pattern" in inp:
                                tool_input_display = f"pattern: {inp['pattern']}"
                            elif "file_path" in inp:
                                fp = inp["file_path"]
                                if len(fp) > 50:
                                    fp = "..." + fp[-47:]
                                tool_input_display = fp
                            elif "command" in inp:
                                cmd = inp["command"]
                                if len(cmd) > 50:
                                    cmd = cmd[:47] + "..."
                                tool_input_display = cmd
                            elif "path" in inp:
                                tool_input_display = inp["path"]

                        debug(
                            "session",
                            f"Tool call #{tool_count}: {tool_name}",
                            tool_input=tool_input_display,
                            full_input=str(inp)[:500] if inp else None,
                        )

                        # Log tool start (handles printing too)
                        if task_logger:
                            task_logger.tool_start(
                                tool_name,
                                tool_input_display,
                                phase,
                                print_to_console=True,
                            )
                        else:
                            print(f"\n[Tool: {tool_name}]", flush=True)

                        if verbose and hasattr(block, "input"):
                            input_str = str(block.input)
                            if len(input_str) > 300:
                                print(f"   Input: {input_str[:300]}...", flush=True)
                            else:
                                print(f"   Input: {input_str}", flush=True)
                        current_tool = tool_name

            # Handle UserMessage (tool results)
            elif msg_type == "UserMessage" and hasattr(msg, "content"):
                for block in msg.content:
                    block_type = type(block).__name__

                    if block_type == "ToolResultBlock":
                        result_content = getattr(block, "content", "")
                        is_error = getattr(block, "is_error", False)

                        # Check if command was blocked by security hook
                        if "blocked" in str(result_content).lower():
                            debug_error(
                                "session",
                                f"Tool BLOCKED: {current_tool}",
                                result=str(result_content)[:300],
                            )
                            print(f"   [BLOCKED] {result_content}", flush=True)
                            if task_logger and current_tool:
                                task_logger.tool_end(
                                    current_tool,
                                    success=False,
                                    result="BLOCKED",
                                    detail=str(result_content),
                                    phase=phase,
                                )
                        elif is_error:
                            # Show errors (truncated)
                            error_str = str(result_content)[:500]
                            debug_error(
                                "session",
                                f"Tool error: {current_tool}",
                                error=error_str[:200],
                            )
                            print(f"   [Error] {error_str}", flush=True)
                            if task_logger and current_tool:
                                # Store full error in detail for expandable view
                                task_logger.tool_end(
                                    current_tool,
                                    success=False,
                                    result=error_str[:100],
                                    detail=str(result_content),
                                    phase=phase,
                                )
                        else:
                            # Tool succeeded
                            debug_detailed(
                                "session",
                                f"Tool success: {current_tool}",
                                result_length=len(str(result_content)),
                            )
                            if verbose:
                                result_str = str(result_content)[:200]
                                print(f"   [Done] {result_str}", flush=True)
                            else:
                                print("   [Done]", flush=True)
                            if task_logger and current_tool:
                                # Store full result in detail for expandable view (only for certain tools)
                                # Skip storing for very large outputs like Glob results
                                detail_content = None
                                if current_tool in (
                                    "Read",
                                    "Grep",
                                    "Bash",
                                    "Edit",
                                    "Write",
                                ):
                                    result_str = str(result_content)
                                    # Only store if not too large (detail truncation happens in logger)
                                    if (
                                        len(result_str) < 50000
                                    ):  # 50KB max before truncation
                                        detail_content = result_str
                                task_logger.tool_end(
                                    current_tool,
                                    success=True,
                                    detail=detail_content,
                                    phase=phase,
                                )

                        current_tool = None

            # Log cache-hit statistics from ResultMessage.usage so operators can
            # verify that the static CLAUDE.md prefix is being reused across calls.
            # usage is dict[str, Any] | None per the SDK types.
            # Fields set by the Anthropic API when caching is active:
            #   cache_read_input_tokens    — tokens served from cache (0.10× price)
            #   cache_creation_input_tokens — tokens written to cache (1.25× / 2× price)
            #   input_tokens               — tokens after the last cache breakpoint
            # Reference: https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching
            elif msg_type == "ResultMessage":
                _usage = getattr(msg, "usage", None) or {}
                _cache_read = _usage.get("cache_read_input_tokens", 0)
                _cache_write = _usage.get("cache_creation_input_tokens", 0)
                _input_tokens = _usage.get("input_tokens", 0)
                if _cache_read or _cache_write:
                    # Per-turn line at DEBUG — high cardinality, only useful
                    # when verifying caching. Aggregate at session end.
                    logger.debug(
                        "Prompt cache: read=%d tok write=%d tok input=%d tok session=%s",
                        _cache_read,
                        _cache_write,
                        _input_tokens,
                        getattr(msg, "session_id", "?"),
                    )
                    if task_logger:
                        task_logger.log(
                            f"[cache] read={_cache_read} write={_cache_write} input={_input_tokens}",
                            LogEntryType.TEXT,
                            phase,
                            print_to_console=False,
                        )
                _cache_read_total += _cache_read
                _cache_write_total += _cache_write
                _last_session_id = getattr(msg, "session_id", _last_session_id)

        # Aggregated cache totals — one line per agent session so operators
        # can confirm the static prefix is being reused across turns.
        if _cache_read_total or _cache_write_total:
            logger.info(
                "Prompt cache totals — read=%d tok write=%d tok session=%s",
                _cache_read_total,
                _cache_write_total,
                _last_session_id or "?",
            )

        print("\n" + "-" * 70 + "\n")

        # Check if build is complete
        if is_build_complete(spec_dir):
            debug_success(
                "session",
                "Session completed - build is complete",
                message_count=message_count,
                tool_count=tool_count,
                response_length=len(response_text),
            )
            return "complete", response_text, {}

        debug_success(
            "session",
            "Session completed - continuing",
            message_count=message_count,
            tool_count=tool_count,
            response_length=len(response_text),
        )
        return "continue", response_text, {}

    except Exception as e:
        # Detect specific error types for better retry handling
        is_concurrency = is_tool_concurrency_error(e)
        is_rate_limit = is_rate_limit_error(e)
        is_auth = is_authentication_error(e)

        # Classify error type for appropriate handling
        if is_concurrency:
            error_type = "tool_concurrency"
        elif is_rate_limit:
            error_type = "rate_limit"
        elif is_auth:
            error_type = "authentication"
        else:
            error_type = "other"

        debug_error(
            "session",
            f"Session error: {e}",
            exception_type=type(e).__name__,
            error_category=error_type,
            message_count=message_count,
            tool_count=tool_count,
        )

        # Sanitize error message to remove potentially sensitive data
        sanitized_error = sanitize_error_message(str(e))

        # Log errors prominently based on type
        if is_concurrency:
            print("\n  Tool concurrency limit reached (400 error)")
            print("   Claude API limits concurrent tool use in a single request")
            print(f"   Error: {sanitized_error[:200]}\n")
        elif is_rate_limit:
            print("\n  Rate limit reached")
            print("   API usage quota exceeded - waiting for reset")
            print(f"   Error: {sanitized_error[:200]}\n")
        elif is_auth:
            print("\n  Authentication error")
            print("   OAuth token may be invalid or expired")
            print(f"   Error: {sanitized_error[:200]}\n")
        else:
            print(f"Error during agent session: {sanitized_error}")

        if task_logger:
            task_logger.log_error(f"Session error: {sanitized_error}", phase)

        error_info = {
            "type": error_type,
            "message": sanitized_error,
            "exception_type": type(e).__name__,
        }
        return "error", sanitized_error, error_info
