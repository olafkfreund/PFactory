"""
Changelog generation runner script.

This script is executed as a subprocess by the changelog service. It loads data,
builds prompts, calls the Claude API, and generates changelog content.
"""

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def emit_phase(phase_num: int, phase_name: str):
    """Print phase marker to stdout for service to detect."""
    print(f"CHANGELOG PHASE {phase_num}: {phase_name}", flush=True)


def emit_complete():
    """Print completion marker."""
    print("CHANGELOG GENERATION COMPLETE", flush=True)


def emit_failed(error: str):
    """Print failure marker."""
    print(f"CHANGELOG GENERATION FAILED: {error}", flush=True)


def load_task_specs(project_path: Path, task_ids: list[str]) -> list[dict[str, Any]]:
    """
    Load spec.md, requirements.json, qa_report.md for each task.

    Args:
        project_path: Path to the project
        task_ids: List of task IDs (e.g., ["001-auth", "002-dashboard"])

    Returns:
        List of task data dictionaries
    """
    tasks = []
    specs_dir = project_path / ".pfactory" / "specs"

    if not specs_dir.exists():
        logger.warning(f"Specs directory not found: {specs_dir}")
        return tasks

    for task_id in task_ids:
        task_dir = specs_dir / task_id
        if not task_dir.exists():
            logger.warning(f"Task directory not found: {task_dir}")
            continue

        task_data = {"id": task_id}

        # Load spec.md
        spec_file = task_dir / "spec.md"
        if spec_file.exists():
            content = spec_file.read_text(encoding="utf-8")
            # Limit to first 2000 characters
            task_data["spec"] = content[:2000]
            if len(content) > 2000:
                task_data["spec"] += "\n\n[... truncated ...]"

        # Load requirements.json
        req_file = task_dir / "requirements.json"
        if req_file.exists():
            try:
                with open(req_file, encoding="utf-8") as f:
                    req_content = f.read()
                # Limit to first 1000 characters
                if len(req_content) > 1000:
                    req_content = req_content[:1000] + "\n[... truncated ...]"
                task_data["requirements"] = req_content
            except Exception as e:
                logger.warning(f"Failed to read requirements.json for {task_id}: {e}")

        # Load qa_report.md
        qa_file = task_dir / "qa_report.md"
        if qa_file.exists():
            content = qa_file.read_text(encoding="utf-8")
            # Limit to first 1000 characters
            task_data["qa_report"] = content[:1000]
            if len(content) > 1000:
                task_data["qa_report"] += "\n\n[... truncated ...]"

        tasks.append(task_data)

    return tasks


def _parse_commits(raw_output: str) -> list[dict[str, Any]]:
    """
    Parse git log output using record/field separators.

    Expects format: %x00%H%x1f%h%x1f%s%x1f%b%x1f%an%x1f%ae%x1f%aI
    where %x00 (NUL) separates records and %x1f (unit separator) separates fields.
    This safely handles commit bodies with newlines and pipe characters.
    """
    commits = []
    # Split by NUL record separator, filter empties
    for record in raw_output.split("\x00"):
        record = record.strip()
        if not record:
            continue

        parts = record.split("\x1f")
        if len(parts) >= 7:
            body = parts[3].strip()[:200] if parts[3].strip() else ""
            commits.append({
                "hash": parts[0],
                "short_hash": parts[1],
                "subject": parts[2],
                "body": body,
                "author_name": parts[4],
                "author_email": parts[5],
                "date": parts[6].strip()
            })

    return commits


# Git log format using NUL as record separator and unit separator for fields.
# This prevents multi-line commit bodies and pipe characters from breaking parsing.
_GIT_LOG_FORMAT = "%x00%H%x1f%h%x1f%s%x1f%b%x1f%an%x1f%ae%x1f%aI"


