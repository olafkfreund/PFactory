"""Drive the FULL PFactory pipeline (Planner→Gen→Executor→Evaluator→Triager)
on a given provider/model and report the terminal verdict mix.

Unlike verify-provider.py (Planner only), this enables the whole auto-fire
chain and polls until a truly terminal state (triaged / *_failed / stuck).
Requires Docker + the pfactory-runner-pytest image (the Executor sandbox).

  TF_MODEL=<model-string> python full-pipeline.py <label>
"""
import asyncio, json, os, shutil, sys, time
from pathlib import Path

sys.path.insert(0, str(Path("apps/backend").resolve()))

# Enable the full auto-fire chain BEFORE importing the agents.
for var in ("PFACTORY_AUTO_PLAN", "PFACTORY_AUTO_GENERATE",
            "PFACTORY_AUTO_EVALUATE", "PFACTORY_AUTO_TRIAGE"):
    os.environ[var] = "1"

from workspaces import snapshot_aifactory_spec  # type: ignore
from agents.tools_pkg.tools import task_control as TC
from agents.planner import schedule_planner

MODEL = os.environ["TF_MODEL"]
LABEL = sys.argv[1] if len(sys.argv) > 1 else "provider"
PROJECT_ID = "pfactory-demo-python"
SPEC_ID = "001-pricing-helper"
REPO = Path(os.path.expanduser("~/.pfactory/demo-suts/python-unit"))
# Truly terminal states only — let the chain run to the Triager.
TERMINAL = {"triaged", "triaged_empty", "planner_failed",
            "gen_functional_failed", "evaluator_failed", "triager_failed",
            "replan_needed", "generated_empty", "stuck"}


def log(*a):
    print(f"[{LABEL} {time.strftime('%H:%M:%S')}]", *a, flush=True)


async def main() -> int:
    log("model:", MODEL, "| FULL pipeline")
    spec_dir = TC._spec_dir(PROJECT_ID, SPEC_ID)
    if spec_dir.exists():
        shutil.rmtree(spec_dir)
    spec_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("context", "tests", "findings", "logs", "memory"):
        (spec_dir / sub).mkdir(exist_ok=True)
    snapshot_aifactory_spec(
        project_id=PROJECT_ID, spec_id=SPEC_ID, branch="pfactory/demo-python-unit",
        base_ref="main", project_root_path=str(REPO), dest_spec_dir=spec_dir)

    (spec_dir / "task_metadata.json").write_text(json.dumps({
        "isAutoProfile": True,
        "phaseModels": {"spec": MODEL, "planning": MODEL, "coding": MODEL, "qa": MODEL},
    }, indent=2))

    now = TC._now_iso()
    TC._status_file(PROJECT_ID, SPEC_ID).write_text(json.dumps({
        "task_id": SPEC_ID, "project_id": PROJECT_ID, "spec_id": SPEC_ID,
        "status": "pending", "phase": "created",
        "lane_progress": dict.fromkeys(TC._MVP_LANES, "pending"),
        "created_at": now, "updated_at": now}, indent=2))

    task = schedule_planner(spec_dir=spec_dir, project_dir=REPO, mode="initial")
    log("planner scheduled:", task is not None)
    if task is None:
        log("ERROR: planner not scheduled"); return 2

    status_path = TC._status_file(PROJECT_ID, SPEC_ID)
    deadline = time.time() + 20 * 60  # full 4-agent run on a slow local model
    last = None
    while time.time() < deadline:
        await asyncio.sleep(5)
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
    final = json.loads(status_path.read_text()).get("status")
    plan_p = spec_dir / "test_plan.json"
    n_sub = 0
    if plan_p.exists():
        try:
            plan = json.loads(plan_p.read_text())
            n_sub = sum(len(ph.get("subtasks", [])) for ph in plan.get("phases", []))
        except Exception:
            pass

    verdicts_p = spec_dir / "findings" / "verdicts.json"
    n_verdicts, mix = 0, {}
    if verdicts_p.exists():
        try:
            vs = json.loads(verdicts_p.read_text())
            items = vs if isinstance(vs, list) else vs.get("verdicts", [])
            n_verdicts = len(items)
            for v in items:
                k = v.get("verdict", "?")
                mix[k] = mix.get(k, 0) + 1
        except Exception as exc:
            log("verdicts.json present but invalid:", exc)

    triage_md = spec_dir / "findings" / "triage_report.md"
    log(f"subtasks={n_sub} verdicts={n_verdicts} mix={mix} "
        f"triage_report={'yes' if triage_md.exists() else 'no'}")
    reached = final in {"triaged", "triaged_empty"}
    log("RESULT:", "PASS" if reached else "FAIL", f"| final_status={final}")
    return 0 if reached else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
