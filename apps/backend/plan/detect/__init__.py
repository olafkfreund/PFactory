"""Detect stage — classify what kind of deliverable a plan describes."""

from plan.detect.target_classifier import (
    ClassificationResult,
    classify_plan,
    classify_text,
)

__all__ = ["ClassificationResult", "classify_plan", "classify_text"]
