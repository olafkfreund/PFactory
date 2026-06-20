"""Backward compatibility shim - import from analysis.risk_classifier instead."""

from analysis.risk_classifier import (
    AssessmentFlags,
    ComplexityAnalysis,
    InfrastructureAnalysis,
    IntegrationAnalysis,
    KnowledgeAnalysis,
    RiskAnalysis,
    RiskAssessment,
    RiskClassifier,
    ScopeAnalysis,
    ValidationRecommendations,
    get_validation_requirements,
    load_risk_assessment,
)

__all__ = [
    "AssessmentFlags",
    "ComplexityAnalysis",
    "InfrastructureAnalysis",
    "IntegrationAnalysis",
    "KnowledgeAnalysis",
    "RiskAnalysis",
    "RiskAssessment",
    "RiskClassifier",
    "ScopeAnalysis",
    "ValidationRecommendations",
    "get_validation_requirements",
    "load_risk_assessment",
]
