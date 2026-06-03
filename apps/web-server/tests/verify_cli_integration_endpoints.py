#!/usr/bin/env python3
"""
Verification script for all 10 CLI integration endpoint implementations.

This script validates that all CLI integration endpoints identified in task 012
have been properly implemented (not returning stub responses).

Verification includes:
- Endpoint function exists in source file
- Function is not a stub (not just returning {"success": True})
- Function contains actual CLI command execution
- Proper error handling is present
- Input validation is implemented
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Tuple


# ============================================================================
# Configuration
# ============================================================================

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
ROUTES_DIR = PROJECT_ROOT / "apps" / "web-server" / "server" / "routes"

# CLI integration endpoints from implementation plan
CLI_ENDPOINTS = [
    # Phase 7: GitLab CLI Operations (5 endpoints)
    {
        "id": "7.1",
        "title": "update_merge_request",
        "file": "gitlab.py",
        "function": "update_merge_request",
        "cli_tool": "glab",
        "phase": "Phase 7: GitLab CLI Operations"
    },
    {
        "id": "7.2",
        "title": "assign_merge_request",
        "file": "gitlab.py",
        "function": "assign_merge_request",
        "cli_tool": "glab",
        "phase": "Phase 7: GitLab CLI Operations"
    },
    {
        "id": "7.3",
        "title": "approve_merge_request",
        "file": "gitlab.py",
        "function": "approve_merge_request",
        "cli_tool": "glab",
        "phase": "Phase 7: GitLab CLI Operations"
    },
    {
        "id": "7.4",
        "title": "merge_merge_request",
        "file": "gitlab.py",
        "function": "merge_merge_request",
        "cli_tool": "glab",
        "phase": "Phase 7: GitLab CLI Operations"
    },
    {
        "id": "7.5",
        "title": "post_merge_request_note",
        "file": "gitlab.py",
        "function": "post_mr_note",  # Actual function name
        "cli_tool": "glab",
        "phase": "Phase 7: GitLab CLI Operations"
    },
    # Phase 9: Context Management (1 endpoint)
    {
        "id": "9.3",
        "title": "invoke_claude_setup",
        "file": "context.py",
        "function": "invoke_claude_setup",
        "cli_tool": "claude",
        "phase": "Phase 9: Context Management"
    },
    # Phase 10: Git Operations (2 endpoints)
    {
        "id": "10.1",
        "title": "squash_commits",
        "file": "git.py",
        "function": "squash_commits",
        "cli_tool": "git",
        "phase": "Phase 10: Git Operations"
    },
    {
        "id": "10.2",
        "title": "create_worktree",
        "file": "git.py",
        "function": "create_worktree",
        "cli_tool": "git",
        "phase": "Phase 10: Git Operations"
    },
    # Phase 14: Git Maintenance & Reviews (2 endpoints)
    {
        "id": "14.1",
        "title": "download_source_update",
        "file": "git.py",
        "function": "download_source_update",
        "cli_tool": "git",
        "phase": "Phase 14: Git Maintenance & Reviews"
    },
    {
        "id": "14.2",
        "title": "create_release",
        "file": "git.py",
        "function": "create_release",
        "cli_tool": "gh/glab",
        "phase": "Phase 14: Git Maintenance & Reviews"
    },
]


# ============================================================================
# Verification Functions
# ============================================================================

def check_endpoint_exists(endpoint: Dict) -> Tuple[bool, str]:
    """Check if endpoint function exists in source file."""
    file_path = ROUTES_DIR / endpoint["file"]

    if not file_path.exists():
        return False, f"File not found: {file_path}"

    content = file_path.read_text()
    function_name = endpoint["function"]

    # Look for function definition
    pattern = rf"def {function_name}\("
    if not re.search(pattern, content):
        return False, f"Function '{function_name}' not found in {endpoint['file']}"

    return True, "Function exists"


def check_not_stub(endpoint: Dict) -> Tuple[bool, str]:
    """Check that endpoint is not a stub implementation."""
    file_path = ROUTES_DIR / endpoint["file"]
    content = file_path.read_text()
    function_name = endpoint["function"]

    # Extract function body
    pattern = rf"def {function_name}\(.*?\):(.*?)(?=\ndef |\nclass |\Z)"
    match = re.search(pattern, content, re.DOTALL)

    if not match:
        return False, "Could not extract function body"

    function_body = match.group(1)

    # Check for stub patterns
    stub_patterns = [
        r'return\s*\{\s*"success"\s*:\s*True\s*\}',  # Simple stub
        r'return\s*\{\s*"success"\s*:\s*True\s*,\s*\}',  # Stub with trailing comma
    ]

    for pattern in stub_patterns:
        if re.search(pattern, function_body):
            return False, "Function contains stub response pattern"

    return True, "Not a stub"


def check_cli_command_execution(endpoint: Dict) -> Tuple[bool, str]:
    """Check that endpoint contains CLI command execution."""
    file_path = ROUTES_DIR / endpoint["file"]
    content = file_path.read_text()
    function_name = endpoint["function"]

    # Extract function body
    pattern = rf"def {function_name}\(.*?\):(.*?)(?=\ndef |\nclass |\Z)"
    match = re.search(pattern, content, re.DOTALL)

    if not match:
        return False, "Could not extract function body"

    function_body = match.group(1)

    # Check for CLI execution patterns
    cli_patterns = [
        r'run_glab_command',  # glab CLI wrapper
        r'run_gh_command',    # gh CLI wrapper
        r'run_git_command',   # git CLI wrapper
        r'subprocess\.run',   # Direct subprocess call
        r'subprocess\.Popen', # Direct subprocess call
    ]

    found_patterns = []
    for pattern in cli_patterns:
        if re.search(pattern, function_body):
            found_patterns.append(pattern)

    if not found_patterns:
        return False, "No CLI command execution found"

    return True, f"CLI execution found: {', '.join(found_patterns)}"


def check_error_handling(endpoint: Dict) -> Tuple[bool, str]:
    """Check that endpoint has proper error handling."""
    file_path = ROUTES_DIR / endpoint["file"]
    content = file_path.read_text()
    function_name = endpoint["function"]

    # Extract function body
    pattern = rf"def {function_name}\(.*?\):(.*?)(?=\ndef |\nclass |\Z)"
    match = re.search(pattern, content, re.DOTALL)

    if not match:
        return False, "Could not extract function body"

    function_body = match.group(1)

    # Check for error handling patterns
    error_patterns = [
        r'try:',
        r'except',
        r'HTTPException',
        r'if\s+not\s+',  # Validation checks
        r'raise',
    ]

    found_patterns = []
    for pattern in error_patterns:
        if re.search(pattern, function_body):
            found_patterns.append(pattern)

    if len(found_patterns) < 2:
        return False, f"Insufficient error handling (found {len(found_patterns)} patterns)"

    return True, f"Error handling present ({len(found_patterns)} patterns)"


def check_input_validation(endpoint: Dict) -> Tuple[bool, str]:
    """Check that endpoint has input validation."""
    file_path = ROUTES_DIR / endpoint["file"]
    content = file_path.read_text()
    function_name = endpoint["function"]

    # Extract function body
    pattern = rf"def {function_name}\(.*?\):(.*?)(?=\ndef |\nclass |\Z)"
    match = re.search(pattern, content, re.DOTALL)

    if not match:
        return False, "Could not extract function body"

    function_body = match.group(1)

    # Check for validation patterns
    validation_patterns = [
        r'if\s+not\s+\w+',  # Not empty checks
        r'\.strip\(',       # Whitespace stripping
        r'len\(',           # Length checks
        r'load_projects',   # Project validation
        r'Pydantic|BaseModel',  # Pydantic validation
    ]

    found_patterns = []
    for pattern in validation_patterns:
        if re.search(pattern, function_body, re.IGNORECASE):
            found_patterns.append(pattern)

    if not found_patterns:
        return False, "No input validation found"

    return True, f"Input validation present ({len(found_patterns)} patterns)"


# ============================================================================
# Main Verification
# ============================================================================

def verify_all_endpoints() -> Dict:
    """Verify all CLI integration endpoints."""
    results = {
        "total_endpoints": len(CLI_ENDPOINTS),
        "verified": 0,
        "failed": 0,
        "endpoints": [],
        "by_phase": {},
        "by_cli_tool": {}
    }

    for endpoint in CLI_ENDPOINTS:
        endpoint_result = {
            "id": endpoint["id"],
            "title": endpoint["title"],
            "file": endpoint["file"],
            "function": endpoint["function"],
            "cli_tool": endpoint["cli_tool"],
            "phase": endpoint["phase"],
            "checks": {},
            "status": "verified",
            "issues": []
        }

        # Run all checks
        checks = [
            ("exists", check_endpoint_exists),
            ("not_stub", check_not_stub),
            ("cli_execution", check_cli_command_execution),
            ("error_handling", check_error_handling),
            ("input_validation", check_input_validation),
        ]

        all_passed = True
        for check_name, check_func in checks:
            passed, message = check_func(endpoint)
            endpoint_result["checks"][check_name] = {
                "passed": passed,
                "message": message
            }
            if not passed:
                all_passed = False
                endpoint_result["issues"].append(f"{check_name}: {message}")

        if all_passed:
            endpoint_result["status"] = "verified"
            results["verified"] += 1
        else:
            endpoint_result["status"] = "failed"
            results["failed"] += 1

        results["endpoints"].append(endpoint_result)

        # Track by phase
        phase = endpoint["phase"]
        if phase not in results["by_phase"]:
            results["by_phase"][phase] = {"total": 0, "verified": 0}
        results["by_phase"][phase]["total"] += 1
        if all_passed:
            results["by_phase"][phase]["verified"] += 1

        # Track by CLI tool
        cli_tool = endpoint["cli_tool"]
        if cli_tool not in results["by_cli_tool"]:
            results["by_cli_tool"][cli_tool] = {"total": 0, "verified": 0}
        results["by_cli_tool"][cli_tool]["total"] += 1
        if all_passed:
            results["by_cli_tool"][cli_tool]["verified"] += 1

    return results


def print_verification_report(results: Dict):
    """Print formatted verification report."""
    print("=" * 80)
    print("CLI INTEGRATION ENDPOINTS VERIFICATION REPORT")
    print("=" * 80)
    print()

    print(f"Total Endpoints: {results['total_endpoints']}")
    print(f"✅ Verified: {results['verified']} ({results['verified']/results['total_endpoints']*100:.1f}%)")
    print(f"❌ Failed: {results['failed']}")
    print()

    print("-" * 80)
    print("VERIFICATION BY PHASE")
    print("-" * 80)
    for phase, stats in sorted(results["by_phase"].items()):
        status = "✅" if stats["verified"] == stats["total"] else "⚠️"
        print(f"{status} {phase}: {stats['verified']}/{stats['total']}")
    print()

    print("-" * 80)
    print("VERIFICATION BY CLI TOOL")
    print("-" * 80)
    for tool, stats in sorted(results["by_cli_tool"].items()):
        status = "✅" if stats["verified"] == stats["total"] else "⚠️"
        print(f"{status} {tool}: {stats['verified']}/{stats['total']}")
    print()

    print("-" * 80)
    print("ENDPOINT DETAILS")
    print("-" * 80)
    for ep in results["endpoints"]:
        status_icon = "✅" if ep["status"] == "verified" else "❌"
        print(f"{status_icon} [{ep['id']}] {ep['title']} ({ep['file']})")

        for check_name, check_result in ep["checks"].items():
            check_icon = "  ✓" if check_result["passed"] else "  ✗"
            print(f"{check_icon} {check_name}: {check_result['message']}")

        if ep["issues"]:
            print(f"  Issues:")
            for issue in ep["issues"]:
                print(f"    - {issue}")
        print()

    print("=" * 80)
    print("VERIFICATION SUMMARY")
    print("=" * 80)
    if results["failed"] == 0:
        print("✅ ALL CLI INTEGRATION ENDPOINTS VERIFIED!")
        print("   All endpoints are properly implemented with:")
        print("   - CLI command execution")
        print("   - Error handling")
        print("   - Input validation")
        print("   - No stub responses")
    else:
        print(f"⚠️  {results['failed']} endpoint(s) need attention")
        print("   See details above for specific issues")
    print("=" * 80)


def save_json_report(results: Dict, output_path: Path):
    """Save detailed verification results as JSON."""
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n📄 Detailed JSON report saved to: {output_path}")


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    print("Starting CLI integration endpoints verification...\n")

    results = verify_all_endpoints()
    print_verification_report(results)

    # Save JSON report
    output_path = Path(__file__).parent / "cli_integration_verification_results.json"
    save_json_report(results, output_path)

    # Exit with appropriate code
    exit(0 if results["failed"] == 0 else 1)
