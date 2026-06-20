"""Predicate-driven lane pipeline for the Evaluator agent (issue #193).

Each lane module owns its subtask selection, runner_fn construction, and
signal-bundle assembly:

  * ``unit``    — pytest (Python), the full signal set.
  * ``api``     — httpx against a host-served app.
  * ``browser`` — Playwright (static URL + docker-compose AppRuntime paths).
  * ``jest``    — Jest (TypeScript unit tests).

``common`` holds lane-agnostic helpers (status I/O, target resolution, kube
runtime, credential specs); ``signals`` holds ``EvaluatorSignals`` and the
per-signal primitives. ``agents/evaluator.py`` remains the thin orchestrator
that fans subtasks out to these lanes.
"""
