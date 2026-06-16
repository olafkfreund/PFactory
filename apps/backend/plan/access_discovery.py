"""RFC-0007 (#84): classify test-target access into the four access classes.

Pure and dependency-light. Given the parsed ``.pfactory.yml`` targets (as plain
dicts) and the spec text, produce the contract ``access.requirements`` list — what
each test target needs to authenticate, classified A/B/C/D — without touching any
vault, network, or secret value. The curation gate (#86) later validates/curates;
the VAL gate (RFC-0006) treats an un-curated requirement as a VAL-3 ``not_run``.

Mapping rationale (RFC-0007 §2):
  A-machine-native  — federated / machine identity, no MFA in the path:
                      none, bearer (scoped token), oauth2_client_credentials,
                      serviceaccount, mtls.
  B-bootstrap-once  — a human credential reused non-interactively (cleared once):
                      basic (user/pass), ref (stored credential / captured login).
  C-ephemeral-target— a disposable target the pipeline owns (no prod cred):
                      docker_compose (local/throwaway).
  D-un-automatable  — interactive MFA (push / hardware key / SMS to a person);
                      inferred from the spec, never faked. A human-credential (B)
                      target is escalated to D when the spec describes such a flow.
"""

from __future__ import annotations

# auth.type -> base access class
_AUTH_CLASS = {
    "none": "A-machine-native",
    "bearer": "A-machine-native",
    "oauth2_client_credentials": "A-machine-native",
    "serviceaccount": "A-machine-native",
    "mtls": "A-machine-native",
    "basic": "B-bootstrap-once",
    "ref": "B-bootstrap-once",
}

# target.type values that are inherently disposable/local -> ephemeral (C)
_EPHEMERAL_TARGET_TYPES = {"docker_compose"}

# spec phrases that signal interactive MFA a pipeline cannot perform
_INTERACTIVE_MFA = (
    "push notification",
    "push approval",
    "approve the login",
    "approve in the app",
    "hardware key",
    "security key",
    "webauthn",
    "fido2",
    "fido",
    "sms code",
    "text message code",
    "one-time code sent",
    "code sent to",
    "manual login",
    "human must log in",
    "interactive login",
)


def detect_interactive_mfa(spec_text: str) -> list[str]:
    """Return the interactive-MFA phrases found in the spec (empty if none)."""
    t = (spec_text or "").lower()
    return [p for p in _INTERACTIVE_MFA if p in t]


def _env_names(auth: dict) -> list[str]:
    """Env-var names the auth references (any ``*_env`` field)."""
    return sorted(
        v for k, v in auth.items() if k.endswith("_env") and isinstance(v, str)
    )


def _credential_ref(auth: dict) -> str | None:
    """A broker ref for the credential — never the secret value itself."""
    typ = auth.get("type")
    if typ == "ref":
        return auth.get("ref")  # already a broker ref, e.g. store:tc_xxx
    if typ == "bearer" and auth.get("token_env"):
        return f"env:{auth['token_env']}"
    if typ == "basic" and auth.get("password_env"):
        return f"env:{auth['password_env']}"
    if typ == "oauth2_client_credentials" and auth.get("client_id_env"):
        return f"env:{auth['client_id_env']}"
    return None  # serviceaccount/mtls use mounted files; none has no credential


def classify_target(target: dict, *, interactive_mfa: bool = False) -> dict:
    """Classify one ``.pfactory.yml`` target dict into an access requirement."""
    auth = target.get("auth") or {"type": "none"}
    atype = auth.get("type", "none")
    ttype = target.get("type")

    if ttype in _EPHEMERAL_TARGET_TYPES:
        cls = "C-ephemeral-target"
    else:
        cls = _AUTH_CLASS.get(
            atype, "B-bootstrap-once"
        )  # unknown auth -> conservative B

    req: dict = {
        "resource": target.get("name") or ttype or "unknown",
        "auth_class": cls,
        "credential_ref": _credential_ref(auth),
        "bootstrap": "human" if cls.startswith(("B", "D")) else "none",
    }
    env = _env_names(auth)
    if env:
        req["env_required"] = env

    # Interactive MFA defeats bootstrap-once: a human-credential target becomes
    # un-automatable. Machine-native (A) and ephemeral (C) are unaffected. This
    # over-flags rather than under-flags (honest: we never claim we can log in).
    if interactive_mfa and cls == "B-bootstrap-once":
        req["auth_class"] = "D-un-automatable"
        req["bootstrap"] = "human"
        req["mvp_note"] = (
            "spec describes interactive MFA (push / hardware key / SMS); "
            "cannot authenticate non-interactively"
        )
    return req


