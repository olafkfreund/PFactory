"""Build a correction request from PFactory's failure artifacts (#184).

Part of epic #182 — the PFactory→AIFactory hand-back. When PFactory's pipeline
surfaces problems in a feature it tested, this module turns the failure
artifacts (``findings/verdicts.json`` + ``findings/triage_report.json``, plus an
optional visual-inspection correction plan) into a structured
``CorrectionRequest`` that the renderer (``render.py``) emits as a
``QA_FIX_REQUEST.md``-shaped payload for AIFactory's QA Fixer.

This is **pure compute** — no LLM, no network, no filesystem. The caller reads
the JSON and passes already-parsed dicts; that keeps the unit tests trivial and
mirrors ``agents/cloud/issues.py`` (pure ``build_*``).

The "what counts as a failure to hand back" policy lives in one place
(``_is_failing``): per the approved design (`docs/plans/2026-06-03-aifactory-
pfactory-handback-design.md`) it is the Evaluator verdict ``reject``. Keeping it
centralised makes it a one-line change to also fold in, say, execution failures
later without touching the rest of the builder.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "Failure",
    "CorrectionRequest",
    "build_correction_request",
]

# The verdict label(s) that signal a feature problem worth handing back.
_FAILING_VERDICTS = frozenset({"reject"})


@dataclass(frozen=True)
class Failure:
    """One failing test mapped to what AIFactory needs to fix it."""

    test_id: str
    test_file: str | None
    lane: str | None
    verdict: str
    reason: str
    acceptance_criterion: str | None = None

    def to_dict(self) -> dict:
        return {
            "test_id": self.test_id,
            "file": self.test_file,
            "lane": self.lane,
            "verdict": self.verdict,
            "reason": self.reason,
            "acceptance_criterion": self.acceptance_criterion,
        }


@dataclass(frozen=True)
class CorrectionRequest:
    """A correction hand-back targeting one AIFactory spec.

    ``aifactory`` is the ``source["aifactory"]`` envelope written by the
    snapshotter (P1): ``{project_id, spec_id, api_url, task_id}``. The sender
    (P4) reads it to know where to POST.
    """

    aifactory: dict
    failures: list[Failure] = field(default_factory=list)
    source_kind: str = "triage"  # "triage" | "visual_inspection"
    visual_plan: str | None = None

    @property
    def aifactory_task_id(self) -> str:
        af = self.aifactory or {}
        tid = af.get("task_id")
        if tid:
            return tid
        return f"{af.get('project_id', '?')}:{af.get('spec_id', '?')}"

    @property
    def nothing_to_hand_back(self) -> bool:
        """True when there is nothing for AIFactory to correct."""
        return not self.failures and not self.visual_plan

    def to_dict(self) -> dict:
        return {
            "aifactory_task_id": self.aifactory_task_id,
            "aifactory": dict(self.aifactory or {}),
            "source": self.source_kind,
            "failing_tests": [f.to_dict() for f in self.failures],
            "has_visual_plan": self.visual_plan is not None,
        }


def _join_reasons(verdict_entry: dict) -> str:
    """Flatten the Evaluator's ``reasons`` into a single readable line."""
    reasons = verdict_entry.get("reasons")
    if isinstance(reasons, list):
        parts = [str(r).strip() for r in reasons if str(r).strip()]
        if parts:
            return "; ".join(parts)
    # Fall back to a single ``reason`` string, then the semantic call.
    for key in ("reason", "semantic_relevance"):
        val = verdict_entry.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return "(no reason recorded)"


def _is_failing(verdict_entry: dict) -> bool:
    return verdict_entry.get("verdict") in _FAILING_VERDICTS


def _file_map(triage_report: dict | None) -> dict[str, str]:
    """test_id → test_file, harvested from every triage_report bucket."""
    out: dict[str, str] = {}
    if not isinstance(triage_report, dict):
        return out
    for bucket in ("rejected", "committed", "flagged", "tests"):
        for entry in triage_report.get(bucket, []) or []:
            if isinstance(entry, dict):
                tid = entry.get("test_id")
                tf = entry.get("test_file") or entry.get("file")
                if tid and tf:
                    out[tid] = tf
    return out


def build_correction_request(
    verdicts: dict,
    triage_report: dict | None,
    source: dict,
    *,
    visual_correction_plan: str | None = None,
) -> CorrectionRequest:
    """Assemble a :class:`CorrectionRequest` from PFactory failure artifacts.

    Args:
        verdicts: parsed ``findings/verdicts.json`` — ``{"verdicts": [...]}``.
        triage_report: parsed ``findings/triage_report.json`` (or ``None``);
            used only to enrich failures with each test's file path.
        source: parsed ``context/source.json`` — must carry the ``aifactory``
            envelope written by the snapshotter (P1).
        visual_correction_plan: optional prose from the visual module's
            ``render_correction_plan(...)`` (a **string**, not a dict). Present
            only for visual-inspection hand-backs.

    Returns:
        A ``CorrectionRequest``. Check ``.nothing_to_hand_back`` before sending —
        an all-accept run with no visual plan yields an empty request.
    """
    aifactory = dict(source.get("aifactory") or {})
    files = _file_map(triage_report)

    failures: list[Failure] = []
    for entry in verdicts.get("verdicts", []) or []:
        if not isinstance(entry, dict) or not _is_failing(entry):
            continue
        test_id = entry.get("test_id") or "(unknown test)"
        failures.append(
            Failure(
                test_id=test_id,
                test_file=entry.get("test_file")
                or entry.get("file")
                or files.get(test_id),
                lane=entry.get("lane") or entry.get("modality"),
                verdict=str(entry.get("verdict")),
                reason=_join_reasons(entry),
                acceptance_criterion=entry.get("acceptance_criterion")
                or entry.get("ac"),
            )
        )

    # Stable ordering — deterministic renders + diffs.
    failures.sort(key=lambda f: f.test_id)

    source_kind = "visual_inspection" if visual_correction_plan else "triage"
    return CorrectionRequest(
        aifactory=aifactory,
        failures=failures,
        source_kind=source_kind,
        visual_plan=visual_correction_plan,
    )
