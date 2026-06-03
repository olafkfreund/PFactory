"""
Sub-Agent Invocation Examples
===============================

Example patterns for invoking sub-agents from main agents.
Shows how planner, architect, and coder agents can use sub-agents.
"""

from pathlib import Path
from typing import Any

from .codebase_analyzer import CodebaseAnalyzer
from .requirements_analyst import RequirementsAnalyst
from .technical_evaluator import TechnicalEvaluator


def example_requirements_analysis(project_dir: Path, requirements: str) -> dict[str, Any]:
    """Example: Analyze requirements before planning.

    Use case: Planner agent validates requirements before creating stories.
    """
    analyst = RequirementsAnalyst(project_dir=project_dir)

    result = analyst.analyze({
        "requirements": requirements,
        "context": "New feature for user authentication",
        "check_feasibility": True
    })

    if result.success:
        print("Requirements Analysis:")
        print(f"  Completeness: {result.data['completeness_score']:.0%}")
        print(f"  Clarity: {result.data['clarity_score']:.0%}")
        print(f"  Confidence: {result.confidence:.0%}")

        if result.issues:
            print("\n  Issues:")
            for issue in result.issues:
                print(f"    - {issue}")

        if result.data['questions']:
            print("\n  Questions to resolve:")
            for q in result.data['questions'][:5]:
                print(f"    - {q}")

    return result.data


def example_codebase_exploration(project_dir: Path, task: str) -> dict[str, Any]:
    """Example: Explore codebase before implementing feature.

    Use case: Coder agent finds relevant files before starting implementation.
    """
    analyzer = CodebaseAnalyzer(project_dir=project_dir)

    result = analyzer.analyze({
        "task": task,
        "search_terms": ["auth", "login", "user"],
        "max_depth": 5
    })

    if result.success:
        print("Codebase Analysis:")
        print(f"  Tech Stack: {', '.join(result.data['tech_stack'])}")
        print(f"  Relevant Files: {len(result.data['relevant_files'])}")
        print(f"  Entry Points: {len(result.data['entry_points'])}")

        if result.data['relevant_files']:
            print("\n  Top relevant files:")
            for file in result.data['relevant_files'][:5]:
                print(f"    - {file}")

        if result.recommendations:
            print("\n  Recommendations:")
            for rec in result.recommendations:
                print(f"    - {rec}")

    return result.data


def example_technical_evaluation(
    project_dir: Path,
    decision: str,
    alternatives: list = None
) -> dict[str, Any]:
    """Example: Evaluate technical decision before implementation.

    Use case: Architect agent evaluates database choice, API design, etc.
    """
    evaluator = TechnicalEvaluator(project_dir=project_dir)

    result = evaluator.analyze({
        "decision": decision,
        "context": "Building authentication system for web application",
        "alternatives": alternatives or [],
        "constraints": ["Must support OAuth 2.0", "Should scale to 10k users"]
    })

    if result.success:
        print("Technical Evaluation:")
        print(f"  Risk Level: {result.data['risk_level']}")
        print(f"  Confidence: {result.confidence:.0%}")

        if result.data['pros']:
            print("\n  Pros:")
            for pro in result.data['pros'][:3]:
                print(f"    + {pro}")

        if result.data['cons']:
            print("\n  Cons:")
            for con in result.data['cons'][:3]:
                print(f"    - {con}")

        if result.data['security_concerns']:
            print("\n  Security Concerns:")
            for concern in result.data['security_concerns'][:3]:
                print(f"    ⚠ {concern}")

        if result.data['best_practices']:
            print("\n  Best Practices:")
            for bp in result.data['best_practices'][:3]:
                print(f"    → {bp}")

    return result.data


def example_combined_workflow(project_dir: Path):
    """Example: Combined workflow using multiple sub-agents.

    Use case: Main agent orchestrates multiple sub-agents for comprehensive analysis.
    """
    print("=" * 70)
    print("COMBINED SUB-AGENT WORKFLOW EXAMPLE")
    print("=" * 70)

    # Step 1: Analyze requirements
    print("\n1. REQUIREMENTS ANALYSIS")
    print("-" * 70)
    requirements = """
    As a user, I want to log in with email and password so that I can access my account.
    The system should support session management and remember me functionality.
    """

    req_result = example_requirements_analysis(project_dir, requirements)

    # Only proceed if requirements are good enough
    if req_result.get('completeness_score', 0) < 0.5:
        print("\n❌ Requirements too incomplete - stopping workflow")
        return

    # Step 2: Explore codebase
    print("\n2. CODEBASE EXPLORATION")
    print("-" * 70)
    code_result = example_codebase_exploration(
        project_dir,
        "Add user authentication with email/password login"
    )

    # Step 3: Evaluate technical decision
    print("\n3. TECHNICAL EVALUATION")
    print("-" * 70)
    tech_result = example_technical_evaluation(
        project_dir,
        "Use JWT tokens for session management",
        alternatives=[
            "Use server-side sessions with Redis",
            "Use cookie-based sessions"
        ]
    )

    # Step 4: Generate summary
    print("\n4. WORKFLOW SUMMARY")
    print("-" * 70)
    print(f"✓ Requirements analyzed (completeness: {req_result.get('completeness_score', 0):.0%})")
    print(f"✓ Found {len(code_result.get('relevant_files', []))} relevant files")
    print(f"✓ Technical decision evaluated (risk: {tech_result.get('risk_level', 'unknown')})")
    print("\n✓ Ready to proceed with implementation")


# Integration pattern for main agents
class SubAgentInvoker:
    """Helper class for main agents to invoke sub-agents.

    Usage in main agent:
        from integrations.bmad.subagents.examples import SubAgentInvoker

        invoker = SubAgentInvoker(project_dir, spec_dir)

        # Before planning
        req_analysis = invoker.analyze_requirements(requirements_text)
        if req_analysis.issues:
            # Handle issues before continuing

        # Before implementation
        relevant_files = invoker.find_relevant_files(task_description)
        # Use relevant_files in context

        # For architecture decisions
        eval_result = invoker.evaluate_decision(decision_text)
        if eval_result.data['risk_level'] == 'high':
            # Request user confirmation
    """

    def __init__(self, project_dir: Path, spec_dir: Path = None):
        self.project_dir = project_dir
        self.spec_dir = spec_dir

    def analyze_requirements(self, requirements: str, context: str = "") -> 'SubAgentResult':
        """Analyze requirements using RequirementsAnalyst."""
        analyst = RequirementsAnalyst(self.project_dir, self.spec_dir)
        return analyst.analyze({
            "requirements": requirements,
            "context": context,
            "check_feasibility": True
        })

    def find_relevant_files(self, task: str, search_terms: list = None) -> 'SubAgentResult':
        """Find relevant files using CodebaseAnalyzer."""
        analyzer = CodebaseAnalyzer(self.project_dir, self.spec_dir)
        return analyzer.analyze({
            "task": task,
            "search_terms": search_terms or [],
            "max_depth": 5
        })

    def evaluate_decision(
        self,
        decision: str,
        context: str = "",
        alternatives: list = None
    ) -> 'SubAgentResult':
        """Evaluate technical decision using TechnicalEvaluator."""
        evaluator = TechnicalEvaluator(self.project_dir, self.spec_dir)
        return evaluator.analyze({
            "decision": decision,
            "context": context,
            "alternatives": alternatives or [],
            "constraints": []
        })


if __name__ == "__main__":
    # Run example workflow
    example_project = Path.cwd()
    example_combined_workflow(example_project)
