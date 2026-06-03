#!/usr/bin/env python3
"""
CLI Integration Endpoints Verification Script
==============================================

Automated verification that all 11 CLI integration endpoints are implemented
and no longer return stub responses.

This script:
1. Checks each endpoint exists in the codebase
2. Verifies it's not a stub (doesn't just return {"success": True})
3. Validates it has proper CLI command execution
4. Generates a verification report

Usage:
    python verify_cli_integration_endpoints.py
"""

import re
import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


# =============================================================================
# ENDPOINT DEFINITIONS
# =============================================================================

CLI_ENDPOINTS = {
    # Phase 7: GitLab CLI Operations (5 endpoints)
    "gitlab.py": [
        {
            "id": "7.1",
            "name": "update_merge_request",
            "function": "update_merge_request",
            "cli_tool": "glab",
            "description": "Update MR title/description using glab CLI"
        },
        {
            "id": "7.2",
            "name": "assign_merge_request",
            "function": "assign_merge_request",
            "cli_tool": "glab",
            "description": "Assign users to MR using glab CLI"
        },
        {
            "id": "7.3",
            "name": "approve_merge_request",
            "function": "approve_merge_request",
            "cli_tool": "glab",
            "description": "Approve MR using glab CLI"
        },
        {
            "id": "7.4",
            "name": "merge_merge_request",
            "function": "merge_merge_request",
            "cli_tool": "glab",
            "description": "Merge MR using glab CLI with safety checks"
        },
        {
            "id": "7.5",
            "name": "post_merge_request_note",
            "function": "post_mr_note",
            "cli_tool": "glab",
            "description": "Post comment on MR using glab CLI"
        },
    ],

    # Phase 9: Context (1 endpoint)
    "context.py": [
        {
            "id": "9.3",
            "name": "invoke_claude_setup",
            "function": "invoke_claude_setup",
            "cli_tool": "claude",
            "description": "Run 'claude setup' CLI command interactively"
        },
    ],

    # Phase 10: Git Operations (2 endpoints)
    "git.py": [
        {
            "id": "10.1",
            "name": "squash_commits",
            "function": "squash_commits",
            "cli_tool": "git",
            "description": "Automated git squash with interactive rebase"
        },
        {
            "id": "10.2",
            "name": "create_worktree",
            "function": "create_worktree",
            "cli_tool": "git",
            "description": "Create git worktree for parallel task work"
        },
    ],

    # Phase 14: Git Maintenance (3 endpoints)
    # Note: git.py endpoints from Phase 14
}

# Phase 14 endpoints are also in git.py
CLI_ENDPOINTS["git.py"].extend([
    {
        "id": "14.1",
        "name": "download_source_update",
        "function": "download_source_update",
        "cli_tool": "git",
        "description": "Update Magestic AI source via git pull"
    },
    {
        "id": "14.2",
        "name": "create_release",
        "function": "create_release",
        "cli_tool": "gh/glab",
        "description": "Create release using gh/glab CLI"
    },
])


# =============================================================================
# VERIFICATION FUNCTIONS
# =============================================================================


def find_endpoint_implementation(file_path: Path, function_name: str) -> tuple[bool, int, list[str]]:
    """
    Find and analyze endpoint implementation.

    Returns:
        (exists, line_number, implementation_lines)
    """
    if not file_path.exists():
        return False, 0, []

    content = file_path.read_text()
    lines = content.split('\n')

    # Find function definition
    function_pattern = rf'^(async\s+)?def\s+{function_name}\s*\('
    implementation_lines = []
    found = False
    line_number = 0

    for i, line in enumerate(lines, 1):
        if re.match(function_pattern, line):
            found = True
            line_number = i
            # Collect next 100 lines or until next function
            for j in range(i - 1, min(i + 99, len(lines))):
                implementation_lines.append(lines[j])
                if j > i and re.match(r'^(async\s+)?def\s+\w+\s*\(', lines[j]):
                    break

    return found, line_number, implementation_lines


def is_stub_implementation(implementation_lines: list[str]) -> bool:
    """
    Check if implementation is still a stub.

    A stub typically just returns {"success": True} without real logic.
    """
    impl_text = '\n'.join(implementation_lines)

    # Check for stub patterns
    stub_patterns = [
        r'return\s*{\s*["\']success["\']\s*:\s*True\s*}',
        r'return\s*{"success":\s*True}',
    ]

    for pattern in stub_patterns:
        if re.search(pattern, impl_text):
            # Also check if there's actual implementation (not just the stub return)
            # Look for subprocess, CLI commands, etc.
            if not any(keyword in impl_text for keyword in [
                'subprocess', 'run_glab_command', 'run_gh_command', 'run_git_command',
                'glab ', 'gh ', 'git ', 'claude '
            ]):
                return True

    return False