def discover_access(targets: list[dict] | None, spec_text: str = "") -> dict | None:
    """Build the contract ``access`` block, or None when there are no targets.

    None => the task needs no external/authenticated resource (the contract omits
    the block entirely).
    """
    if not targets:
        return None
    mfa = bool(detect_interactive_mfa(spec_text))
    return {"requirements": [classify_target(t, interactive_mfa=mfa) for t in targets]}


def validate_access(
    requirements: list[dict] | None,
    *,
    env_present=None,
) -> dict:
    """Readiness check over discovered access requirements (pure, #84).

    Turns discovery into validation the planner / curation gate (#86) can surface:
    can each requirement actually be authenticated *now*?

    Args:
        requirements: the ``access.requirements`` list (or None / []).
        env_present: ``(env_name) -> bool`` — whether an env var is set. Defaults
            to ``os.environ`` membership. Injectable for tests.

    Returns ``{ready: bool, issues: [{resource, kind, detail}]}`` where ``kind`` is:
      - ``un_automatable``   — class D; cannot authenticate non-interactively.
      - ``missing_credential`` — an ``env:`` ref whose variable is unset.
      - ``needs_bootstrap``  — bootstrap=human and not yet curated (a one-time
                               human-verified login/seed/approval is required).
    A curated requirement raises no issue. ``ready`` is True only when there are
    no issues at all. ``store:``/``vault:`` refs are not probed here — their
    existence is the curation gate's job (#86).
    """
    if env_present is None:
        import os

        def env_present(name: str) -> bool:  # noqa: ANN001
            return bool(os.environ.get(name))

    issues: list[dict] = []
    for req in requirements or []:
        resource = req.get("resource", "unknown")
        if req.get("curated"):
            continue  # the curation gate already provisioned + verified this
        if req.get("auth_class") == "D-un-automatable":
            issues.append(
                {
                    "resource": resource,
                    "kind": "un_automatable",
                    "detail": req.get("mvp_note") or "interactive MFA; human-driven",
                }
            )
            continue
        ref = req.get("credential_ref")
        if isinstance(ref, str) and ref.startswith("env:"):
            var = ref.split(":", 1)[1]
            if not env_present(var):
                issues.append(
                    {
                        "resource": resource,
                        "kind": "missing_credential",
                        "detail": f"env var {var} is not set",
                    }
                )
                continue
        if req.get("bootstrap") == "human":
            issues.append(
                {
                    "resource": resource,
                    "kind": "needs_bootstrap",
                    "detail": "requires a one-time human-verified bootstrap",
                }
            )
    return {"ready": not issues, "issues": issues}


def _requirement_state(req: dict, ref_exists) -> str:
    """Per-requirement curation state. ``ref_exists(ref) -> True|False|None``."""
    if req.get("curated"):
        return "curated"
    if req.get("auth_class") == "D-un-automatable":
        return "un_automatable"
    ref = req.get("credential_ref")
    if ref:
        present = ref_exists(ref)
        if present is True:
            return "ready"  # credential present -> usable (bootstrap done)
        if present is False:
            return "missing_credential"
        return "unverified"  # None -> can't probe here; curation confirms
    # No credential ref: class A with a mounted identity (serviceaccount/mtls/none)
    # is ready; anything still flagged human-bootstrap is pending.
    return "needs_bootstrap" if req.get("bootstrap") == "human" else "ready"


