#!/usr/bin/env bash
#
# build-demo-from-workspace.sh — produce a composite demo for an already-run
# (triaged) scenario, reading its real workspace, against the themed portal.
#
# Builds the three panes (scenario-accurate terminal still + themed portal
# walkthrough + triage report), composites them, and runs the quality gate.
# Use when the pipeline has already produced verdicts.json/triage_report.md and
# you just need a fresh themed recording (e.g. after a re-skin).
#
# Usage:
#   CHROME_PATH=<chrome> FRONT_URL=http://localhost:8400 \
#   build-demo-from-workspace.sh <scenario> <project_id> <spec_id> \
#       <title> <subtitle> <command> <lane_note> [expect_failure]
set -euo pipefail

SCEN="$1"; PROJECT="$2"; SPEC="$3"; TITLE="$4"; SUBTITLE="$5"; CMD="$6"; LANE="$7"
EXPECT_FAILURE="${8:-0}"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WS="$HOME/.pfactory/workspaces/$PROJECT/specs/$SPEC"
RUN_DIR="$ROOT/docs/static/recordings/demos/${SCEN}-themed01"
PANES="$RUN_DIR/panes"
PY="$ROOT/apps/backend/.venv/bin/python"
mkdir -p "$PANES"

[[ -f "$WS/findings/triage_report.md" ]] || { echo "no triage_report.md for $SCEN ($WS)"; exit 1; }
cp "$WS/findings/triage_report.md" "$RUN_DIR/triage_report.md"

# 1. terminal pane (scenario-accurate, from the real plan+verdicts) — ANIMATED:
#    self-playing line-reveal HTML, captured to webm via Playwright recordVideo.
"$PY" "$ROOT/scripts/demo/render-terminal-pane.py" "$WS" "$CMD" "$PANES/terminal.html" \
  --lane-note "$LANE" --animate >/dev/null
node "$ROOT/scripts/demo/record-html.mjs" "$PANES/terminal.html" "$PANES/terminal.webm" 13 960 720 >/dev/null 2>&1
# still frame (after full reveal) for posters / the quality gate
node -e "
const {chromium}=require('@playwright/test');
(async()=>{const b=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH});
const c=await b.newContext({viewport:{width:960,height:720}});const p=await c.newPage();
await p.goto('file://'+require('path').resolve('$PANES/terminal.html'));
await p.waitForTimeout(5000);await p.screenshot({path:'$PANES/terminal.png'});await b.close();})()
.catch(e=>{console.error(e);process.exit(1)});"

# 2. portal pane — scenario-specific: drill into THIS task, linger on Verdicts.
SPEC="$SPEC" node "$ROOT/scripts/demo/portal-record.mjs" "$PANES/portal.webm" >/dev/null 2>&1
# Keep only the TAIL (the lingered scenario verdicts) — robust to the variable
# "preparing workspace" splash + nav time at the head, which is identical across
# demos and made them all look the same.
ffmpeg -y -sseof -12 -i "$PANES/portal.webm" -c:v libvpx-vp9 -an "$PANES/portal-tail.webm" >/dev/null 2>&1 \
  && mv "$PANES/portal-tail.webm" "$PANES/portal.webm"
node "$ROOT/scripts/demo/portal-task-shot.mjs" "$PANES/report.png" "$SPEC" "Report" >/dev/null 2>&1

# 3. composite + gate
RUN_DIR="$RUN_DIR" TITLE="$TITLE" SUBTITLE="$SUBTITLE" "$ROOT/scripts/demo/compose.sh" >/dev/null 2>&1
RUN_DIR="$RUN_DIR" MIN_SECONDS=8 EXPECT_FAILURE="$EXPECT_FAILURE" "$ROOT/scripts/demo/quality-gate.sh"
echo "DEMO_OK $SCEN -> $RUN_DIR/demo.mp4"
