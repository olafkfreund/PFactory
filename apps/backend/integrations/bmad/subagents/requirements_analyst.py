"""
Requirements Analyst Sub-Agent
================================

Analyzes and validates requirements for completeness, clarity, and feasibility.
Identifies gaps, ambiguities, and potential issues in requirements.
"""

import json
from typing import Any

from .base import SubAgent, SubAgentResult


class RequirementsAnalyst(SubAgent):
    """Sub-agent for requirements analysis and validation.

    Analyzes requirements to identify:
    - Completeness: Are all necessary details present?
    - Clarity: Are requirements unambiguous?
    - Feasibility: Are requirements technically achievable?
    - Conflicts: Do requirements contradict each other?
    - Missing context: What additional information is needed?

    Input data structure:
    {
        "requirements": str | dict,  # Raw requirements or structured data
        "context": str (optional),   # Additional context
        "check_feasibility": bool (optional, default=True)
    }

    Output data structure:
    {
        "completeness_score": float,  # 0.0-1.0
        "clarity_score": float,        # 0.0-1.0
        "missing_elements": List[str], # Missing requirement elements
        "ambiguous_items": List[str],  # Ambiguous requirements
        "conflicts": List[str],        # Conflicting requirements
        "questions": List[str]         # Questions for clarification
    }
    """

    @property
    def name(self) -> str:
        return "Requirements Analyst"

    @property
    def description(self) -> str:
        return "Analyzes requirements for completeness, clarity, and feasibility"

    def analyze(self, input_data: dict[str, Any]) -> SubAgentResult:
        """Analyze requirements for quality and completeness.

        Args:
            input_data: Dictionary with 'requirements' and optional 'context'

        Returns:
            SubAgentResult with analysis findings
        """
        requirements = input_data.get("requirements")
        context = input_data.get("context", "")
        check_feasibility = input_data.get("check_feasibility", True)

        if not requirements:
            return SubAgentResult(
                success=False,
                data={},
                reasoning="No requirements provided for analysis",
                confidence=0.0,
            )

        # Convert requirements to string if dict/list
        if isinstance(requirements, (dict, list)):
            requirements_text = json.dumps(requirements, indent=2)
        else:
            requirements_text = str(requirements)

        # Analyze completeness
        completeness_analysis = self._analyze_completeness(requirements_text)

        # Analyze clarity
        clarity_analysis = self._analyze_clarity(requirements_text)

        # Check for conflicts
        conflicts = self._detect_conflicts(requirements_text)

        # Generate clarification questions
        questions = self._generate_questions(requirements_text, context)

        # Calculate overall confidence
        confidence = (
            completeness_analysis["score"] + clarity_analysis["score"]
        ) / 2.0

        # Compile issues and recommendations
        issues = []
        recommendations = []

        if completeness_analysis["score"] < 0.7:
            issues.append(
                f"Requirements are {int((1 - completeness_analysis['score']) * 100)}% incomplete"
            )
            recommendations.append("Add missing requirement elements before proceeding")

        if clarity_analysis["score"] < 0.7:
            issues.append("Requirements contain ambiguities")
            recommendations.append("Clarify ambiguous requirements")

        if conflicts:
            issues.append(f"Found {len(conflicts)} conflicting requirements")
            recommendations.append("Resolve requirement conflicts")

        if questions:
            recommendations.append(
                f"Answer {len(questions)} clarification questions before implementation"
            )

        return SubAgentResult(
            success=True,
            data={
                "completeness_score": completeness_analysis["score"],
                "clarity_score": clarity_analysis["score"],
                "missing_elements": completeness_analysis["missing"],
                "ambiguous_items": clarity_analysis["ambiguous"],
                "conflicts": conflicts,
                "questions": questions,
            },
            reasoning=self._generate_reasoning(
                completeness_analysis, clarity_analysis, conflicts, questions
            ),
            confidence=confidence,
            issues=issues,
            recommendations=recommendations,
            metadata={
                "requirements_length": len(requirements_text),
                "context_provided": bool(context),
            },
        )

    def _analyze_completeness(self, requirements: str) -> dict[str, Any]:
        """Analyze requirements completeness.

        Checks for presence of essential elements:
        - User role/persona
        - Desired functionality
        - Expected outcome/benefit
        - Acceptance criteria
        - Technical constraints
        """
        required_elements = {
            "user_role": ["as a", "as an", "user", "role"],
            "functionality": ["want", "need", "should", "must"],
            "outcome": ["so that", "in order to", "to enable"],
            "acceptance_criteria": [
                "acceptance",
                "criteria",
                "ac",
                "when",
                "then",
                "given",
            ],
            "constraints": ["must not", "cannot", "limit", "constraint"],
        }

        requirements_lower = requirements.lower()
        missing = []
        found_count = 0

        for element, keywords in required_elements.items():
            if any(kw in requirements_lower for kw in keywords):
                found_count += 1
            else:
                missing.append(element.replace("_", " ").title())

        score = found_count / len(required_elements)

        return {"score": score, "missing": missing}

    def _analyze_clarity(self, requirements: str) -> dict[str, Any]:
        """Analyze requirements clarity.

        Identifies ambiguous terms and vague language:
        - Vague quantifiers (some, many, few)
        - Unclear references (it, they, this, that)
        - Subjective terms (good, bad, nice, user-friendly)
        """
        ambiguous_terms = [
            "some",
            "many",
            "few",
            "several",
            "various",
            "appropriate",
            "reasonable",
            "user-friendly",
            "intuitive",
            "easy",
            "fast",
            "good",
            "bad",
            "nice",
        ]

        requirements_lower = requirements.lower()
        ambiguous = []

        for term in ambiguous_terms:
            if term in requirements_lower:
                ambiguous.append(f"Vague term: '{term}'")

        # Penalize based on number of ambiguous terms
        penalty = min(len(ambiguous) * 0.1, 0.5)
        score = max(0.5, 1.0 - penalty)

        return {"score": score, "ambiguous": ambiguous}

    def _detect_conflicts(self, requirements: str) -> list[str]:
        """Detect conflicting requirements.

        Looks for contradictions like:
        - "must" followed by "must not" for same feature
        - Conflicting priorities
        """
        conflicts = []

        # Simple heuristic: look for contradictory statements
        # In a real implementation, this would use more sophisticated NLP
        lines = requirements.split("\n")
        for i, line in enumerate(lines):
            line_lower = line.lower()
            if "must" in line_lower and "not" in line_lower:
                # Check if there's a conflicting requirement nearby
                for j in range(max(0, i - 3), min(len(lines), i + 4)):
                    if i != j and "must" in lines[j].lower():
                        conflicts.append(
                            f"Potential conflict between lines {i+1} and {j+1}"
                        )
                        break

        return conflicts[:5]  # Limit to top 5 conflicts

    def _generate_questions(
        self, requirements: str, context: str
    ) -> list[str]:
        """Generate clarification questions.

        Based on missing elements and ambiguities, generate questions
        that need answers before implementation.
        """
        questions = []

        requirements_lower = requirements.lower()

        # Questions based on missing elements
        if "acceptance" not in requirements_lower:
            questions.append("What are the acceptance criteria for this feature?")

        if not any(term in requirements_lower for term in ["user", "as a", "role"]):
            questions.append("Who is the target user for this feature?")

        if "integration" in requirements_lower and "api" not in requirements_lower:
            questions.append("What API endpoints or services will this integrate with?")

        if any(term in requirements_lower for term in ["data", "store", "save"]):
            if "database" not in requirements_lower:
                questions.append("Where should the data be stored?")

        if "authentication" in requirements_lower or "login" in requirements_lower:
            if "session" not in requirements_lower:
                questions.append("How should user sessions be managed?")

        return questions[:10]  # Limit to top 10 questions

    def _generate_reasoning(
        self,
        completeness: dict,
        clarity: dict,
        conflicts: list[str],
        questions: list[str],
    ) -> str:
        """Generate reasoning explanation for the analysis."""
        parts = []

        parts.append(
            f"Requirements completeness: {int(completeness['score'] * 100)}%"
        )
        if completeness["missing"]:
            parts.append(
                f"Missing elements: {', '.join(completeness['missing'])}"
            )

        parts.append(f"Requirements clarity: {int(clarity['score'] * 100)}%")
        if clarity["ambiguous"]:
            parts.append(
                f"Found {len(clarity['ambiguous'])} ambiguous terms"
            )

        if conflicts:
            parts.append(f"Detected {len(conflicts)} potential conflicts")

        if questions:
            parts.append(
                f"Generated {len(questions)} clarification questions"
            )

        return ". ".join(parts) + "."
