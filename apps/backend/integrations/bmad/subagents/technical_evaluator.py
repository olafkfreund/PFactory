"""
Technical Evaluator Sub-Agent
===============================

Evaluates technical decisions, assesses risks, and recommends best practices.
Provides expert guidance on architecture, technology choices, and implementation approach.
"""

from typing import Any

from .base import SubAgent, SubAgentResult


class TechnicalEvaluator(SubAgent):
    """Sub-agent for technical decision evaluation.

    Evaluates technical decisions to assess:
    - Risk level (security, performance, maintainability)
    - Best practices alignment
    - Scalability implications
    - Technology fit
    - Implementation complexity

    Input data structure:
    {
        "decision": str,                    # Technical decision to evaluate
        "context": str (optional),          # Additional context
        "alternatives": List[str] (optional), # Alternative approaches
        "constraints": List[str] (optional)  # Known constraints
    }

    Output data structure:
    {
        "risk_level": str,          # "low", "medium", "high"
        "risk_factors": List[str],  # Identified risk factors
        "pros": List[str],          # Advantages
        "cons": List[str],          # Disadvantages
        "best_practices": List[str], # Best practice recommendations
        "security_concerns": List[str],
        "performance_concerns": List[str],
        "scalability_concerns": List[str],
        "alternative_recommendations": List[str]
    }
    """

    @property
    def name(self) -> str:
        return "Technical Evaluator"

    @property
    def description(self) -> str:
        return "Evaluates technical decisions and assesses risks"

    def analyze(self, input_data: dict[str, Any]) -> SubAgentResult:
        """Evaluate a technical decision.

        Args:
            input_data: Dictionary with 'decision' and optional context

        Returns:
            SubAgentResult with evaluation findings
        """
        decision = input_data.get("decision", "")
        context = input_data.get("context", "")
        alternatives = input_data.get("alternatives", [])
        constraints = input_data.get("constraints", [])

        if not decision:
            return SubAgentResult(
                success=False,
                data={},
                reasoning="No technical decision provided for evaluation",
                confidence=0.0,
            )

        # Assess risk level
        risk_assessment = self._assess_risk(decision, context, constraints)

        # Identify pros and cons
        pros, cons = self._analyze_pros_cons(decision)

        # Check security concerns
        security_concerns = self._assess_security(decision)

        # Check performance concerns
        performance_concerns = self._assess_performance(decision)

        # Check scalability
        scalability_concerns = self._assess_scalability(decision)

        # Generate best practice recommendations
        best_practices = self._recommend_best_practices(decision)

        # Evaluate alternatives if provided
        alternative_recommendations = []
        if alternatives:
            alternative_recommendations = self._evaluate_alternatives(
                decision, alternatives
            )

        # Calculate confidence based on analysis depth
        confidence = 0.7  # Base confidence for heuristic analysis
        if context:
            confidence += 0.1
        if constraints:
            confidence += 0.1
        if alternatives:
            confidence += 0.1

        confidence = min(1.0, confidence)

        # Compile issues based on risk level
        issues = []
        if risk_assessment["level"] == "high":
            issues.append("High-risk technical decision")
            issues.extend(risk_assessment["factors"][:3])
        elif risk_assessment["level"] == "medium":
            issues.append("Medium-risk technical decision - review carefully")

        # Generate recommendations
        recommendations = []
        if security_concerns:
            recommendations.append(
                f"Address {len(security_concerns)} security concerns"
            )
        if performance_concerns:
            recommendations.append(
                "Consider performance implications"
            )
        if scalability_concerns:
            recommendations.append(
                "Plan for scalability challenges"
            )
        if alternative_recommendations:
            recommendations.append(
                "Review alternative approaches"
            )

        recommendations.extend(best_practices[:3])  # Top 3 best practices

        return SubAgentResult(
            success=True,
            data={
                "risk_level": risk_assessment["level"],
                "risk_factors": risk_assessment["factors"],
                "pros": pros,
                "cons": cons,
                "best_practices": best_practices,
                "security_concerns": security_concerns,
                "performance_concerns": performance_concerns,
                "scalability_concerns": scalability_concerns,
                "alternative_recommendations": alternative_recommendations,
            },
            reasoning=self._generate_reasoning(
                risk_assessment, pros, cons, security_concerns, performance_concerns
            ),
            confidence=confidence,
            issues=issues,
            recommendations=recommendations,
            metadata={
                "decision_length": len(decision),
                "context_provided": bool(context),
                "alternatives_count": len(alternatives),
                "constraints_count": len(constraints),
            },
        )

    def _assess_risk(
        self, decision: str, context: str, constraints: list[str]
    ) -> dict[str, Any]:
        """Assess overall risk level of the technical decision."""
        risk_factors = []
        risk_score = 0

        decision_lower = decision.lower()
        combined_text = f"{decision} {context}".lower()

        # Security risk indicators
        security_keywords = [
            "authentication",
            "password",
            "token",
            "api key",
            "secret",
            "credential",
            "session",
            "cookie",
        ]
        if any(kw in decision_lower for kw in security_keywords):
            risk_factors.append("Security-sensitive decision")
            risk_score += 3

        # Data handling risk indicators
        data_keywords = ["database", "sql", "query", "data", "store", "persist"]
        if any(kw in decision_lower for kw in data_keywords):
            risk_factors.append("Data handling involved")
            risk_score += 2

        # External integration risk
        integration_keywords = [
            "api",
            "integration",
            "third-party",
            "external",
            "service",
        ]
        if any(kw in decision_lower for kw in integration_keywords):
            risk_factors.append("External integration")
            risk_score += 2

        # Performance-critical indicators
        performance_keywords = ["real-time", "performance", "scale", "concurrent"]
        if any(kw in decision_lower for kw in performance_keywords):
            risk_factors.append("Performance-critical")
            risk_score += 2

        # Legacy system integration
        if "legacy" in combined_text or "migration" in combined_text:
            risk_factors.append("Legacy system integration")
            risk_score += 2

        # Breaking change indicators
        breaking_keywords = ["breaking", "incompatible", "migration", "rewrite"]
        if any(kw in decision_lower for kw in breaking_keywords):
            risk_factors.append("Potential breaking change")
            risk_score += 3

        # Constraint-based risk
        if any("cannot" in c.lower() or "must not" in c.lower() for c in constraints):
            risk_factors.append("Hard constraints present")
            risk_score += 1

        # Determine risk level
        if risk_score >= 6:
            level = "high"
        elif risk_score >= 3:
            level = "medium"
        else:
            level = "low"

        return {"level": level, "factors": risk_factors, "score": risk_score}

    def _analyze_pros_cons(self, decision: str) -> tuple[list[str], list[str]]:
        """Identify pros and cons of the decision."""
        pros = []
        cons = []

        decision_lower = decision.lower()

        # Positive indicators
        if "standard" in decision_lower or "industry standard" in decision_lower:
            pros.append("Uses industry standard approach")

        if "simple" in decision_lower or "straightforward" in decision_lower:
            pros.append("Simple to implement")

        if "scalable" in decision_lower:
            pros.append("Designed for scalability")

        if "secure" in decision_lower or "security" in decision_lower:
            pros.append("Security-conscious approach")

        if "tested" in decision_lower or "proven" in decision_lower:
            pros.append("Proven solution")

        # Negative indicators
        if "complex" in decision_lower or "complicated" in decision_lower:
            cons.append("High implementation complexity")

        if "custom" in decision_lower and "solution" in decision_lower:
            cons.append("Custom solution requires ongoing maintenance")

        if "experimental" in decision_lower or "beta" in decision_lower:
            cons.append("Technology not fully mature")

        if "monolithic" in decision_lower:
            cons.append("Monolithic architecture limits flexibility")

        if "tight coupling" in decision_lower or "tightly coupled" in decision_lower:
            cons.append("High coupling increases maintenance burden")

        return pros, cons

    def _assess_security(self, decision: str) -> list[str]:
        """Identify security concerns."""
        concerns = []
        decision_lower = decision.lower()

        security_patterns = {
            "password": "Password handling requires secure hashing (bcrypt, Argon2)",
            "token": "Tokens should be stored securely and have expiration",
            "api key": "API keys must not be hardcoded or committed to version control",
            "session": "Session management should use secure, httpOnly cookies",
            "sql": "SQL queries must use parameterized statements to prevent injection",
            "authentication": "Authentication should use established protocols (OAuth, JWT)",
            "authorization": "Authorization checks required at API and data layers",
            "encryption": "Ensure encryption at rest and in transit",
            "input": "All user input must be validated and sanitized",
            "file upload": "File uploads need validation, scanning, and size limits",
        }

        for pattern, concern in security_patterns.items():
            if pattern in decision_lower:
                concerns.append(concern)

        return concerns[:5]  # Top 5 concerns

    def _assess_performance(self, decision: str) -> list[str]:
        """Identify performance concerns."""
        concerns = []
        decision_lower = decision.lower()

        performance_patterns = {
            "query": "Database queries should be indexed and optimized",
            "n+1": "Avoid N+1 query problems - use eager loading or batch queries",
            "loop": "Minimize operations inside loops, especially I/O",
            "recursive": "Recursive operations may cause stack overflow or performance issues",
            "synchronous": "Synchronous operations may block - consider async alternatives",
            "cache": "Caching strategy needed for frequently accessed data",
            "large file": "Large file operations need streaming to avoid memory issues",
            "real-time": "Real-time features require efficient data structures and indexing",
        }

        for pattern, concern in performance_patterns.items():
            if pattern in decision_lower:
                concerns.append(concern)

        return concerns[:5]  # Top 5 concerns

    def _assess_scalability(self, decision: str) -> list[str]:
        """Identify scalability concerns."""
        concerns = []
        decision_lower = decision.lower()

        scalability_patterns = {
            "in-memory": "In-memory storage doesn't scale across multiple instances",
            "file system": "File system storage complicates horizontal scaling",
            "single server": "Single server architecture is a scalability bottleneck",
            "blocking": "Blocking operations limit concurrent request handling",
            "state": "Stateful architecture complicates horizontal scaling",
            "monolithic": "Monolithic design makes it harder to scale components independently",
        }

        for pattern, concern in scalability_patterns.items():
            if pattern in decision_lower:
                concerns.append(concern)

        return concerns[:5]  # Top 5 concerns

    def _recommend_best_practices(self, decision: str) -> list[str]:
        """Generate best practice recommendations."""
        recommendations = []
        decision_lower = decision.lower()

        # General best practices
        if "api" in decision_lower:
            recommendations.append("Follow REST/GraphQL best practices for API design")
            recommendations.append("Version APIs to maintain backward compatibility")

        if "database" in decision_lower or "data" in decision_lower:
            recommendations.append("Use database migrations for schema changes")
            recommendations.append("Implement proper backup and recovery procedures")

        if "test" in decision_lower or "testing" in decision_lower:
            recommendations.append("Maintain test coverage above 80%")
            recommendations.append("Include integration tests for critical paths")

        if "error" in decision_lower or "exception" in decision_lower:
            recommendations.append("Implement structured error handling and logging")
            recommendations.append("Don't expose internal errors to users")

        # Always-applicable recommendations
        recommendations.append("Document technical decisions in ADRs")
        recommendations.append("Follow the principle of least privilege")
        recommendations.append("Keep dependencies up to date for security")

        return recommendations[:8]  # Top 8 recommendations

    def _evaluate_alternatives(
        self, decision: str, alternatives: list[str]
    ) -> list[str]:
        """Evaluate alternative approaches."""
        evaluations = []

        for i, alt in enumerate(alternatives, 1):
            alt_lower = alt.lower()

            # Simple heuristic evaluation
            if "simpler" in alt_lower or "easier" in alt_lower:
                evaluations.append(
                    f"Alternative {i}: {alt} - May reduce complexity"
                )
            elif "proven" in alt_lower or "established" in alt_lower:
                evaluations.append(
                    f"Alternative {i}: {alt} - Lower risk, established solution"
                )
            elif "flexible" in alt_lower or "scalable" in alt_lower:
                evaluations.append(
                    f"Alternative {i}: {alt} - Better long-term flexibility"
                )
            else:
                evaluations.append(f"Alternative {i}: {alt} - Consider trade-offs")

        return evaluations

    def _generate_reasoning(
        self,
        risk_assessment: dict,
        pros: list[str],
        cons: list[str],
        security_concerns: list[str],
        performance_concerns: list[str],
    ) -> str:
        """Generate reasoning explanation for the evaluation."""
        parts = []

        parts.append(f"Risk level: {risk_assessment['level']}")

        if risk_assessment["factors"]:
            parts.append(
                f"Risk factors: {', '.join(risk_assessment['factors'][:3])}"
            )

        if pros:
            parts.append(f"Advantages: {len(pros)} identified")

        if cons:
            parts.append(f"Disadvantages: {len(cons)} identified")

        if security_concerns:
            parts.append(f"{len(security_concerns)} security concerns found")

        if performance_concerns:
            parts.append(f"{len(performance_concerns)} performance considerations")

        return ". ".join(parts) + "."