def load_git_commits(
    project_path: Path,
    history_type: str,
    count: int = 25,
    since_date: str | None = None,
    from_tag: str | None = None,
    to_tag: str | None = None,
    include_merge_commits: bool = False
) -> list[dict[str, Any]]:
    """
    Load git commits based on history options.

    Args:
        project_path: Path to the project
        history_type: Type of history ('recent', 'since-date', 'tag-range', 'since-version')
        count: Number of commits for 'recent' type
        since_date: Date for 'since-date' type
        from_tag: Starting tag for 'tag-range' and 'since-version'
        to_tag: Ending tag for 'tag-range'
        include_merge_commits: Whether to include merge commits

    Returns:
        List of commit dictionaries
    """
    # Build git log command
    cmd = ["git", "log", f"--format={_GIT_LOG_FORMAT}"]

    if history_type == "recent":
        cmd.extend(["-n", str(count)])
    elif history_type == "since-date":
        if since_date:
            cmd.extend([f"--since={since_date}"])
    elif history_type == "tag-range":
        if from_tag and to_tag:
            cmd.append(f"{from_tag}..{to_tag}")
    elif history_type == "since-version":
        if from_tag:
            cmd.append(f"{from_tag}..HEAD")

    if not include_merge_commits:
        cmd.append("--no-merges")

    try:
        result = subprocess.run(
            cmd,
            cwd=project_path,
            capture_output=True,
            text=True,
            check=True
        )

        return _parse_commits(result.stdout)

    except subprocess.CalledProcessError as e:
        logger.error(f"Git command failed: {e.stderr}")
        raise RuntimeError(f"Failed to load git commits: {e.stderr}")


def load_branch_diff_commits(
    project_path: Path,
    base_branch: str,
    compare_branch: str
) -> list[dict[str, Any]]:
    """
    Load commits unique to compare branch.

    Args:
        project_path: Path to the project
        base_branch: Base branch (e.g., 'main')
        compare_branch: Compare branch (e.g., 'feature/auth')

    Returns:
        List of commit dictionaries
    """
    cmd = [
        "git", "log",
        f"{base_branch}..{compare_branch}",
        f"--format={_GIT_LOG_FORMAT}",
        "--no-merges"
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=project_path,
            capture_output=True,
            text=True,
            check=True
        )

        return _parse_commits(result.stdout)

    except subprocess.CalledProcessError as e:
        logger.error(f"Git command failed: {e.stderr}")
        raise RuntimeError(f"Failed to load branch diff commits: {e.stderr}")


