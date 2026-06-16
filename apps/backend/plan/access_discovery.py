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
