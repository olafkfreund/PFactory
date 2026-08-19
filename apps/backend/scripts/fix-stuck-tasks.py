#!/usr/bin/env python3
"""
Fix Stuck Tasks Script
======================

This script fixes tasks that are stuck in incorrect states by:
1. Creating missing review_state.json files
2. Correcting invalid status fields
3. Identifying tasks that need attention

Usage:
    python scripts/fix-stuck-tasks.py /path/to/project
    python scripts/fix-stuck-tasks.py --dry-run /path/to/project
"""

import argparse
import json
import sys
from pathlib import Path


def find_spec_directories(project_path: Path) -> list[Path]:
    """Find all spec directories in a project."""
    specs_dir = project_path / ".pfactory" / "specs"
    if not specs_dir.exists():
        return []

    return [d for d in specs_dir.iterdir() if d.is_dir()]


def check_task_state(spec_dir: Path) -> tuple[bool, list[str]]:
    """Check if a task has issues.

    Returns:
        (has_issues, list_of_issues)
    """
    issues = []

    # Check for test_plan.json
    plan_file = spec_dir / "test_plan.json"
    if not plan_file.exists():
        issues.append("Missing test_plan.json")
        return (True, issues)

    try:
        with open(plan_file) as f:
            plan = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        issues.append(f"Cannot read test_plan.json: {e}")
        return (True, issues)

    # Check status field
    status = plan.get("status", "")
    valid_statuses = [
        # Current frontend statuses
        "backlog",
        "in_progress",
        "ai_review",
        "human_review",
        "done",
        # Legacy values (may exist in older spec files)
        "pending",
        "qa_failed",
        "completed",
        "cancelled",
        "review",
    ]

    if status not in valid_statuses:
        issues.append(f"Invalid status: '{status}' (should be one of {valid_statuses})")

    # Check for review_state.json
    review_state_file = spec_dir / "review_state.json"
    if not review_state_file.exists():
        # Only an issue if task requires review
        review_reason = plan.get("reviewReason", "")
        if review_reason or status == "human_review":
            issues.append("Missing review_state.json (needed for review)")

    # Check for stuck states
    if status == "backlog":
        # Check if any subtasks are started/completed
        phases = plan.get("phases", [])
        has_progress = False
        for phase in phases:
            subtasks = phase.get("subtasks", [])
            for subtask in subtasks:
                if subtask.get("status") in ["in_progress", "completed"]:
                    has_progress = True
                    break
            if has_progress:
                break

        if has_progress:
            issues.append(
                "Status is 'backlog' but task has progress (should be 'in_progress' or 'human_review')"
            )

    return (len(issues) > 0, issues)


def fix_task(spec_dir: Path, dry_run: bool = False) -> bool:
    """Fix issues in a task.

    Returns:
        True if fixes were applied
    """
    fixed = False

    # Read plan
    plan_file = spec_dir / "test_plan.json"
    if not plan_file.exists():
        print("  ✗ Cannot fix: missing test_plan.json")
        return False

    try:
        with open(plan_file) as f:
            plan = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  ✗ Cannot fix: {e}")
        return False

    # Fix 1: Create review_state.json if missing
    review_state_file = spec_dir / "review_state.json"
    if not review_state_file.exists():
        review_reason = plan.get("reviewReason", "")
        status = plan.get("status", "")

        if review_reason or status == "human_review":
            print("  → Creating review_state.json")

            if not dry_run:
                review_state = {
                    "approved": False,
                    "approved_by": "",
                    "approved_at": "",
                    "feedback": [],
                    "spec_hash": "",
                    "review_count": 0,
                }
                with open(review_state_file, "w") as f:
                    json.dump(review_state, f, indent=2)

            fixed = True

    # Fix 2: Correct invalid status
    status = plan.get("status", "")

    if status == "backlog":
        # Check if task has progress
        phases = plan.get("phases", [])
        has_progress = False
        for phase in phases:
            subtasks = phase.get("subtasks", [])
            for subtask in subtasks:
                if subtask.get("status") in ["in_progress", "completed"]:
                    has_progress = True
                    break
            if has_progress:
                break

        if has_progress:
            # Determine correct status
            review_reason = plan.get("reviewReason", "")
            if review_reason == "plan_review":
                new_status = "human_review"
            else:
                new_status = "in_progress"

            print(f"  → Updating status: 'backlog' → '{new_status}'")

            if not dry_run:
                plan["status"] = new_status
                with open(plan_file, "w") as f:
                    json.dump(plan, f, indent=2)

            fixed = True

    elif status not in [
        "backlog",
        "in_progress",
        "ai_review",
        "human_review",
        "done",
        "pending",
        "qa_failed",
        "completed",
        "cancelled",
        "review",
    ]:
        print(f"  → Correcting invalid status: '{status}' → 'in_progress'")

        if not dry_run:
            plan["status"] = "in_progress"
            with open(plan_file, "w") as f:
                json.dump(plan, f, indent=2)

        fixed = True

    return fixed


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix stuck tasks in PFactory projects")
    parser.add_argument("project_path", type=Path, help="Path to the project directory")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be fixed without making changes",
    )

    args = parser.parse_args()

    project_path = Path(args.project_path)
    if not project_path.exists():
        print(f"Error: Project path does not exist: {project_path}")
        sys.exit(1)

    print(f"Scanning project: {project_path}")
    if args.dry_run:
        print("(DRY RUN - no changes will be made)")
    print()

    # Find all spec directories
    spec_dirs = find_spec_directories(project_path)

    if not spec_dirs:
        print("No spec directories found in project")
        sys.exit(0)

    print(f"Found {len(spec_dirs)} task(s)")
    print()

    # Check each task
    tasks_with_issues = []
    tasks_fixed = []

    for spec_dir in sorted(spec_dirs):
        spec_name = spec_dir.name
        has_issues, issues = check_task_state(spec_dir)

        if has_issues:
            tasks_with_issues.append(spec_name)
            print(f"⚠️  {spec_name}")
            for issue in issues:
                print(f"    - {issue}")

            # Attempt to fix
            fixed = fix_task(spec_dir, dry_run=args.dry_run)
            if fixed:
                tasks_fixed.append(spec_name)
                print("    ✓ Fixes applied")

            print()

    # Summary
    print("=" * 60)
    print("Summary:")
    print(f"  Total tasks: {len(spec_dirs)}")
    print(f"  Tasks with issues: {len(tasks_with_issues)}")
    print(f"  Tasks fixed: {len(tasks_fixed)}")

    if args.dry_run and tasks_with_issues:
        print()
        print("Run without --dry-run to apply fixes")

    sys.exit(0 if not tasks_with_issues else 1)


if __name__ == "__main__":
    main()
