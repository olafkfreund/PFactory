"""
Verification script for end-to-end workflow tests.

This script validates that all critical workflows are properly tested
and that the test suite covers realistic user scenarios.
"""

import ast
import json
from pathlib import Path
from typing import Dict, List, Set


def analyze_test_file(file_path: Path) -> Dict:
    """Analyze test file and extract workflow information."""
    with open(file_path, 'r') as f:
        tree = ast.parse(f.read())

    workflows = []
    test_classes = []
    test_methods = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name.startswith('Test'):
            test_classes.append(node.name)
            class_workflows = []

            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name.startswith('test_'):
                    test_methods.append(f"{node.name}.{item.name}")
                    class_workflows.append(item.name)

                    # Extract docstring to understand workflow steps
                    docstring = ast.get_docstring(item)
                    if docstring:
                        steps = []
                        for line in docstring.split('\n'):
                            line = line.strip()
                            if line and line[0].isdigit() and '.' in line:
                                steps.append(line)

                        workflows.append({
                            'class': node.name,
                            'method': item.name,
                            'steps': steps,
                            'description': docstring.split('\n')[0]
                        })

    return {
        'test_classes': test_classes,
        'test_methods': test_methods,
        'workflows': workflows,
        'total_workflows': len(workflows),
        'total_test_classes': len(test_classes),
        'total_test_methods': len(test_methods)
    }


def extract_endpoints_used(workflows: List[Dict]) -> Set[str]:
    """Extract endpoint names mentioned in workflow tests."""
    endpoints = set()

    # Known endpoints from implementation
    known_endpoints = [
        # Profile Management
        'save_claude_profile', 'set_claude_profile_token', 'rename_claude_profile',
        'set_active_claude_profile', 'retry_with_profile',
        'update_api_profile', 'delete_api_profile', 'set_active_api_profile',

        # Ideation & Roadmap
        'generate_ideation', 'refresh_ideation', 'stop_ideation',
        'update_idea_status', 'dismiss_idea', 'archive_idea', 'delete_idea',
        'delete_multiple_ideas', 'dismiss_all_ideas',
        'update_feature_status',

        # GitLab
        'investigate_gitlab_issue', 'update_merge_request', 'assign_merge_request',
        'approve_merge_request', 'merge_merge_request', 'post_merge_request_note',
        'run_mr_review', 'post_mr_review', 'followup_mr_review',

        # GitHub
        'investigate_github_issue',

        # Git Operations
        'squash_commits', 'create_worktree', 'create_release', 'download_source_update',

        # Projects
        'scan_for_projects', 'update_project_settings', 'update_project_env',

        # Settings
        'update_api_key', 'update_auto_switch_settings', 'update_source_env',

        # Context
        'invoke_claude_setup',

        # Changelog & Files
        'save_changelog_image', 'clear_insights_session',

        # Terminal
        'save_terminal_buffer'
    ]

    for workflow in workflows:
        for step in workflow.get('steps', []):
            for endpoint in known_endpoints:
                if endpoint in step.lower():
                    endpoints.add(endpoint)

    return endpoints


def generate_coverage_matrix(workflows: List[Dict]) -> Dict[str, List[str]]:
    """Generate coverage matrix showing which workflows test which features."""
    coverage = {
        'profile_management': [],
        'ideation_roadmap': [],
        'gitlab_integration': [],
        'github_integration': [],
        'git_operations': [],
        'project_setup': [],
        'settings_configuration': [],
        'error_handling': []
    }

    for workflow in workflows:
        class_name = workflow['class'].lower()
        method_name = workflow['method'].lower()

        if 'profile' in class_name or 'profile' in method_name:
            coverage['profile_management'].append(workflow['method'])

        if 'ideation' in class_name or 'roadmap' in class_name:
            coverage['ideation_roadmap'].append(workflow['method'])

        if 'gitlab' in class_name:
            coverage['gitlab_integration'].append(workflow['method'])

        if 'github' in class_name:
            coverage['github_integration'].append(workflow['method'])

        if 'git' in class_name and 'gitlab' not in class_name:
            coverage['git_operations'].append(workflow['method'])

        if 'project' in class_name or 'setup' in method_name:
            coverage['project_setup'].append(workflow['method'])

        if 'settings' in class_name or 'configuration' in class_name:
            coverage['settings_configuration'].append(workflow['method'])

        if 'error' in class_name or 'recovery' in method_name:
            coverage['error_handling'].append(workflow['method'])

    return coverage


def main():
    """Main verification function."""
    test_file = Path(__file__).parent / "test_e2e_workflows.py"

    if not test_file.exists():
        print(f"❌ Test file not found: {test_file}")
        return False

    print("=" * 80)
    print("END-TO-END WORKFLOW TEST VERIFICATION")
    print("=" * 80)
    print()

    # Analyze test file
    analysis = analyze_test_file(test_file)

    print(f"📊 Test Statistics:")
    print(f"  - Test Classes: {analysis['total_test_classes']}")
    print(f"  - Test Methods: {analysis['total_test_methods']}")
    print(f"  - Documented Workflows: {analysis['total_workflows']}")
    print()

    # Show workflows
    print(f"📋 Workflows Tested:")
    print()
    for workflow in analysis['workflows']:
        print(f"  {workflow['class']}.{workflow['method']}")
        print(f"    Description: {workflow['description']}")
        print(f"    Steps: {len(workflow['steps'])} documented steps")
        print()

    # Show coverage
    coverage = generate_coverage_matrix(analysis['workflows'])
    print(f"🎯 Coverage by Category:")
    print()
    for category, tests in coverage.items():
        category_name = category.replace('_', ' ').title()
        status = "✅" if tests else "⚠️"
        print(f"  {status} {category_name}: {len(tests)} workflow(s)")
        for test in tests:
            print(f"      - {test}")
    print()

    # Extract endpoints used
    endpoints = extract_endpoints_used(analysis['workflows'])
    print(f"🔗 Endpoints Tested in Workflows: {len(endpoints)}")
    for endpoint in sorted(endpoints):
        print(f"  - {endpoint}")
    print()

    # Overall assessment
    total_categories = len(coverage)
    covered_categories = sum(1 for tests in coverage.values() if tests)

    print("=" * 80)
    print(f"📈 Overall Coverage: {covered_categories}/{total_categories} categories covered")

    if covered_categories >= total_categories * 0.75:
        print("✅ VERIFICATION PASSED - Good workflow coverage")
        return True
    else:
        print("⚠️ VERIFICATION WARNING - Some workflow categories need more tests")
        return False


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