def curation_status(requirements: list[dict] | None, *, ref_exists=None) -> dict:
    """Per-requirement curation state combining structure + credential presence (#86).

    ``ref_exists(ref) -> True|False|None`` probes credential presence WITHOUT
    returning the value. It is injected (PFactory wires ``pfactory_secrets.probe``);
    the default treats every ref as unverifiable (None) so this module stays pure
    and vendorable. States: ``curated`` · ``ready`` · ``needs_bootstrap`` ·
    ``missing_credential`` · ``un_automatable`` · ``unverified``. ``ready`` overall
    is True only when every requirement is ``ready``/``curated``.
    """
    if ref_exists is None:

        def ref_exists(_ref):  # noqa: ANN001 - default: undeterminable
            return None

    out = [
        {
            "resource": r.get("resource", "unknown"),
            "state": _requirement_state(r, ref_exists),
        }
        for r in requirements or []
    ]
    ready = all(s["state"] in ("ready", "curated") for s in out)
    return {"ready": ready, "requirements": out}


# --------------------------------------------------------------------------- #
# Curation gate (#86 PR-b) — placement-agnostic. WHERE a human approves
# (CFactory action / PFactory API / plan-review) is the caller's concern; this is
# the gate everyone calls. It flips `curated: true` only when the rules below hold
# and emits an RFC-0001a audit record (never any secret value).


def curate_requirement(
    req: dict,
    *,
    approval: dict | None = None,
    liveness_check=None,
) -> tuple[dict, dict | None]:
    """Apply the curation gate to one requirement. Returns ``(req_out, audit)``.

    Curates (sets ``curated: true``) ONLY when ALL hold:
      - the requirement is not class D (D-un-automatable is never curated — it is
        refused, honestly, never faked);
      - if ``bootstrap == 'human'``, an ``approval`` record with ``approved_by`` +
        ``scope`` is supplied (the human-verified bootstrap);
      - ``liveness_check(req) -> bool`` is supplied and returns truthy (we never
        mark curated without confirming the credential/target actually works).

    On success ``req_out`` is a copy with ``curated: true`` (+ ``human_approval``
    when human-bootstrapped) and ``audit`` is an access-curation record for the
    RFC-0001a chain. On refusal ``req_out`` is unchanged and ``audit`` is None.
    Idempotent: an already-curated requirement is returned unchanged with no audit.
    Never returns or logs a secret value.
    """
    if req.get("curated"):
        return req, None
    if req.get("auth_class") == "D-un-automatable":
        return req, None  # un-automatable is refused, never curated
    if req.get("bootstrap") == "human":
        if not (approval and approval.get("approved_by") and approval.get("scope")):
            return req, None  # needs a human-verified approval record first
    if liveness_check is None or not liveness_check(req):
        return req, None  # only curate after a passing liveness check

    out = dict(req)
    out["curated"] = True
    if approval:
        out["human_approval"] = {
            "approved_by": approval["approved_by"],
            "approved_at": approval.get("approved_at", ""),
            "scope": approval["scope"],
        }
    audit = {
        "kind": "access_curated",
        "resource": out.get("resource", "unknown"),
        "auth_class": out.get("auth_class"),
        "credential_ref": out.get("credential_ref"),  # a ref, never the secret
        "liveness": "passed",
        "approved_by": (approval or {}).get("approved_by"),
        "approved_at": (approval or {}).get("approved_at", ""),
        "scope": (approval or {}).get("scope"),
    }
    return out, audit


def curate_access(
    requirements: list[dict] | None,
    *,
    approvals: dict | None = None,
    liveness_check=None,
) -> dict:
    """Run the curation gate over all requirements. Pure; callers inject approvals
    (``{resource: approval}``) + ``liveness_check``. Returns
    ``{requirements: [...], audit: [...], all_curated: bool}`` — requirements with
    the curated/refused outcome applied, the audit records to append to the
    RFC-0001a chain, and whether every requirement is now curated.
    """
    approvals = approvals or {}
    out_reqs: list[dict] = []
    audit: list[dict] = []
    for req in requirements or []:
        res = req.get("resource")
        new_req, rec = curate_requirement(
            req, approval=approvals.get(res), liveness_check=liveness_check
        )
        out_reqs.append(new_req)
        if rec is not None:
            audit.append(rec)
    all_curated = bool(out_reqs) and all(r.get("curated") for r in out_reqs)
    return {"requirements": out_reqs, "audit": audit, "all_curated": all_curated}
