"""Typed handback triage-report contract — consumer-side validation (#467).

The TFactory→AIFactory handback (failed tests routed back to the QA Fixer) is the
least-governed payload in the pipeline: historically a free-form
``QA_FIX_REQUEST.md`` blob. Per multi-agent best practice, every inter-agent
payload should be schema-validated before the next stage acts on it — otherwise
the QA Fixer can be fed garbage.

This module validates the *structured* triage report TFactory's
``CorrectionRequest`` produces (``agents/handback/request.py``) before the fixer
runs. It is **dependency-free** on purpose: the validator is the runtime source
of truth (no ``jsonschema`` at runtime, matching the emitter's stdlib ethos), and
``handback_triage.schema.json`` ships alongside it as the published contract for
cross-repo CI checks.

**Non-breaking (epic #468).** Validation only engages when a caller supplies the
typed ``triage`` block. TFactory currently POSTs markdown only; that legacy path
is accepted untouched until the producer cuts over to sending the structured
report (TFactory#283).
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["CONTRACT_VERSION", "TriageValidation", "validate_triage_report"]

# Versioned like RFC-0002 so producer and consumer can negotiate the shape.
CONTRACT_VERSION = "handback-triage-1"

_KNOWN_SOURCES = frozenset({"triage", "visual_inspection"})


@dataclass(frozen=True)
class TriageValidation:
    """Outcome of validating an inbound triage report.

    ``ok`` gates the QA Fixer. ``manifest_hash`` / ``correlation_key`` are
    surfaced for audit + observability regardless of source, keyed logging
    being an explicit acceptance criterion of #467.
    """

    ok: bool
    errors: list[str] = field(default_factory=list)
    source: str | None = None
    manifest_hash: str | None = None
    correlation_key: str | None = None
    failing_test_count: int = 0
    contract_version: str = CONTRACT_VERSION


def _is_nonempty_str(v: object) -> bool:
    return isinstance(v, str) and bool(v.strip())


def validate_triage_report(triage: object) -> TriageValidation:
    """Validate a structured triage report against the typed contract.

    A report is valid when it carries a ``source`` and *something to act on* —
    either at least one well-formed failing test (``test_id`` + ``reason``) or a
    visual-inspection plan. Malformed reports are rejected so the QA Fixer never
    runs on garbage. Never raises — a bad payload yields ``ok=False`` with
    human-readable errors, not an exception.
    """
    errors: list[str] = []

    if not isinstance(triage, dict):
        return TriageValidation(
            ok=False, errors=["triage report must be a JSON object"]
        )

    source = triage.get("source")
    if not _is_nonempty_str(source):
        errors.append("missing or empty 'source'")
    elif source not in _KNOWN_SOURCES:
        # Forward-compatible: unknown sources warn but don't hard-fail, so a new
        # producer kind doesn't break the consumer before we teach it the name.
        errors_soft = (
            f"unrecognised source '{source}' (known: {sorted(_KNOWN_SOURCES)})"
        )
        # treat as non-fatal: record nothing fatal, but note it
        # (kept out of `errors` so ok stays True on the happy path)
        _ = errors_soft

    failing = triage.get("failing_tests", [])
    if not isinstance(failing, list):
        errors.append("'failing_tests' must be a list")
        failing = []

    valid_failures = 0
    for i, ft in enumerate(failing):
        if not isinstance(ft, dict):
            errors.append(f"failing_tests[{i}] must be an object")
            continue
        ok_id = _is_nonempty_str(ft.get("test_id"))
        ok_reason = _is_nonempty_str(ft.get("reason"))
        if not ok_id:
            errors.append(f"failing_tests[{i}] missing or empty 'test_id'")
        if not ok_reason:
            errors.append(f"failing_tests[{i}] missing or empty 'reason'")
        if ok_id and ok_reason:
            valid_failures += 1

    has_visual = bool(triage.get("has_visual_plan"))
    if valid_failures == 0 and not has_visual:
        errors.append("nothing to act on: no valid failing_tests and no visual plan")

    manifest_hash = triage.get("manifest_hash")
    if manifest_hash is not None and not isinstance(manifest_hash, str):
        errors.append("'manifest_hash' must be a string when present")
        manifest_hash = None

    correlation_key = triage.get("correlation_key")
    if correlation_key is not None and not isinstance(correlation_key, str):
        correlation_key = None

    return TriageValidation(
        ok=not errors,
        errors=errors,
        source=source if _is_nonempty_str(source) else None,
        manifest_hash=manifest_hash,
        correlation_key=correlation_key,
        failing_test_count=valid_failures,
    )
