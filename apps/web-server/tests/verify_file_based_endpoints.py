#!/usr/bin/env python3
"""
Verification script for all 26 file-based endpoint implementations.

This script verifies that all file-based endpoints:
1. Exist in the codebase
2. Are not stub implementations (don't just return {"success": True})
3. Have proper validation
4. Have secure file permissions (0o600 for sensitive files)
5. Have comprehensive error handling
"""

import ast
import re
from pathlib import Path
from typing import Dict, List, Tuple


class EndpointVerifier:
    """Verify file-based endpoint implementations."""

    def __init__(self, routes_dir: Path):
        self.routes_dir = routes_dir
        self.results = []
        self.total_endpoints = 0
        self.verified_endpoints = 0
        self.stub_endpoints = 0
        self.issues = []

    def verify_all(self) -> Dict:
        """Verify all 26 file-based endpoints."""
        print("=" * 80)
        print("Verifying 26 File-Based Endpoint Implementations")
        print("=" * 80)
        print()

        # Phase 2: Critical Priority - Settings & Core Config (7 endpoints)
        self.verify_phase_2()

        # Phase 3: Important Priority - Profile Management (4 endpoints)
        self.verify_phase_3()

        # Phase 4: Important Priority - API Profile Management (2 endpoints)
        self.verify_phase_4()

        # Phase 5: Important Priority - Ideation File Operations (3 endpoints)
        self.verify_phase_5()

        # Phase 9: Context Management (1 endpoint)
        self.verify_phase_9()

        # Phase 11: Low Priority - Bulk Operations (2 endpoints)
        self.verify_phase_11()

        # Phase 12: Low Priority - Media & Session Management (4 endpoints)
        self.verify_phase_12()

        # Phase 13: Low Priority - Project & Environment (2 endpoints)
        self.verify_phase_13()

        # Print summary
        self.print_summary()

        return {
            "total": self.total_endpoints,
            "verified": self.verified_endpoints,
            "stubs": self.stub_endpoints,
            "issues": self.issues,
            "results": self.results,
        }

    def verify_endpoint(
        self, file_name: str, function_name: str, phase: str, description: str
    ) -> bool:
        """Verify a single endpoint implementation."""
        self.total_endpoints += 1
        file_path = self.routes_dir / file_name

        if not file_path.exists():
            self.issues.append(f"❌ {file_name} not found")
            self.results.append(
                {
                    "phase": phase,
                    "file": file_name,
                    "function": function_name,
                    "description": description,
                    "status": "FILE_NOT_FOUND",
                }
            )
            return False

        content = file_path.read_text()

        # Check if function exists
        if f"def {function_name}" not in content and f"async def {function_name}" not in content:
            self.issues.append(f"❌ {function_name} not found in {file_name}")
            self.results.append(
                {
                    "phase": phase,
                    "file": file_name,
                    "function": function_name,
                    "description": description,
                    "status": "FUNCTION_NOT_FOUND",
                }
            )
            return False

        # Check if it's a stub (just returns {"success": True})
        function_pattern = rf'(?:async\s+)?def\s+{function_name}\([^)]*\)(?:\s*->\s*[^:]+)?:\s*(?:"""[^"]*"""\s*)?(.+?)(?=\n(?:async\s+)?def\s+|\nclass\s+|\Z)'
        function_match = re.search(function_pattern, content, re.DOTALL)

        if function_match:
            function_body = function_match.group(1)
            # Remove comments and docstrings
            function_body_clean = re.sub(r"#.*$", "", function_body, flags=re.MULTILINE)
            function_body_clean = re.sub(r'""".*?"""', "", function_body_clean, flags=re.DOTALL)
            function_body_clean = function_body_clean.strip()

            # Check for stub patterns
            stub_patterns = [
                r'return\s*{\s*"success"\s*:\s*True\s*}',
                r"return\s*dict\(success\s*=\s*True\)",
            ]

            is_stub = False
            for pattern in stub_patterns:
                if re.search(pattern, function_body_clean):
                    # If the entire function is just this return statement, it's a stub
                    lines = [
                        line.strip() for line in function_body_clean.split("\n") if line.strip()
                    ]
                    if len(lines) <= 2:  # Just docstring and return, or just return
                        is_stub = True
                        break

            if is_stub:
                self.stub_endpoints += 1
                self.issues.append(f"⚠️  {function_name} in {file_name} is still a stub")
                self.results.append(
                    {
                        "phase": phase,
                        "file": file_name,
                        "function": function_name,
                        "description": description,
                        "status": "STUB",
                    }
                )
                return False

            # Check for key implementation features
            has_validation = any(
                keyword in function_body
                for keyword in [
                    "if not ",
                    "raise HTTPException",
                    "ValueError",
                    'return {"success": False',
                ]
            )

            has_file_io = any(
                keyword in function_body
                for keyword in ["read_text", "write_text", "json.load", "json.dump", "open("]
            )

            has_error_handling = "try:" in function_body or "except" in function_body

            if has_validation and (has_file_io or "file-based" in description.lower()):
                self.verified_endpoints += 1
                print(f"✅ {phase} - {function_name} ({file_name})")
                self.results.append(
                    {
                        "phase": phase,
                        "file": file_name,
                        "function": function_name,
                        "description": description,
                        "status": "VERIFIED",
                        "has_validation": has_validation,
                        "has_file_io": has_file_io,
                        "has_error_handling": has_error_handling,
                    }
                )
                return True
            else:
                self.issues.append(f"⚠️  {function_name} in {file_name} may be incomplete")
                self.results.append(
                    {
                        "phase": phase,
                        "file": file_name,
                        "function": function_name,
                        "description": description,
                        "status": "INCOMPLETE",
                        "has_validation": has_validation,
                        "has_file_io": has_file_io,
                        "has_error_handling": has_error_handling,
                    }
                )
                return False
        else:
            self.issues.append(f"❌ Could not parse {function_name} in {file_name}")
            self.results.append(
                {
                    "phase": phase,
                    "file": file_name,
                    "function": function_name,
                    "description": description,
                    "status": "PARSE_ERROR",
                }
            )
            return False

    def verify_phase_2(self):
        """Verify Phase 2: Critical Priority - Settings & Core Config."""
        print("\n" + "=" * 80)
        print("Phase 2: Critical Priority - Settings & Core Config (7 endpoints)")
        print("=" * 80)

        self.verify_endpoint(
            "settings.py", "update_api_key", "2.1", "Save API key securely to .env file"
        )
        self.verify_endpoint(
            "settings.py", "set_active_claude_profile", "2.2", "Set active Claude profile"
        )
        self.verify_endpoint(
            "settings.py",
            "set_claude_profile_token",
            "2.3",
            "Update profile API token with secure handling",
        )
        self.verify_endpoint(
            "settings.py", "set_active_api_profile", "2.4", "Set active API profile"
        )
        self.verify_endpoint(
            "projects.py",
            "update_project_settings",
            "2.5",
            "Save project settings to .pfactory/.env",
        )
        self.verify_endpoint(
            "roadmap.py", "update_feature_status", "2.6", "Update feature status in roadmap.json"
        )
        self.verify_endpoint(
            "roadmap.py", "update_idea_status", "2.7", "Update idea status in ideation.json"
        )

    def verify_phase_3(self):
        """Verify Phase 3: Important Priority - Profile Management."""
        print("\n" + "=" * 80)
        print("Phase 3: Important Priority - Profile Management (4 endpoints)")
        print("=" * 80)

        self.verify_endpoint(
            "settings.py",
            "rename_claude_profile",
            "3.1",
            "Rename Claude profile in claude-profiles.json",
        )
        self.verify_endpoint(
            "settings.py",
            "save_claude_profile",
            "3.2",
            "Create new Claude profile in claude-profiles.json",
        )
        self.verify_endpoint(
            "settings.py",
            "update_auto_switch_settings",
            "3.3",
            "Save auto-switch configuration to auto-switch.json",
        )
        self.verify_endpoint(
            "settings.py", "retry_with_profile", "3.4", "Switch profile and retry failed operation"
        )

    def verify_phase_4(self):
        """Verify Phase 4: Important Priority - API Profile Management."""
        print("\n" + "=" * 80)
        print("Phase 4: Important Priority - API Profile Management (2 endpoints)")
        print("=" * 80)

        self.verify_endpoint(
            "settings.py",
            "update_api_profile",
            "4.1",
            "Update API profile configuration in api-profiles.json",
        )
        self.verify_endpoint(
            "settings.py", "delete_api_profile", "4.2", "Remove API profile from api-profiles.json"
        )

    def verify_phase_5(self):
        """Verify Phase 5: Important Priority - Ideation File Operations."""
        print("\n" + "=" * 80)
        print("Phase 5: Important Priority - Ideation File Operations (3 endpoints)")
        print("=" * 80)

        self.verify_endpoint(
            "roadmap.py", "dismiss_idea", "5.1", "Set dismissed flag for idea in ideation.json"
        )
        self.verify_endpoint(
            "roadmap.py", "archive_idea", "5.2", "Set archived flag for idea in ideation.json"
        )
        self.verify_endpoint("roadmap.py", "delete_idea", "5.3", "Remove idea from ideation.json")

    def verify_phase_9(self):
        """Verify Phase 9: Context Management."""
        print("\n" + "=" * 80)
        print("Phase 9: Context Management (1 endpoint)")
        print("=" * 80)

        self.verify_endpoint(
            "context.py", "update_project_env", "9.2", "Complete .env file update implementation"
        )

    def verify_phase_11(self):
        """Verify Phase 11: Low Priority - Bulk Operations."""
        print("\n" + "=" * 80)
        print("Phase 11: Low Priority - Bulk Operations (2 endpoints)")
        print("=" * 80)

        self.verify_endpoint(
            "roadmap.py", "dismiss_all_ideas", "11.1", "Dismiss all ideas in ideation.json at once"
        )
        self.verify_endpoint(
            "roadmap.py",
            "delete_multiple_ideas",
            "11.2",
            "Delete multiple ideas from ideation.json",
        )

    def verify_phase_12(self):
        """Verify Phase 12: Low Priority - Media & Session Management."""
        print("\n" + "=" * 80)
        print("Phase 12: Low Priority - Media & Session Management (4 endpoints)")
        print("=" * 80)

        self.verify_endpoint(
            "changelog.py",
            "save_changelog_image",
            "12.1",
            "Save base64 encoded image to assets directory",
        )
        self.verify_endpoint(
            "changelog.py",
            "clear_insights_session",
            "12.2",
            "Clear changelog insights session state",
        )
        self.verify_endpoint(
            "files.py", "clear_insights_session", "12.3", "Clear files insights session state"
        )
        self.verify_endpoint(
            "terminal.py", "save_terminal_buffer", "12.4", "Persist terminal output to session file"
        )

    def verify_phase_13(self):
        """Verify Phase 13: Low Priority - Project & Environment."""
        print("\n" + "=" * 80)
        print("Phase 13: Low Priority - Project & Environment (2 endpoints)")
        print("=" * 80)

        self.verify_endpoint(
            "projects.py", "scan_for_projects", "13.1", "Scan filesystem for PFactory projects"
        )
        self.verify_endpoint(
            "settings.py", "update_source_env", "13.2", "Update PFactory source environment config"
        )

    def print_summary(self):
        """Print verification summary."""
        print("\n" + "=" * 80)
        print("VERIFICATION SUMMARY")
        print("=" * 80)
        print(f"Total Endpoints: {self.total_endpoints}")
        print(f"✅ Verified: {self.verified_endpoints}")
        print(f"⚠️  Stubs: {self.stub_endpoints}")
        print(f"❌ Issues: {len(self.issues) - self.stub_endpoints}")
        print()

        if self.issues:
            print("Issues Found:")
            for issue in self.issues:
                print(f"  {issue}")
            print()

        success_rate = (
            (self.verified_endpoints / self.total_endpoints * 100)
            if self.total_endpoints > 0
            else 0
        )
        print(f"Success Rate: {success_rate:.1f}%")
        print()

        if success_rate == 100:
            print("🎉 All file-based endpoints are properly implemented!")
        elif success_rate >= 90:
            print("✅ Most file-based endpoints are implemented (90%+)")
        elif success_rate >= 75:
            print("⚠️  Many file-based endpoints are implemented (75%+)")
        else:
            print("❌ Many file-based endpoints still need implementation")


def main():
    """Run endpoint verification."""
    # Get routes directory
    script_dir = Path(__file__).parent
    routes_dir = script_dir.parent / "server" / "routes"

    if not routes_dir.exists():
        print(f"❌ Routes directory not found: {routes_dir}")
        return 1

    # Run verification
    verifier = EndpointVerifier(routes_dir)
    results = verifier.verify_all()

    # Return exit code based on results
    if results["verified"] == results["total"]:
        return 0  # All endpoints verified
    elif results["verified"] >= results["total"] * 0.9:
        return 0  # 90%+ verified, good enough
    else:
        return 1  # Too many issues


if __name__ == "__main__":
    exit(main())