def build_changelog_prompt(
    source_mode: str,
    data: list[dict[str, Any]],
    version: str,
    date: str,
    format_type: str,
    audience: str,
    emoji_level: str | None,
    custom_instructions: str | None
) -> str:
    """
    Build LLM prompt for changelog generation.

    Args:
        source_mode: Source mode ('tasks', 'git-history', 'branch-diff')
        data: Source data (tasks or commits)
        version: Version number
        date: Release date
        format_type: Format type ('keep-a-changelog', 'simple-list', 'github-release')
        audience: Target audience ('technical', 'user-facing', 'marketing')
        emoji_level: Emoji level ('none', 'little', 'medium', 'high')
        custom_instructions: Custom instructions

    Returns:
        Complete prompt string
    """
    prompt = f"""You are an AI assistant that generates high-quality changelogs from code changes.

**Task:** Generate a changelog for version {version} (date: {date})

**Format:** {format_type}

"""

    # Add format-specific instructions
    if format_type == "keep-a-changelog":
        prompt += """Follow the Keep a Changelog format (https://keepachangelog.com):
- Use ## [Version] - Date header
- Group changes by category: Added, Changed, Fixed, Deprecated, Removed, Security
- Use bullet points for each change
- Be specific and user-focused

"""
    elif format_type == "simple-list":
        prompt += """Use a simple bulleted list format:
- ## Version - Date
- List all changes as bullet points
- Group related changes together

"""
    elif format_type == "github-release":
        prompt += """Use GitHub Release format:
- ## What's Changed header
- Group by: **New Features**, **Bug Fixes**, **Other Changes**
- Use descriptive bullet points

"""

    # Add audience-specific guidance
    prompt += f"**Audience:** {audience}\n\n"

    if audience == "technical":
        prompt += """Write for developers. Include:
- Technical details (API changes, breaking changes, dependencies)
- Code examples where relevant
- Migration notes if needed

"""
    elif audience == "user-facing":
        prompt += """Write for end users. Focus on:
- What changed from user's perspective
- Benefits and improvements
- Simple, jargon-free language

"""
    elif audience == "marketing":
        prompt += """Write for marketing/announcements. Emphasize:
- Value proposition and benefits
- Excitement and engagement
- Clear impact statements

"""

    # Add emoji level instructions
    if emoji_level and emoji_level != "none":
        prompt += f"**Emoji Level:** {emoji_level}\n"
        if emoji_level == "little":
            prompt += "Add emojis for section headers only (✨ Added, 🐛 Fixed, etc.)\n\n"
        elif emoji_level == "medium":
            prompt += "Add emojis for headers + major changes\n\n"
        elif emoji_level == "high":
            prompt += "Add emojis throughout for all changes\n\n"
    else:
        prompt += "**Emojis:** Do NOT include any emoji characters (e.g. ✨🐛🔧📌💡🎯🔐) in the output. Still use all section headers, categories, and markdown formatting as required by the format above — just no emoji characters.\n\n"

    # Add custom instructions
    if custom_instructions:
        prompt += f"**Custom Instructions:**\n{custom_instructions}\n\n"

    prompt += "---\n\n**SOURCE DATA:**\n\n"
    prompt += f"**Mode:** {source_mode}\n"
    prompt += f"**Number of items:** {len(data)}\n\n"

    # Format source data
    if source_mode == "tasks":
        prompt += "The following tasks were completed:\n\n"
        for task in data:
            prompt += f"### Task: {task['id']}\n"
            if "spec" in task:
                prompt += f"**Specification:**\n{task['spec']}\n\n"
            if "requirements" in task:
                prompt += f"**Requirements:**\n{task['requirements']}\n\n"
            if "qa_report" in task:
                prompt += f"**QA Report:**\n{task['qa_report']}\n\n"
            prompt += "---\n\n"
    else:
        prompt += "The following commits were made:\n\n"
        for commit in data:
            prompt += f"**Commit {commit['short_hash']}:** {commit['subject']}\n"
            prompt += f"Author: {commit['author_name']}\n"
            prompt += f"Date: {commit['date']}\n"
            if commit.get("body"):
                prompt += f"Body: {commit['body']}\n"
            prompt += "\n"

    prompt += """
**OUTPUT INSTRUCTIONS:**
1. Analyze all changes in the source data
2. Group related changes into logical categories using section headers (e.g. ### Added, ### Fixed, ### Changed)
3. Write clear, concise descriptions following the format
4. Match the audience tone precisely
5. Apply emoji level as specified
6. Include only meaningful changes (skip trivial commits like "fix typo")
7. Output ONLY the changelog content (no preamble or explanation)

Generate the changelog now:
"""

    return prompt


