#!/usr/bin/env python3
"""
Verification script for performance, file locking, concurrent access, and API rate limit tests.

This script:
1. Runs all performance test classes
2. Validates test coverage
3. Generates a summary report
4. Checks for common issues (file corruption, race conditions, deadlocks)
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List


def run_pytest_tests() -> Dict[str, any]:
    """Run pytest tests and capture results."""
    print("=" * 80)
    print("RUNNING PERFORMANCE TESTS")
    print("=" * 80)
    print()

    test_file = Path(__file__).parent / "test_performance.py"

    # Run pytest with detailed output
    result = subprocess.run(
        ["pytest", str(test_file), "-v", "-s", "--tb=short"],
        capture_output=True,
        text=True
    )

    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr
    }


def analyze_test_output(output: str) -> Dict[str, any]:
    """Analyze pytest output to extract test results."""
    lines = output.split("\n")

    results = {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "file_locking_tests": 0,
        "concurrent_access_tests": 0,
        "rate_limit_tests": 0,
        "benchmark_tests": 0,
        "test_details": []
    }

    for line in lines:
        if "PASSED" in line:
            results["passed"] += 1
            results["total"] += 1

            # Categorize by test type
            if "test_concurrent_profile" in line or "test_concurrent_api" in line or "test_concurrent_ideation" in line:
                results["file_locking_tests"] += 1
            elif "test_concurrent_read" in line or "test_concurrent_mixed" in line or "test_concurrent_different" in line:
                results["concurrent_access_tests"] += 1
            elif "test_profile_switch" in line or "test_cascade" in line or "test_concurrent_rate" in line or "test_rate_limit_with_retry" in line:
                results["rate_limit_tests"] += 1
            elif "test_throughput" in line or "test_latency" in line:
                results["benchmark_tests"] += 1

            results["test_details"].append({"name": line.strip(), "status": "PASSED"})

        elif "FAILED" in line:
            results["failed"] += 1
            results["total"] += 1
            results["test_details"].append({"name": line.strip(), "status": "FAILED"})

        elif "SKIPPED" in line:
            results["skipped"] += 1
            results["total"] += 1
            results["test_details"].append({"name": line.strip(), "status": "SKIPPED"})

    return results


def print_summary(results: Dict[str, any]):
    """Print test summary."""
    print()
    print("=" * 80)
    print("PERFORMANCE TEST SUMMARY")
    print("=" * 80)
    print()

    print(f"Total Tests: {results['total']}")
    print(f"  ✅ Passed: {results['passed']}")
    print(f"  ❌ Failed: {results['failed']}")
    print(f"  ⏭️  Skipped: {results['skipped']}")
    print()

    print("Test Coverage by Category:")
    print(f"  🔒 File Locking Tests: {results['file_locking_tests']}")
    print(f"  🔄 Concurrent Access Tests: {results['concurrent_access_tests']}")
    print(f"  🚦 Rate Limit Tests: {results['rate_limit_tests']}")
    print(f"  ⚡ Performance Benchmarks: {results['benchmark_tests']}")
    print()

    if results["failed"] > 0:
        print("❌ FAILED TESTS:")
        for test in results["test_details"]:
            if test["status"] == "FAILED":
                print(f"   - {test['name']}")
        print()

    # Overall status
    if results["failed"] == 0 and results["total"] > 0:
        print("✅ ALL PERFORMANCE TESTS PASSED!")
        print()
        print("Summary:")
        print("  ✅ File locking works correctly under concurrent writes")
        print("  ✅ Concurrent API access completes successfully")
        print("  ✅ Rate limit handling and profile switching works")
        print("  ✅ Performance benchmarks meet requirements")
    elif results["total"] == 0:
        print("⚠️  WARNING: No tests were run!")
    else:
        print(f"❌ {results['failed']} test(s) failed. See details above.")

    print()


def check_test_coverage():
    """Verify all required test scenarios are covered."""
    print("=" * 80)
    print("TEST COVERAGE VERIFICATION")
    print("=" * 80)
    print()

    test_file = Path(__file__).parent / "test_performance.py"
    content = test_file.read_text()

    required_scenarios = {
        "File Locking": [
            "test_concurrent_profile_updates",
            "test_concurrent_api_profile_creation",
            "test_concurrent_ideation_updates"
        ],
        "Concurrent Access": [
            "test_concurrent_read_operations",
            "test_concurrent_mixed_operations",
            "test_concurrent_different_endpoints"
        ],
        "Rate Limits": [
            "test_profile_switch_on_rate_limit",
            "test_cascade_profile_switches",
            "test_concurrent_rate_limit_handling",
            "test_rate_limit_with_retry_logic"
        ],
        "Performance": [
            "test_throughput_profile_reads",
            "test_throughput_profile_writes",
            "test_latency_under_load"
        ]
    }

    all_covered = True
    for category, scenarios in required_scenarios.items():
        print(f"{category}:")
        for scenario in scenarios:
            if f"def {scenario}" in content:
                print(f"  ✅ {scenario}")
            else:
                print(f"  ❌ {scenario} - MISSING!")
                all_covered = False
        print()

    if all_covered:
        print("✅ ALL REQUIRED TEST SCENARIOS ARE IMPLEMENTED")
    else:
        print("❌ SOME REQUIRED TEST SCENARIOS ARE MISSING")

    print()
    return all_covered


def main():
    """Main verification function."""
    print()
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 15 + "PERFORMANCE TEST VERIFICATION" + " " * 34 + "║")
    print("╚" + "=" * 78 + "╝")
    print()

    # Check test coverage
    coverage_ok = check_test_coverage()

    # Run tests
    test_results = run_pytest_tests()

    # Analyze results
    results = analyze_test_output(test_results["stdout"])

    # Print summary
    print_summary(results)

    # Print stderr if there were errors
    if test_results["stderr"] and test_results["returncode"] != 0:
        print("=" * 80)
        print("TEST ERRORS")
        print("=" * 80)
        print(test_results["stderr"])
        print()

    # Exit code
    if test_results["returncode"] == 0 and coverage_ok:
        print("✅ VERIFICATION COMPLETE - ALL CHECKS PASSED")
        return 0
    else:
        print("❌ VERIFICATION FAILED - SEE ERRORS ABOVE")
        return 1


if __name__ == "__main__":
    sys.exit(main())
