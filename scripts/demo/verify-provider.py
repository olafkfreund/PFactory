"""Verify a given LLM provider can drive PFactory's Planner stage.

Runs the Planner only (AUTO_GENERATE=0) on the python-unit spec with the model
set via task_metadata.json, then reports whether a valid test_plan.json was
produced. Proves PFactory's provider abstraction works for that platform.

  TF_MODEL=<model-string> python verify_provider.py <label>
"""

import asyncio, json, os, shutil, sys, time
from pathlib import Path

sys.path.insert(0, str(Path("apps/backend").resolve()))
from workspaces import snapshot_aifactory_spec  # type: ignore
from agents.tools_pkg.tools import task_control as TC
from agents.planner import schedule_planner

MODEL = os.environ["TF_MODEL"]
LABEL = sys.argv[1] if len(sys.argv) > 1 else "provider"
PROJECT_ID = "pfactory-demo-python"
SPEC_ID = "001-pricing-helper"
REPO = Path(os.path.expanduser("~/.pfactory/demo-suts/python-unit"))
TERMINAL = {
    "planned",
    "planned_empty",
    "planner_failed",
    "stuck",
    "generating",
    "generated",
    "replan_needed",
    "generated_empty",
}


def log(*a):
    print(f"[{LABEL} {time.strftime('%H:%M:%S')}]", *a, flush=True)


async def main() -> int:
    log("model:", MODEL)
    spec_dir = TC._spec_dir(PROJECT_ID, SPEC_ID)
    if spec_dir.exists():
        shutil.rmtree(spec_dir)
    spec_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("context", "tests", "findings", "logs", "memory"):
        (spec_dir / sub).mkdir(exist_ok=True)
    snapshot_aifactory_spec(
        project_id=PROJECT_ID,
        spec_id=SPEC_ID,
        branch="pfactory/demo-python-unit",
        base_ref="main",
        project_root_path=str(REPO),
        dest_spec_dir=spec_dir,
    )

    # Per-phase model override (auto profile).
    (spec_dir / "task_metadata.json").write_text(
        json.dumps(
            {
                "isAutoProfile": True,
                "phaseModels": {"spec": MODEL, "planning": MODEL, "coding": MODEL, "qa": MODEL},
            },
            indent=2,
        )
    )

    now = TC._now_iso()
    TC._status_file(PROJECT_ID, SPEC_ID).write_text(
        json.dumps(
            {
                "task_id": SPEC_ID,
                "project_id": PROJECT_ID,
                "spec_id": SPEC_ID,
                "status": "pending",
                "phase": "created",
                "lane_progress": dict.fromkeys(TC._MVP_LANES, "pending"),
                "created_at": now,
                "updated_at": now,
            },
            indent=2,
        )
    )

    task = schedule_planner(spec_dir=spec_dir, project_dir=REPO, mode="initial")
    log("planner scheduled:", task is not None)
    if task is None:
        log("ERROR: planner not scheduled")
        return 2

    status_path = TC._status_file(PROJECT_ID, SPEC_ID)
    deadline = time.time() + 6 * 60
    last = None
    while time.time() < deadline:
        await asyncio.sleep(4)
        try:
            st = json.loads(status_path.read_text())
        except Exception:
            continue
        cur = (st.get("status"), st.get("phase"))
        if cur != last:
            log("status:", st.get("status"), "|", st.get("phase"))
            last = cur
        if st.get("status") in TERMINAL:
            break

    # Report
    plan_p = spec_dir / "test_plan.json"
    ok = False
    n = 0
    if plan_p.exists():
        try:
            plan = json.loads(plan_p.read_text())
            n = sum(len(ph.get("subtasks", [])) for ph in plan.get("phases", []))
            ok = n > 0
        except Exception as exc:
            log("test_plan.json present but invalid:", exc)
    final = json.loads(status_path.read_text()).get("status")
    log("RESULT:", "PASS" if ok else "FAIL", f"| status={final} | test_plan subtasks={n}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