def generate_changelog(
    project_path: Path,
    source_mode: str,
    task_ids: list[str] | None,
    git_history: dict[str, Any] | None,
    branch_diff: dict[str, Any] | None,
    version: str,
    date: str,
    format_type: str,
    audience: str,
    emoji_level: str | None,
    custom_instructions: str | None
) -> str:
    """
    Main generation logic.

    Args:
        project_path: Path to the project
        source_mode: Source mode ('tasks', 'git-history', 'branch-diff')
        task_ids: Task IDs for tasks mode
        git_history: Git history options
        branch_diff: Branch diff options
        version: Version number
        date: Release date
        format_type: Format type
        audience: Target audience
        emoji_level: Emoji level
        custom_instructions: Custom instructions

    Returns:
        Generated changelog content
    """
    emit_phase(1, "STARTING")

    # Validate inputs
    if source_mode not in ["tasks", "git-history", "branch-diff"]:
        raise ValueError(f"Invalid source mode: {source_mode}")

    if not project_path.exists():
        raise ValueError(f"Project path does not exist: {project_path}")

    # Check if git repo
    if source_mode in ["git-history", "branch-diff"]:
        git_dir = project_path / ".git"
        if not git_dir.exists():
            raise ValueError(f"Not a git repository: {project_path}")

    # Load data based on source mode
    emit_phase(2, f"LOADING {source_mode.upper()}")

    data = []
    if source_mode == "tasks":
        if not task_ids:
            raise ValueError("No task IDs provided for tasks mode")
        data = load_task_specs(project_path, task_ids)
        if not data:
            logger.warning("No task data found, generating minimal changelog")

    elif source_mode == "git-history":
        if not git_history:
            raise ValueError("No git history options provided")

        data = load_git_commits(
            project_path,
            history_type=git_history.get("type", "recent"),
            count=git_history.get("count", 25),
            since_date=git_history.get("sinceDate"),
            from_tag=git_history.get("fromTag"),
            to_tag=git_history.get("toTag"),
            include_merge_commits=git_history.get("includeMergeCommits", False)
        )

    elif source_mode == "branch-diff":
        if not branch_diff:
            raise ValueError("No branch diff options provided")

        data = load_branch_diff_commits(
            project_path,
            base_branch=branch_diff.get("baseBranch", "main"),
            compare_branch=branch_diff.get("compareBranch", "HEAD")
        )

    # Analyzing
    emit_phase(3, "ANALYZING")

    # Build prompt
    prompt = build_changelog_prompt(
        source_mode=source_mode,
        data=data,
        version=version,
        date=date,
        format_type=format_type,
        audience=audience,
        emoji_level=emoji_level,
        custom_instructions=custom_instructions
    )

    # Generate with LLM (based on settings)
    emit_phase(4, "GENERATING")

    try:
        import json
        import os

        # Load LLM provider settings from web server config
        settings_file = Path.home() / ".pfactory" / "settings.json"
        llm_provider = "ollama"  # default
        llm_config = {}

        if settings_file.exists():
            with open(settings_file) as f:
                settings = json.load(f)
                llm_provider = settings.get("llmProvider", "ollama")
                llm_config = {
                    "ollama_base_url": settings.get("llmOllamaBaseUrl", "http://localhost:11434"),
                    "ollama_model": settings.get("llmOllamaModel", "qwen3-30b-local:latest"),
                    "anthropic_model": settings.get("llmAnthropicModel", "claude-sonnet-4-5-20250929"),
                    "openai_model": settings.get("llmOpenaiModel", "gpt-4o"),
                    "openai_base_url": settings.get("llmOpenaiBaseUrl")
                }

        logger.info(f"Using LLM provider: {llm_provider}")

        if llm_provider == "ollama":
            from openai import OpenAI

            client = OpenAI(
                base_url=f"{llm_config['ollama_base_url']}/v1",
                api_key="ollama"
            )

            response = client.chat.completions.create(
                model=llm_config['ollama_model'],
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
                temperature=0.7
            )

            logger.info(f"API response received. Model: {response.model}, Finish reason: {response.choices[0].finish_reason}")
            changelog_content = response.choices[0].message.content.strip()

        elif llm_provider == "anthropic":
            from anthropic import Anthropic

            # PFactory is OAuth-only by default (see core/auth.py). Changelog
            # generation with the Anthropic provider is the one exception:
            # the Anthropic SDK's messages.create() does NOT accept an OAuth
            # token — only a direct API key works here. Require ANTHROPIC_API_KEY
            # to be set EXPLICITLY by a user who has accepted the direct-billing
            # trade-off. The previous code tried `os.environ.get("ANTHROPIC_API_KEY")
            # or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")` — the OAuth fallback
            # would silently fail at the API layer because OAuth tokens aren't
            # valid API keys, so it was effectively dead code that only obscured
            # the real failure mode.
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "Changelog generation with the Anthropic provider requires "
                    "ANTHROPIC_API_KEY (direct-billing path). PFactory's default "
                    "is OAuth via Claude Code which does NOT use this key. Set "
                    "ANTHROPIC_API_KEY explicitly only if you accept direct API "
                    "billing, or switch the changelog LLM provider to 'openai' / "
                    "'ollama' / 'openrouter' in Settings → Integrations."
                )

            client = Anthropic(api_key=api_key)

            response = client.messages.create(
                model=llm_config['anthropic_model'],
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}]
            )

            logger.info(f"API response received. Model: {response.model}, Stop reason: {response.stop_reason}")
            changelog_content = response.content[0].text.strip()

        elif llm_provider == "openai":
            from openai import OpenAI

            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("OpenAI API key not found. Please set it in Settings → Integrations.")

            client = OpenAI(
                api_key=api_key,
                base_url=llm_config['openai_base_url']
            )

            response = client.chat.completions.create(
                model=llm_config['openai_model'],
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
                temperature=0.7
            )

            logger.info(f"API response received. Model: {response.model}, Finish reason: {response.choices[0].finish_reason}")
            changelog_content = response.choices[0].message.content.strip()

        else:
            raise RuntimeError(f"Unknown LLM provider: {llm_provider}")

        if not changelog_content:
            raise RuntimeError("No content generated by LLM")

    except Exception as e:
        logger.error(f"Failed to generate changelog with {llm_provider}: {e}")
        raise RuntimeError(f"LLM generation failed: {str(e)}")

    # Formatting
    emit_phase(5, "FORMATTING")

    # Save to file
    changelog_dir = project_path / ".pfactory" / "changelog"
    changelog_dir.mkdir(parents=True, exist_ok=True)

    output_file = changelog_dir / "generated.md"
    output_file.write_text(changelog_content, encoding="utf-8")

    logger.info(f"Saved changelog to {output_file}")

    emit_complete()

    return changelog_content


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Generate changelog using AI")

    # Required arguments
    parser.add_argument("--project", type=str, required=True, help="Project path")
    parser.add_argument("--source-mode", type=str, required=True,
                        choices=["tasks", "git-history", "branch-diff"],
                        help="Source mode")
    parser.add_argument("--version", type=str, required=True, help="Version number")
    parser.add_argument("--date", type=str, required=True, help="Release date")
    parser.add_argument("--format", type=str, required=True,
                        choices=["keep-a-changelog", "simple-list", "github-release"],
                        help="Changelog format")
    parser.add_argument("--audience", type=str, required=True,
                        choices=["technical", "user-facing", "marketing"],
                        help="Target audience")

    # Tasks mode
    parser.add_argument("--task-ids", type=str, help="Comma-separated task IDs")

    # Git history mode
    parser.add_argument("--git-history-type", type=str,
                        choices=["recent", "since-date", "tag-range", "since-version"],
                        help="Git history type")
    parser.add_argument("--git-history-count", type=int, default=25,
                        help="Number of commits for recent type")
    parser.add_argument("--git-history-since-date", type=str,
                        help="Date for since-date type")
    parser.add_argument("--git-history-from-tag", type=str,
                        help="Starting tag")
    parser.add_argument("--git-history-to-tag", type=str,
                        help="Ending tag")
    parser.add_argument("--include-merge-commits", action="store_true",
                        help="Include merge commits")

    # Branch diff mode
    parser.add_argument("--base-branch", type=str, default="main",
                        help="Base branch")
    parser.add_argument("--compare-branch", type=str, default="HEAD",
                        help="Compare branch")

    # Optional
    parser.add_argument("--emoji-level", type=str,
                        choices=["none", "little", "medium", "high"],
                        help="Emoji level")
    parser.add_argument("--custom-instructions", type=str,
                        help="Custom instructions")

    args = parser.parse_args()

    try:
        project_path = Path(args.project).resolve()

        # Build source-specific options
        task_ids = None
        git_history = None
        branch_diff = None

        if args.source_mode == "tasks":
            if not args.task_ids:
                emit_failed("No task IDs provided for tasks mode")
                sys.exit(1)
            task_ids = [tid.strip() for tid in args.task_ids.split(",")]

        elif args.source_mode == "git-history":
            if not args.git_history_type:
                emit_failed("No git history type provided")
                sys.exit(1)

            git_history = {
                "type": args.git_history_type,
                "count": args.git_history_count,
                "sinceDate": args.git_history_since_date,
                "fromTag": args.git_history_from_tag,
                "toTag": args.git_history_to_tag,
                "includeMergeCommits": args.include_merge_commits
            }

        elif args.source_mode == "branch-diff":
            branch_diff = {
                "baseBranch": args.base_branch,
                "compareBranch": args.compare_branch
            }

        # Generate changelog
        generate_changelog(
            project_path=project_path,
            source_mode=args.source_mode,
            task_ids=task_ids,
            git_history=git_history,
            branch_diff=branch_diff,
            version=args.version,
            date=args.date,
            format_type=args.format,
            audience=args.audience,
            emoji_level=args.emoji_level,
            custom_instructions=args.custom_instructions
        )

    except Exception as e:
        logger.error(f"Changelog generation failed: {e}", exc_info=True)
        emit_failed(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