def has_cli_integration(implementation_lines: list[str], cli_tool: str) -> bool:
    """Check if implementation includes CLI command execution"""
    impl_text = '\n'.join(implementation_lines)

    cli_indicators = [
        'subprocess.run',
        'subprocess.Popen',
        'run_glab_command',
        'run_gh_command',
        'run_git_command',
        f'"{cli_tool}"',
        f"'{cli_tool}'",
        'glab ',
        'gh ',
        'git ',
        'claude ',
    ]

    return any(indicator in impl_text for indicator in cli_indicators)


def verify_endpoint(file_path: Path, endpoint: dict) -> dict:
    """Verify a single endpoint implementation"""
    result = {
        "id": endpoint["id"],
        "name": endpoint["name"],
        "function": endpoint["function"],
        "cli_tool": endpoint["cli_tool"],
        "exists": False,
        "is_stub": True,
        "has_cli": False,
        "line_number": 0,
        "status": "❌ NOT FOUND"
    }

    exists, line_num, impl_lines = find_endpoint_implementation(file_path, endpoint["function"])

    if not exists:
        return result

    result["exists"] = True
    result["line_number"] = line_num
    is_stub = is_stub_implementation(impl_lines)
    has_cli = has_cli_integration(impl_lines, endpoint["cli_tool"])

    result["is_stub"] = is_stub
    result["has_cli"] = has_cli

    if is_stub:
        result["status"] = "⚠️  STUB"
    elif not has_cli:
        result["status"] = "⚠️  NO CLI"
    else:
        result["status"] = "✅ IMPLEMENTED"

    return result


# =============================================================================
# MAIN VERIFICATION
# =============================================================================


def main():
    """Run verification for all CLI integration endpoints"""
    print("=" * 80)
    print("CLI Integration Endpoints Verification")
    print("=" * 80)
    print()

    routes_dir = project_root / "apps" / "web-server" / "server" / "routes"

    all_results = []
    total_endpoints = 0
    implemented_count = 0
    stub_count = 0
    missing_count = 0

    for file_name, endpoints in CLI_ENDPOINTS.items():
        file_path = routes_dir / file_name
        print(f"\n📁 {file_name}")
        print("-" * 80)

        for endpoint in endpoints:
            total_endpoints += 1
            result = verify_endpoint(file_path, endpoint)
            all_results.append(result)

            status_icon = result["status"].split()[0]
            print(f"{status_icon} {endpoint['id']:6} {endpoint['name']:30} (line {result['line_number'] if result['line_number'] > 0 else 'N/A'})")
            print(f"         CLI Tool: {endpoint['cli_tool']}")

            if result["status"] == "✅ IMPLEMENTED":
                implemented_count += 1
            elif result["status"] == "⚠️  STUB":
                stub_count += 1
            else:
                missing_count += 1

    # Summary
    print("\n" + "=" * 80)
    print("VERIFICATION SUMMARY")
    print("=" * 80)
    print(f"Total CLI Integration Endpoints: {total_endpoints}")
    print(f"✅ Implemented: {implemented_count} ({implemented_count/total_endpoints*100:.1f}%)")
    print(f"⚠️  Still Stubs: {stub_count}")
    print(f"❌ Not Found: {missing_count}")
    print()

    # CLI Tools Summary
    print("CLI TOOLS REQUIRED:")
    cli_tools = set(r["cli_tool"] for r in all_results)
    for tool in sorted(cli_tools):
        endpoints_for_tool = [r for r in all_results if r["cli_tool"] == tool]
        impl_for_tool = [r for r in endpoints_for_tool if r["status"] == "✅ IMPLEMENTED"]
        print(f"  • {tool}: {len(impl_for_tool)}/{len(endpoints_for_tool)} endpoints implemented")

    print()

    # Exit code
    if stub_count > 0 or missing_count > 0:
        print("⚠️  WARNING: Some endpoints are not fully implemented")
        return 1
    else:
        print("✅ SUCCESS: All CLI integration endpoints are implemented!")
        return 0


if __name__ == "__main__":
    sys.exit(main())
