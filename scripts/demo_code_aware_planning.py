#!/usr/bin/env python3
"""Repeatable demo of RFC-0010 code-aware planning.

Drives the real PFactory planner end to end over throwaway fixtures and prints
the grounded contract for each scenario:

  A) modify existing AWS EKS Terraform  -> delta-aware contract
  B) rewrite Python -> Rust (migration) -> equivalence contract

    apps/backend/.venv/bin/python scripts/demo_code_aware_planning.py

Optionally prove the live reconnaissance clone against a real repo (network):

    apps/backend/.venv/bin/python scripts/demo_code_aware_planning.py --clone owner/name
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent / "apps" / "backend"
sys.path.insert(0, str(_BACKEND))

from plan import service as svc_mod  # noqa: E402
from plan.detect import source_inspector as si  # noqa: E402
from plan.emit import task_contract as tc  # noqa: E402
from plan.emit.contract_emit import assemble_contract  # noqa: E402
from plan.recon.reconnoiter import build_repo_map  # noqa: E402

_EKS = {
    "eks.tf": 'provider "aws" { region = "eu-west-1" }\n'
    'resource "aws_eks_cluster" "main" { name = "prod" version = "1.29" }\n',
    "node_groups.tf": 'resource "aws_eks_node_group" "workers" {\n'
    "  scaling_config { min_size = 1, max_size = 3, desired_size = 2 }\n}\n"
    'module "vpc" { source = "terraform-aws-modules/vpc/aws" }\n',
}
_PAY = {
    "pay/__init__.py": "",
    "pay/refund.py": "def refund(amount, reason):\n"
    '    if amount <= 0:\n        raise ValueError("bad")\n'
    '    return {"refunded": amount, "reason": reason}\n',
    "tests/test_refund.py": "def test_x():\n    assert True\n",
}


def _materialize(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="demo-recon-"))
    for rel, body in files.items():
        fp = root / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(body)
    return root


def _subtasks(contract: dict) -> list[dict]:
    return [st for ph in contract["phases"] for st in ph["subtasks"]]


def scenario_a() -> None:
    print(
        "\n"
        + "=" * 70
        + "\nSCENARIO A — modify existing AWS EKS Terraform\n"
        + "=" * 70
    )
    fix = _materialize(_EKS)
    rmap = build_repo_map(fix, repo="acme/infra", base_ref="main", commit="abc12345")
    svc_mod.reconnoiter = lambda repo, base_ref=None: rmap
    svc = svc_mod.PlanService(persist=False)
    spec = (
        "# Scale the EKS workers\n\n## Acceptance Criteria\n"
        "- AC#1: Increase the EKS node group max size to 6\n"
        "- AC#2: Keep the cluster version unchanged\n"
    )
    s = svc.ingest_text(
        spec, title="Scale EKS", channel="cli", repo="acme/infra", base_ref="main"
    )
    out = svc.process(s.session_id)
    c = assemble_contract(out.plan, out.epic, repo="acme/infra")
    print("change_mode      :", c.get("change_mode"))
    print("environment.lang :", c.get("environment", {}).get("language"))
    print("baseline.iac     :", c.get("baseline", {}).get("iac"))
    for st in _subtasks(c):
        if st.get("files_to_modify"):
            print(f"subtask {st['id']}   : files_to_modify={st['files_to_modify']}")
    print("ac_to_code_map   :", c.get("tfactory", {}).get("ac_to_code_map"))
    print("schema-valid     :", tc.validate_contract(c) == [])


def scenario_b() -> None:
    print(
        "\n"
        + "=" * 70
        + "\nSCENARIO B — rewrite Python -> Rust (migration)\n"
        + "=" * 70
    )
    fix = _materialize(_PAY)
    rmap = build_repo_map(fix, repo="acme/pay", base_ref="main", commit="def67890")
    svc_mod.reconnoiter = lambda repo, base_ref=None: rmap
    svc_mod.inspect_source = lambda repo, base_ref, lang: si.build_behavioral_contract(
        fix, lang
    )
    svc = svc_mod.PlanService(persist=False)
    spec = (
        "# Port payments to Rust\n\nRewrite the payments module from Python to Rust.\n\n"
        "## Acceptance Criteria\n- AC#1: The Rust refund behaves identically\n"
    )
    s = svc.ingest_text(
        spec, title="Port payments", channel="cli", repo="acme/pay", base_ref="main"
    )
    out = svc.process(s.session_id)
    c = assemble_contract(out.plan, out.epic, repo="acme/pay")
    env = c.get("environment", {})
    eq = c.get("tfactory", {}).get("equivalence", {})
    print(
        "workflow_type    :", c["workflow_type"], "| change_mode:", c.get("change_mode")
    )
    print(
        f"environment      : source={env.get('source_language')} "
        f"target={env.get('target_language')}"
    )
    print("equivalence.map  :", eq.get("module_map"))
    print("tfactory.lanes   :", c.get("tfactory", {}).get("lanes"))
    print("schema-valid     :", tc.validate_contract(c) == [])


def live_clone(repo: str) -> None:
    from plan.recon import reconnoiter

    print(
        "\n"
        + "=" * 70
        + f"\nLIVE RECON — clone {repo} (read-only, static)\n"
        + "=" * 70
    )
    rm = reconnoiter(repo)
    print("available:", rm.available, "| commit:", (rm.commit or "")[:12])
    print("languages:", rm.languages, "| iac:", rm.iac)
    print("frameworks:", rm.frameworks[:8])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--clone", metavar="owner/name", help="live recon clone of a real repo"
    )
    args = ap.parse_args()
    if args.clone:
        live_clone(args.clone)
    scenario_a()
    scenario_b()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
