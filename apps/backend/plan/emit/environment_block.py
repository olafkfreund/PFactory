"""RFC-0005 (Phase 0): attach the ``environment`` manifest to the task contract.

The manifest is the single source of truth for the per-task toolchain, so the
build env (AIFactory) and verify env (TFactory) cannot drift. It is derived from
the already-attached ``tfactory`` block (lanes + frameworks), so this must run
AFTER :func:`plan.emit.tfactory_block.attach_tfactory`.

The planner only DECLARES the manifest (language, toolchain, system_packages,
verify commands, provisioning method = nix, proof). The actual ``flake.nix`` is
generated from this manifest at consumption time by the shared
``nix_provisioner`` (the consumer has the repo checkout); keeping it declarative
here means the manifest stays the one source of truth.

Language resolution reads the tfactory block's ``language`` field, which
:mod:`plan.emit.tfactory_block` now always sets. The old inference here was
``"python" if unit_fw == "pytest" else "typescript"`` — a binary that silently
labelled EVERY non-pytest plan (Swift, Kotlin, Java, ...) as TypeScript, so a
native-mobile contract shipped a Node environment. Descriptor-declared
languages (``plan/languages/*.yaml``, RFC-0005 paved road) carry their own lane
commands, proof command and network class.

No-op (block omitted) when the contract carries no ``tfactory`` lanes.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)
_EXACT_VERSION = re.compile(r"[0-9]{1,4}(\.[0-9]{1,4}){0,2}")
_CLAUSE = re.compile(r"(~=|==|!=|<=|>=|<|>)\s*([0-9]{1,4}(?:\.[0-9]{1,4}){0,2})(\.\*)?")
# Keep in sync with Factory/scripts/nix_provisioner.py _PY_ATTR; #1020 excludes 3.11.
_NIX_CACHED_PYTHON = ("3.14", "3.13", "3.12")


def _resolve_python(spec: str) -> tuple[str | None, str | None]:
    clauses = []
    for text in spec.split(","):
        match = _CLAUSE.fullmatch(text.strip())
        if match is None:
            return None, "unparseable"
        op, version, wildcard = match.groups()
        parts = tuple(map(int, version.split(".")))
        if (wildcard and op not in ("==", "!=")) or (op == "~=" and len(parts) < 2):  # noqa: PLR2004
            return None, "unparseable"
        clauses.append((op, parts, bool(wildcard)))

    def satisfies(minor: str) -> bool:
        prefix = tuple(map(int, minor.split(".")))
        for op, parts, wildcard in clauses:
            if op == "!=" and len(parts) == 3 and prefix == parts[:2]:  # noqa: PLR2004
                return False
            for patch in (0, 9999):
                candidate = (*prefix, patch)
                operand = (*parts, *((0,) * (3 - len(parts))))
                if wildcard:
                    matched = candidate[: len(parts)] == parts
                    ok = matched if op == "==" else not matched
                elif op == "~=":
                    ok = candidate >= operand and candidate[: len(parts) - 1] == parts[:-1]
                else:
                    ok = {
                        "<": candidate < operand,
                        "<=": candidate <= operand,
                        ">": candidate > operand,
                        ">=": candidate >= operand,
                        "==": candidate == operand,
                        "!=": candidate != operand,
                    }[op]
                if not ok:
                    return False
        return True

    candidates = [f"3.{minor}" for minor in range(8, 15)]
    satisfying = [minor for minor in candidates if satisfies(minor)]
    if not satisfying:
        return None, "unsatisfiable"
    return next((minor for minor in _NIX_CACHED_PYTHON if minor in satisfying), satisfying[0]), None


def contract_toolchain(versions: dict[str, Any]) -> dict[str, str]:
    result = {}
    for key, raw in versions.items():
        reason = None
        if not isinstance(raw, str):
            reason = "non_string"
        else:
            value = raw.strip()
            if key == "python" and not _EXACT_VERSION.fullmatch(value):
                value, reason = _resolve_python(value)
            elif key == "go":
                if not _EXACT_VERSION.fullmatch(value):
                    reason = "unparseable"
                elif tuple(map(int, value.split(".")[:2])) < (1, 21):
                    reason = "go_too_old"
            if reason is None:
                result[key] = value
        if reason is not None:
            logger.warning("Omitting toolchain %r: %s", key, reason)
    return result


# Verify command per lane, by language. Kept deliberately small + conventional;
# the consumer runs these inside the materialized Nix dev shell.
_PY_LANE_CMD = {
    "unit": "pytest -q",
    "api": "pytest -q tests/api",
    "integration": "pytest -q tests/integration",
}
_NODE_LANE_CMD = {
    "unit": "npm test",
    "api": "npm run test:api",
    "integration": "npm run test:integration",
}


def _descriptor_environment(
    tf: dict[str, Any], lanes: list[str], baseline: dict[str, Any]
) -> dict[str, Any] | None:
    """The environment manifest for a descriptor-declared language, or None.

    Lane commands come from the descriptor's ``available: true`` lanes only —
    an unavailable lane contributes NOTHING here (its reason already travels on
    the tfactory block's ``unavailable_lanes``), so nothing unrunnable can leak
    into ``verify_commands`` and read as a verdict downstream.
    """
    from plan.language_descriptors import load_languages  # noqa: PLC0415

    language = str(tf.get("language") or "").lower()
    descriptor = load_languages().get(language) if language else None
    if descriptor is None:
        return None

    verify_commands: list[str] = []
    for lane_key in lanes:
        lane = descriptor.lane(lane_key)
        if lane is not None and lane.available and lane.command not in verify_commands:
            # Deduped: kotlin's unit and api lanes share one `gradle test`
            # invocation; running it twice would double the wall clock for
            # zero extra evidence.
            verify_commands.append(lane.command)

    env: dict[str, Any] = {
        "language": (baseline.get("languages") or [descriptor.name])[0],
        "verify_commands": verify_commands,
        "system_packages": [],
        "provisioning": {"method": "nix", "ref": "flake.nix", "generated": True},
        # The descriptor states the toolchain's own minimum (gradle fetches
        # plugins + deps at run time; SPM resolves over git). A lane that
        # exercises a running app needs egress anyway, so take the wider need.
        "network": (
            "restricted"
            if descriptor.network == "restricted" or ("api" in lanes or "integration" in lanes)
            else "none"
        ),
    }
    if descriptor.proof_command:
        env["proof"] = {"verify": [descriptor.proof_command]}
    toolchain = contract_toolchain(baseline.get("versions") or {})
    if toolchain:
        env["toolchain"] = toolchain
    return env


def derive_environment(contract: dict) -> dict | None:
    """Derive an RFC-0005 environment manifest from the contract's tfactory block.

    Returns None when there are no lanes (nothing to provision beyond defaults).
    """
    tf = contract.get("tfactory") or {}
    lanes: list[str] = list(tf.get("lanes") or [])
    if not lanes:
        return None
    frameworks: dict[str, str] = dict(tf.get("frameworks") or {})
    baseline = contract.get("baseline") or {}

    descriptor_env = _descriptor_environment(tf, lanes, baseline)
    if descriptor_env is not None:
        return descriptor_env

    unit_fw = frameworks.get("unit", "pytest")
    # The test-framework language drives the lane commands + proof. Prefer the
    # tfactory block's explicit language (always set since the descriptor work);
    # the framework inference remains only for blocks emitted by older code.
    lane_language = str(
        tf.get("language") or ("python" if unit_fw == "pytest" else "typescript")
    ).lower()
    if lane_language not in ("python", "typescript", "javascript"):
        # FAIL CLOSED. Reaching here means the tfactory block names a language
        # (swift, kotlin, ...) that _descriptor_environment could not resolve —
        # i.e. the vendored plan/languages/ descriptors are missing or broken.
        # Falling back to the framework binary would silently emit a
        # python/typescript environment for a native plan, which is EXACTLY the
        # defect this module used to have (any non-pytest plan labelled
        # TypeScript). One loud refusal at emit time beats a plausible-but-
        # wrong toolchain failing hours downstream.
        raise ValueError(
            f"tfactory block names language {lane_language!r} but no "
            "plan/languages/ descriptor is vendored for it; refusing to emit a "
            "python/typescript environment for a plan in another language"
        )
    browser = "browser" in lanes

    lane_cmd = _PY_LANE_CMD if lane_language == "python" else _NODE_LANE_CMD
    verify_commands: list[str] = [lane_cmd[ln] for ln in lanes if ln in lane_cmd]
    if browser:
        # The Nix `playwright` binary (not npx) — see nix_provisioner.
        verify_commands.append("playwright test")

    system_packages: list[str] = ["chromium"] if browser else []

    # A lane that exercises a running app needs egress to reach it; pure unit is
    # hermetic.
    needs_net = browser or "api" in lanes or "integration" in lanes
    network = "restricted" if needs_net else "none"

    proof: list[str] = ["python --version" if lane_language == "python" else "node --version"]
    if browser:
        proof.append("playwright --version")

    # RFC-0010: report the repo's actual primary language + pinned versions when
    # reconnaissance grounded the plan (the manifest's source of truth), else the
    # framework-derived language.
    reported_language = (baseline.get("languages") or [lane_language])[0]

    env: dict[str, Any] = {
        "language": reported_language,
        "verify_commands": verify_commands,
        "system_packages": system_packages,
        "provisioning": {"method": "nix", "ref": "flake.nix", "generated": True},
        "network": network,
        "proof": {"verify": proof},
    }
    toolchain = contract_toolchain(baseline.get("versions") or {})
    if toolchain:
        env["toolchain"] = toolchain
    return env


def attach_environment(contract: dict, *, enabled: bool = True) -> dict:
    """Set ``contract['environment']`` from the tfactory block (RFC-0005).

    Additive + optional: no-op when disabled or when there is nothing to derive.
    Mutates and returns ``contract``.
    """
    if not enabled:
        return contract
    env = derive_environment(contract)
    if env is not None:
        contract["environment"] = env
    return contract
