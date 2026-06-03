#!/usr/bin/env bash
#
# quality-gate.sh — the bar every /demo run must clear before it is published.
#
# This is what makes the demos "good quality each time": a mechanical checklist
# the orchestrator runs after compositing, so a half-recorded or mis-stitched
# demo never ships. Prints a PASS/FAIL table and exits non-zero on any failure.
#
# Usage:
#   RUN_DIR=/path/to/run scripts/demo/quality-gate.sh
#   scripts/demo/quality-gate.sh --run-dir /path/to/run [--min-seconds 15]
#
# Checks:
#   1. demo.mp4 exists, non-empty, decodes, ≥ MIN_SECONDS long
#   2. demo.gif exists, non-empty
#   3. mp4 has the expected composite resolution (16:9, ≥ 1280 wide)
#   4. both source panes (terminal + portal) were present
#   5. a triage report panel input existed (report.webm or report.png)
#   6. the run's triage_report.md exists AND records ≥ 1 verdict
#   7. the run captured BOTH an accept and a reject/flag verdict when the
#      scenario advertises a seeded failure (so the "pass AND fail" story shows)
#
# Env vars:
#   RUN_DIR        run dir (required)
#   MIN_SECONDS    minimum acceptable demo length (default 15)
#   EXPECT_FAILURE 1 if the scenario seeds a failing AC (default 0)
#   REPORT_MD      path to the triage report md (default autodetected in RUN_DIR)
#
# Exit codes:  0 — all checks pass   1 — one or more failed   2 — usage error
#
set -uo pipefail

RUN_DIR="${RUN_DIR:-}"
MIN_SECONDS="${MIN_SECONDS:-15}"
EXPECT_FAILURE="${EXPECT_FAILURE:-0}"
REPORT_MD="${REPORT_MD:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-dir)     RUN_DIR="$2"; shift 2 ;;
    --min-seconds) MIN_SECONDS="$2"; shift 2 ;;
    --expect-failure) EXPECT_FAILURE=1; shift ;;
    --report-md)   REPORT_MD="$2"; shift 2 ;;
    *) echo "quality-gate.sh: unknown arg '$1'" >&2; exit 2 ;;
  esac
done
[[ -n "$RUN_DIR" ]] || { echo "quality-gate.sh: RUN_DIR required" >&2; exit 2; }

PASS=0; FAIL=0
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; PASS=$((PASS+1)); }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL=$((FAIL+1)); }

MP4="$RUN_DIR/demo.mp4"
GIF="$RUN_DIR/demo.gif"
PANES="$RUN_DIR/panes"

echo "quality-gate: $RUN_DIR"

# 1. mp4 present + decodable + long enough
if [[ -s "$MP4" ]]; then
  DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$MP4" 2>/dev/null | awk '{printf "%.1f",$1}')
  if [[ -n "$DUR" && "$(awk -v d="$DUR" -v m="$MIN_SECONDS" 'BEGIN{print (d>=m)}')" == "1" ]]; then
    ok "demo.mp4 decodes, ${DUR}s ≥ ${MIN_SECONDS}s"
  else
    bad "demo.mp4 too short or undecodable (${DUR:-?}s, need ≥ ${MIN_SECONDS}s)"
  fi
else
  bad "demo.mp4 missing or empty"
fi

# 2. gif present
[[ -s "$GIF" ]] && ok "demo.gif present" || bad "demo.gif missing or empty"

# 3. resolution sanity (16:9, ≥1280 wide)
if [[ -s "$MP4" ]]; then
  WH=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "$MP4" 2>/dev/null)
  W=${WH%,*}; H=${WH#*,}
  if [[ -n "$W" && "$W" -ge 1280 && -n "$H" && "$H" -gt 0 ]]; then
    RATIO=$(awk -v w="$W" -v h="$H" 'BEGIN{printf "%.3f", w/h}')
    ok "resolution ${W}x${H} (aspect ${RATIO})"
  else
    bad "resolution too small or unreadable (${WH:-?})"
  fi
fi

# 4. source panes
[[ -s "$PANES/terminal.webm" ]] && ok "terminal pane captured" || bad "terminal pane (panes/terminal.webm) missing"
[[ -s "$PANES/portal.webm"   ]] && ok "portal pane captured"   || bad "portal pane (panes/portal.webm) missing"

# 5. report panel input
if [[ -s "$PANES/report.webm" || -s "$PANES/report.png" ]]; then
  ok "triage report panel input present"
else
  bad "no report panel input (panes/report.{webm,png})"
fi

# 6 + 7. triage report content
if [[ -z "$REPORT_MD" ]]; then
  REPORT_MD=$(find "$RUN_DIR" -maxdepth 3 -name 'triage_report.md' 2>/dev/null | head -1)
fi
if [[ -n "$REPORT_MD" && -s "$REPORT_MD" ]]; then
  VERDICTS=$(grep -ciE 'accept|reject|flag' "$REPORT_MD" || true)
  if [[ "${VERDICTS:-0}" -ge 1 ]]; then
    ok "triage report has verdict lines (${VERDICTS})"
  else
    bad "triage report present but no accept/reject/flag verdicts found"
  fi
  if [[ "$EXPECT_FAILURE" == "1" ]]; then
    HAS_ACCEPT=$(grep -ciE 'accept' "$REPORT_MD" || true)
    HAS_FAIL=$(grep -ciE 'reject|flag' "$REPORT_MD" || true)
    if [[ "${HAS_ACCEPT:-0}" -ge 1 && "${HAS_FAIL:-0}" -ge 1 ]]; then
      ok "shows BOTH a pass and a fail (accept=${HAS_ACCEPT}, reject/flag=${HAS_FAIL})"
    else
      bad "scenario seeds a failure but report lacks both accept and reject/flag (accept=${HAS_ACCEPT:-0}, reject/flag=${HAS_FAIL:-0})"
    fi
  fi
else
  bad "triage_report.md not found under $RUN_DIR"
fi

echo
echo "quality-gate: ${PASS} passed, ${FAIL} failed"
[[ "$FAIL" -eq 0 ]] || exit 1
echo "quality-gate: ✓ demo cleared the bar"
