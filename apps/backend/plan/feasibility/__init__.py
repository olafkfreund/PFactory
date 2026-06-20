"""Feasibility engine (Phase C): can this plan actually be built, and at what
cost / access?

Runs after Synthesize and folds its findings into the review's feasibility lens.
Two concerns, each behind a seam so the real cloud calls are guarded and never
fail the pipeline (help, never override — a pricing/IAM outage yields a
low-confidence estimate + advisory, not an error):

- :mod:`plan.feasibility.cost`   — price the proposed resource shape (AWS Price
  List · Azure Retail Prices · GCP Billing Catalog · static fallback).
- :mod:`plan.feasibility.access` — verify the principal can actually do the work
  (credentials · region enablement · IAM policy-simulation).

RFC-0014 removed the dev-day/story-point effort assessor — sizing an LLM-agent
task in human dev-days is meaningless; the scorer's difficulty/risk/autonomy
verdict on the contract replaces it.

:func:`plan.feasibility.run.assess_feasibility` is the single entry point.
"""

from plan.feasibility.run import FeasibilityResult, assess_feasibility

__all__ = ["FeasibilityResult", "assess_feasibility"]
