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

No-op (block omitted) when the contract carries no ``tfactory`` lanes.
"""

from __future__ import annotations

from typing import Any

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


def derive_environment(contract: dict) -> dict | None:
    """Derive an RFC-0005 environment manifest from the contract's tfactory block.

    Returns None when there are no lanes (nothing to provision beyond defaults).
    """
    tf = contract.get("tfactory") or {}
    lanes: list[str] = list(tf.get("lanes") or [])
    if not lanes:
        return None
    frameworks: dict[str, str] = dict(tf.get("frameworks") or {})

    unit_fw = frameworks.get("unit", "pytest")
    language = "python" if unit_fw == "pytest" else "typescript"
    browser = "browser" in lanes

    lane_cmd = _PY_LANE_CMD if language == "python" else _NODE_LANE_CMD
    verify_commands: list[str] = [
        lane_cmd[ln] for ln in lanes if ln in lane_cmd
    ]
    if browser:
        # The Nix `playwright` binary (not npx) — see nix_provisioner.
        verify_commands.append("playwright test")

    system_packages: list[str] = ["chromium"] if browser else []

    # A lane that exercises a running app needs egress to reach it; pure unit is
    # hermetic.
    needs_net = browser or "api" in lanes or "integration" in lanes
    network = "restricted" if needs_net else "none"

    proof: list[str] = ["python --version" if language == "python" else "node --version"]
    if browser:
        proof.append("playwright --version")

    env: dict[str, Any] = {
        "language": language,
        "verify_commands": verify_commands,
        "system_packages": system_packages,
        "provisioning": {"method": "nix", "ref": "flake.nix", "generated": True},
        "network": network,
        "proof": {"verify": proof},
    }
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
