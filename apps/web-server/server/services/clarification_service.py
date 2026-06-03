"""
Task Clarification Service.

Generates clarification questions for newly created tasks using an LLM.
Uses the same `claude --print` pattern as InsightsService.generate_task_from_chat.
"""

import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def _parse_clarification_json(raw: str) -> dict:
    """Parse a JSON clarification response from LLM output.

    Returns {"questions": [...], "skip": bool, "skipReason": str}.
    On parse failure, returns skip=True as a safe default.
    """
    # Strip markdown fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)

    safe_default = {"questions": [], "skip": True, "skipReason": "Could not analyze task."}

    # Attempt 1: direct parse
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return _validate_response(parsed)
    except json.JSONDecodeError:
        pass

    # Attempt 2: brace-matching — find first { … }
    start = cleaned.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(cleaned[start:i + 1])
                        if isinstance(parsed, dict):
                            return _validate_response(parsed)
                    except json.JSONDecodeError:
                        break

    return safe_default


def _validate_response(parsed: dict) -> dict:
    """Validate and normalize the LLM response structure."""
    skip = bool(parsed.get("skip", False))
    skip_reason = str(parsed.get("skipReason", "")).strip()
    questions = parsed.get("questions", [])

    # Validate questions format
    valid_questions = []
    if isinstance(questions, list):
        for i, q in enumerate(questions[:5]):  # Max 5
            if isinstance(q, dict) and q.get("question"):
                # Parse options (2-4 string choices)
                raw_options = q.get("options", [])
                options = [str(o).strip() for o in raw_options if isinstance(o, str) and o.strip()][:4]
                valid_questions.append({
                    "id": str(q.get("id", f"q{i + 1}")),
                    "question": str(q["question"]).strip(),
                    "options": options,
                })
            elif isinstance(q, str):
                valid_questions.append({
                    "id": f"q{i + 1}",
                    "question": q.strip(),
                    "options": [],
                })

    # If no valid questions and not explicitly skipped, skip anyway
    if not valid_questions and not skip:
        skip = True
        skip_reason = skip_reason or "Task appears sufficiently detailed."

    return {
        "questions": valid_questions,
        "skip": skip,
        "skipReason": skip_reason,
    }


CLARIFICATION_PROMPT = """You are a senior product manager reviewing a task before it goes to a development team.

Analyze the following task and determine if clarification questions are needed.

**Task Title:** {title}

**Task Description:**
{description}

Your job:
1. If the task already has clear requirements, acceptance criteria, or enough detail for a developer to start — return skip=true.
2. If the task is vague, ambiguous, or missing critical information — generate up to 5 targeted clarification questions.

Only ask questions about genuinely missing information. Do NOT ask about:
- Implementation details the developer can decide
- Testing strategies
- Timeline or priority
- Obvious technical choices

Focus on:
- What exactly the user wants (scope, behavior)
- Edge cases that could change the approach
- Integration points or dependencies
- User-facing behavior expectations

Each question MUST include 2-4 multiple-choice options that are concrete, actionable answers the user can pick from. Make options specific to the task context, not generic.

Return ONLY a JSON object with this exact structure:
{{
  "skip": true/false,
  "skipReason": "Brief explanation if skipping",
  "questions": [
    {{"id": "q1", "question": "Your question here?", "options": ["Option A", "Option B", "Option C"]}},
    {{"id": "q2", "question": "Your question here?", "options": ["Option A", "Option B"]}}
  ]
}}

If skip is true, questions should be an empty array.
Respond with ONLY the JSON object, no other text."""


async def generate_clarification_questions(
    title: str,
    description: str,
    project_path: Path,
) -> dict:
    """Generate clarification questions for a task using an LLM.

    Returns dict with keys: questions, skip, skipReason.
    On any failure, returns skip=True so the user is never blocked.
    """
    from .insights_providers import get_provider

    safe_default = {"questions": [], "skip": True, "skipReason": "Could not analyze task."}

    prompt = CLARIFICATION_PROMPT.format(title=title, description=description)

    # Resolve Claude CLI path
    claude_bin = shutil.which("claude") or "claude"

    cmd = [claude_bin, "--print", "--model", "haiku", prompt]

    # Scrub ANTHROPIC_API_KEY (OAuth-only policy — see core/auth.py).
    from ..utils.subprocess_env import make_subprocess_env
    env = make_subprocess_env()
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("CLAUDECODE", None)

    # Resolve OAuth token
    try:
        provider = get_provider("claude")
        token, _pid, profile_name = provider._resolve_claude_token()
        if token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
            logger.info(f"[ClarificationService] Using profile: {profile_name}")
    except Exception:
        pass

    logger.info(f"[ClarificationService] Generating questions for: {title[:80]}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(project_path),
            env=env,
        )

        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        response = stdout.decode("utf-8", errors="replace").strip()

        stderr_text = stderr.decode("utf-8", errors="replace").strip() if stderr else ""
        logger.info(
            f"[ClarificationService] CLI finished: rc={proc.returncode}, "
            f"stdout_len={len(response)}, stderr_len={len(stderr_text)}"
        )
        if stderr_text:
            logger.debug(f"[ClarificationService] stderr: {stderr_text[:500]}")

        if proc.returncode != 0 and not response:
            logger.error(f"[ClarificationService] CLI exited {proc.returncode}")
            return safe_default

        if response:
            result = _parse_clarification_json(response)
            logger.info(
                f"[ClarificationService] Result: skip={result['skip']}, "
                f"questions={len(result['questions'])}"
            )
            return result

        return safe_default

    except asyncio.TimeoutError:
        logger.error("[ClarificationService] Timed out (60s)")
        return safe_default
    except Exception as e:
        logger.error(f"[ClarificationService] Failed: {e}", exc_info=True)
        return safe_default
