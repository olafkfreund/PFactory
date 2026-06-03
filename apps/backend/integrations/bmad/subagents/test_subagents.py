"""
Sub-Agent Tests
================

Simple tests to verify sub-agent functionality.
"""

from pathlib import Path

from .codebase_analyzer import CodebaseAnalyzer
from .requirements_analyst import RequirementsAnalyst
from .technical_evaluator import TechnicalEvaluator


def test_requirements_analyst():
    """Test RequirementsAnalyst sub-agent."""
    print("\n" + "=" * 70)
    print("TEST: RequirementsAnalyst")
    print("=" * 70)

    project_dir = Path.cwd()
    analyst = RequirementsAnalyst(project_dir=project_dir)

    # Test with complete requirements
    result = analyst.analyze({
        "requirements": """
        As a user, I want to log in with email and password so that I can access my account.

        Acceptance Criteria:
        - AC1: Login form accepts email and password
        - AC2: Valid credentials redirect to dashboard
        - AC3: Invalid credentials show error message
        - AC4: Passwords must be hashed using bcrypt
        """,
        "context": "User authentication feature"
    })

    assert result.success, "Analysis should succeed"
    assert result.data['completeness_score'] > 0.5, "Should detect reasonable completeness"
    assert result.confidence > 0.5, "Should have reasonable confidence"

    print(f"✓ Completeness: {result.data['completeness_score']:.0%}")
    print(f"✓ Clarity: {result.data['clarity_score']:.0%}")
    print(f"✓ Confidence: {result.confidence:.0%}")

    if result.data['questions']:
        print(f"✓ Generated {len(result.data['questions'])} clarification questions")

    print("\n✅ RequirementsAnalyst test passed!")


def test_codebase_analyzer():
    """Test CodebaseAnalyzer sub-agent."""
    print("\n" + "=" * 70)
    print("TEST: CodebaseAnalyzer")
    print("=" * 70)

    project_dir = Path.cwd()
    analyzer = CodebaseAnalyzer(project_dir=project_dir)

    result = analyzer.analyze({
        "task": "Add user authentication",
        "search_terms": ["auth", "user", "login"],
        "max_depth": 3
    })

    assert result.success, "Analysis should succeed"
    assert "tech_stack" in result.data, "Should detect tech stack"
    assert "project_structure" in result.data, "Should analyze structure"

    print(f"✓ Tech Stack: {', '.join(result.data['tech_stack']) or 'None detected'}")
    print(f"✓ Total Files: {result.data['project_structure'].get('total_files', 0)}")
    print(f"✓ Relevant Files: {len(result.data['relevant_files'])}")
    print(f"✓ Entry Points: {len(result.data['entry_points'])}")

    if result.data['relevant_files']:
        print(f"  Sample files: {result.data['relevant_files'][:3]}")

    print("\n✅ CodebaseAnalyzer test passed!")


def test_technical_evaluator():
    """Test TechnicalEvaluator sub-agent."""
    print("\n" + "=" * 70)
    print("TEST: TechnicalEvaluator")
    print("=" * 70)

    project_dir = Path.cwd()
    evaluator = TechnicalEvaluator(project_dir=project_dir)

    result = evaluator.analyze({
        "decision": "Use JWT tokens for authentication with session management",
        "context": "Web application with REST API",
        "alternatives": [
            "Server-side sessions with Redis",
            "Cookie-based sessions"
        ],
        "constraints": ["Must support OAuth 2.0"]
    })

    assert result.success, "Evaluation should succeed"
    assert "risk_level" in result.data, "Should assess risk level"
    assert result.data['risk_level'] in ["low", "medium", "high"], "Valid risk level"

    print(f"✓ Risk Level: {result.data['risk_level']}")
    print(f"✓ Risk Factors: {len(result.data['risk_factors'])}")
    print(f"✓ Pros: {len(result.data['pros'])}")
    print(f"✓ Cons: {len(result.data['cons'])}")
    print(f"✓ Security Concerns: {len(result.data['security_concerns'])}")
    print(f"✓ Best Practices: {len(result.data['best_practices'])}")

    if result.data['security_concerns']:
        print(f"  Sample security concern: {result.data['security_concerns'][0]}")

    print("\n✅ TechnicalEvaluator test passed!")


def test_all():
    """Run all sub-agent tests."""
    print("\n" + "=" * 70)
    print("RUNNING ALL SUB-AGENT TESTS")
    print("=" * 70)

    try:
        test_requirements_analyst()
        test_codebase_analyzer()
        test_technical_evaluator()

        print("\n" + "=" * 70)
        print("✅ ALL TESTS PASSED!")
        print("=" * 70)
        print("\nSub-agents are working correctly and ready for integration.")

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        return False
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True


if __name__ == "__main__":
    test_all()
