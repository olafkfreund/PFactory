"""Emit a plan session's documents to the object store (RFC-0016 #190).

When a plan reaches its terminal ``emitted`` state, this module renders the
session's human-readable deliverables — the plan/spec markdown
(``render_plan_docs``) and the RFC-0001a audit pack (``build_audit_pack`` →
``render_markdown``) — and uploads them to the shared MinIO object store via the
vendored ``artifact_store`` client, returning the resulting ``artifacts[]``
records (URIs, never blobs) for stamping onto the durable ``job_states`` row.

Per apis/concurrency-conventions.md §2 the key layout is
``pfactory/<correlation_key>/<job_id>/<role>[/<path>]`` in bucket
``factory-artifacts``.

Role note (RFC-0016 #190 follow-up): the shared ``artifact_store`` client and
apis/job-state.schema.json currently enumerate roles
``workspace | build | test-report | evidence | log`` — there is no ``doc`` role
yet. The plan deliverables are the *output the plan stage builds*, so they are
recorded under the nearest valid role, ``build``. Adding a first-class ``doc``
role to the hub canonical ``ROLES`` (and the conventions doc) is a clean
follow-up; this module routes through one ``_PLAN_DOC_ROLE`` constant so it is a
one-line change here once the canonical adds it.

**Fail-open by design.** Object storage is an enhancement, not part of the emit
contract: every upload is wrapped in try/except and returns whatever was
uploaded so far — a missing client (no boto3), an unset ``S3_ENDPOINT``, or an
unreachable MinIO MUST NEVER block or change the plan emit. The transport is
configured from the environment (``S3_ENDPOINT`` / ``S3_BUCKET`` /
``S3_ACCESS_KEY`` / ``S3_SECRET_KEY``) set on the pod by factory-gitops.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from plan.service import PlanSession

logger = logging.getLogger(__name__)

# The role under which plan deliverables are stored. The shared contract has no
# `doc` role yet (see module docstring); `build` is the nearest valid role for
# "what the plan stage produced". One-line swap when the canonical adds `doc`.
_PLAN_DOC_ROLE = "build"


def _record(uri: str, role: str, content: str) -> dict[str, Any]:
    """Build one apis/job-state.schema.json ``artifacts[]`` item.

    Only the schema's allowed keys (role/uri/content_type/bytes) — the
    ``artifacts[]`` item schema is ``additionalProperties: false``.
    """
    return {
        "role": role,
        "uri": uri,
        "content_type": "text/markdown",
        "bytes": len(content.encode("utf-8")),
    }


def emit_plan_artifacts(
    session: PlanSession,
    *,
    job_id: str | None = None,
    correlation_key: str | int | None = None,
) -> list[dict[str, Any]]:
    """Render + upload a plan session's documents; return ``artifacts[]``.

    Best-effort and fail-open: any failure (no boto3, no ``S3_ENDPOINT``,
    unreachable MinIO, a render hiccup) is logged and yields whatever was
    uploaded so far — never raises, so the plan emit is never blocked.
    ``job_id`` defaults to the session id; ``correlation_key`` is the emitted
    epic issue number when known, threading the key so the object is joinable.
    """
    artifacts: list[dict[str, Any]] = []
    jid = job_id or session.session_id
    corr = correlation_key or session.correlation_key or session.emitted_issue_number
    try:
        # Lazy: importing the store (and boto3) only on the terminal emit keeps
        # the request path dep-free and lets the no-S3 dev path no-op cleanly.
        from runners.artifact_store import (  # noqa: PLC0415 - lazy by design
            ArtifactRef,
            ArtifactStore,
            StoreConfig,
        )

        cfg = StoreConfig.from_env()
        if not cfg.endpoint:
            logger.info(
                "[plan-artifacts] S3_ENDPOINT unset; skipping artifact upload "
                "for job_id=%s (emit unaffected)",
                jid,
            )
            return artifacts
        store = ArtifactStore(cfg)

        # Render the deliverables (pure functions over the session). Each is
        # independently guarded so one render miss never loses the other.
        docs: list[tuple[str, str]] = []
        try:
            from plan.emit.docs.render import (  # noqa: PLC0415 - lazy by design
                render_plan_docs,
            )

            bundle = render_plan_docs(session)
            if getattr(bundle, "markdown", None):
                docs.append(("plan.md", bundle.markdown))
        except Exception:  # noqa: BLE001 — one render miss must not lose the rest
            logger.warning("[plan-artifacts] plan-docs render failed", exc_info=True)
        try:
            from plan.emit.audit_pack import (  # noqa: PLC0415 - lazy by design
                build_audit_pack,
                render_markdown,
            )

            pack_md = render_markdown(build_audit_pack(session))
            if pack_md:
                docs.append(("audit-pack.md", pack_md))
        except Exception:  # noqa: BLE001 — one render miss must not lose the rest
            logger.warning("[plan-artifacts] audit-pack render failed", exc_info=True)

        for name, content in docs:
            ref = ArtifactRef(
                "pfactory",
                jid,
                _PLAN_DOC_ROLE,
                correlation_key=corr,
                path=name,
                bucket=cfg.bucket,
            )
            uri = store.put_artifact(ref, content, content_type="text/markdown")
            artifacts.append(_record(uri, _PLAN_DOC_ROLE, content))

        if artifacts:
            logger.info(
                "[plan-artifacts] uploaded %d plan document(s) for job_id=%s to %s",
                len(artifacts),
                jid,
                cfg.bucket,
            )
    except Exception:  # noqa: BLE001 — artifact emission must never block an emit
        logger.warning(
            "[plan-artifacts] artifact upload failed for job_id=%s (continuing; emit unaffected)",
            jid,
            exc_info=True,
        )
    return artifacts
