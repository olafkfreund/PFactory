#!/usr/bin/env bash
# record-handover-demo.sh — terminal demo of the PFactory handover workflow.
#
# Strategy: NARRATE OVER A REAL COMPLETED TASK.  Previously this script
# tried to run a fresh agent + poll its status live, which doesn't work
# inside asciinema's headless mode (the TTY-less recording context kills
# long-running poll loops after the first batch of subprocess pipes).
#
# Instead: read the artifacts an already-completed task left on disk
# (spec.md, test_plan.json, build-progress.txt, qa_report.md)
# and walk a viewer through the workflow with those artifacts as ground
# truth.  Recording is short (~60s), deterministic, and shows the real
# output of a real agent run — just compressed in time vs. the original
# multi-minute build.
#
# The narration script does the steps a developer actually takes:
#   1. Clone the demo repo (shown for real — actual git clone)
#   2. Show the GitHub issue (link to existing real issue)
#   3. Explain /handover (narration — shows where the skill lives)
#   4. Walk the artifacts an agent run produces:
#        spec.md → test_plan.json → build-progress.txt → qa_report.md
#   5. Show the final state — what the developer sees in the portal

set -uo pipefail

BOLD=$'\033[1m'
DIM=$'\033[2m'
CYAN=$'\033[36m'
GREEN=$'\033[32m'
YELLOW=$'\033[33m'
MAGENTA=$'\033[35m'
RESET=$'\033[0m'

banner() {
  echo
  echo "${CYAN}${BOLD}━━━ $1 ━━━${RESET}"
  echo
  sleep 1
}

narrate() {
  echo "${DIM}# ${1}${RESET}"
  sleep 0.6
}

run_cmd() {
  echo "${MAGENTA}\$${RESET} ${BOLD}$*${RESET}"
  sleep 0.4
  "$@"
}

WORKSPACE="/tmp/pfactory-handover-demo-rec"
SPEC_DIR="/tmp/pfactory-demo/.pfactory/specs/001-gh6-document-the-quick-start-in-readme"
GH_REPO="olafkfreund/pfactory-demo"

rm -rf "$WORKSPACE"

# ── Intro ────────────────────────────────────────────────────────────────

banner "PFactory — terminal handover workflow"

cat <<EOF
${BOLD}The 60-second story:${RESET}
  A developer wants to ship a feature.  They use Claude Code to write
  the spec, then type ${BOLD}/handover${RESET} to send it to PFactory's
  autonomous pipeline.  PFactory plans → codes → QAs → opens a PR.
  The developer comes back to a merge-ready branch.

This recording walks the workflow using artifacts from a real
completed run — every JSON / markdown shown was produced by an
agent build that finished earlier today.
EOF
sleep 3

# ── Step 1: clone ────────────────────────────────────────────────────────

banner "Step 1 — clone the demo repo"

narrate "Standard clone — nothing PFactory-specific."
run_cmd git clone --depth 1 "https://github.com/${GH_REPO}.git" "$WORKSPACE"
cd "$WORKSPACE"
run_cmd ls
sleep 1

# ── Step 2: file the issue ───────────────────────────────────────────────

banner "Step 2 — the GitHub issue"

narrate "A real issue lives at https://github.com/${GH_REPO}/issues/6"
narrate "Title: 'Document the Quick Start in README'"
narrate "  — the demo's existing spec walks through this end-to-end."
sleep 1
cat <<'EOF'

  ┌─────────────────────────────────────────────────────────────┐
  │ Document the Quick Start in README                       #6 │
  ├─────────────────────────────────────────────────────────────┤
  │ Add a Quick Start section showing the three commands a new  │
  │ user runs to bring the app up locally:                      │
  │   pip install -e .                                          │
  │   uvicorn src.app.main:app --reload                         │
  │   curl http://localhost:8000/                               │
  └─────────────────────────────────────────────────────────────┘

EOF
sleep 2

# ── Step 3: the handover ─────────────────────────────────────────────────

banner "Step 3 — /handover in Claude Code"

cat <<EOF
${BOLD}In a Claude Code session in this repo, the developer types:${RESET}

  ${CYAN}/handover${RESET}

The skill at ${BOLD}.claude/skills/handover/SKILL.md${RESET} reads the recent
conversation, calls ${BOLD}mcp__pfactory__task_create_and_run${RESET}, and
returns a portal URL.  PFactory's autonomous pipeline takes it
from there.

EOF
sleep 3

# ── Step 4: the artifacts PFactory produces ─────────────────────────────

banner "Step 4 — the spec PFactory wrote"

narrate "spec.md — what the planner agent crystallised from the issue body"
run_cmd cat "$SPEC_DIR/spec.md"
sleep 2

banner "Step 5 — the implementation plan"

narrate "test_plan.json — the subtasks the planner generated"
narrate "(the same view you see in the portal's Subtasks tab)"
sleep 1
# Pretty-print the plan — show subtask titles + their descriptions
python3 - "$SPEC_DIR/test_plan.json" <<'PYEOF'
import json, sys
plan = json.load(open(sys.argv[1]))
subtasks = plan.get("subtasks") or plan.get("tasks") or []
print(f"  {len(subtasks)} subtasks:")
for st in subtasks:
    sid = st.get("id") or st.get("number", "?")
    title = (st.get("title") or st.get("description") or "")[:140]
    status = st.get("status", "?")
    print(f"    [{status:9s}] {sid}  {title}")
PYEOF
sleep 3

banner "Step 6 — the coder agent's build trace"

narrate "build-progress.txt — what the coder agent narrated as it worked"
narrate "(this is what you see live in the portal's Logs tab)"
sleep 1
run_cmd head -25 "$SPEC_DIR/build-progress.txt"
sleep 2

banner "Step 7 — QA review"

narrate "qa_report.md — what the QA agent flagged"
sleep 1
run_cmd head -25 "$SPEC_DIR/qa_report.md"
sleep 2

# ── Step 8: the result ───────────────────────────────────────────────────

banner "Step 8 — the result"

cat <<EOF
${BOLD}What the developer sees in the portal:${RESET}

  • Task status:    ${GREEN}Human Review${RESET} (plan + code + QA all complete)
  • Worktree:       isolated branch with the changes
  • Action:         click "Merge to main" or "Create PR" — done

${BOLD}Total developer keystrokes:${RESET}
  • 1 conversation in Claude Code
  • 1 ${CYAN}/handover${RESET}
  • 1 plan approval in the portal
  • 1 "Merge to main" click after reviewing the diff

${BOLD}Total developer wait time:${RESET}
  A few minutes while the agent builds — or step away and come back.

EOF
sleep 3

banner "Done — that's the PFactory handover workflow"

cat <<EOF
${BOLD}Try it yourself:${RESET}
  ${CYAN}https://olafkfreund.github.io/PFactory/getting-started${RESET}
  ${CYAN}https://github.com/olafkfreund/PFactory/blob/main/guides/HANDOVER_WORKFLOW.md${RESET}

EOF
sleep 1
