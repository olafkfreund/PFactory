#!/usr/bin/env bash
#
# compose.sh — stitch the three demo panes into one multi-pane screencast.
#
# Layout (matches the /demo "composite" design):
#
#   ┌────────────┬───────────────┐
#   │ Claude Code│  PFactory     │   top row: terminal (left) + portal (right)
#   │ terminal   │  portal       │
#   ├────────────┴───────────────┤
#   │  triage report / verdicts  │   bottom row: full-width report panel
#   └────────────────────────────┘
#
# Inputs (under $RUN_DIR/panes/):
#   terminal.webm   — Claude Code handover terminal (asciinema → video; see
#                     cast-to-video.sh). REQUIRED.
#   portal.webm     — PFactory portal walkthrough (Playwright recording).
#                     REQUIRED.
#   report.webm     — optional moving capture of the triage report. If absent,
#                     report.png is used as a still (looped to full duration).
#   report.png      — still screenshot of findings/triage_report.md rendered.
#                     Used when report.webm is absent. One of report.{webm,png}
#                     is REQUIRED.
#
# Outputs (under $RUN_DIR/):
#   demo.mp4   — H.264, yuv420p, +faststart (web-embeddable, link-out quality)
#   demo.gif   — palette-optimised, 960px wide, 12 fps (README/Pages embed)
#
# Usage:
#   RUN_DIR=/path/to/run TITLE="PFactory — greeting-generator" \
#     scripts/demo/compose.sh
#   # or:
#   scripts/demo/compose.sh --run-dir /path/to/run --title "..."
#
# Env vars:
#   RUN_DIR   run directory holding panes/ (required)
#   TITLE     lower-third caption burned into the video (optional)
#   CANVAS_W  output width  (default 1920)
#   CANVAS_H  output height (default 1080)
#   GIF_FPS   gif frame rate (default 12)
#   GIF_W     gif width     (default 960)
#
# Exit codes:
#   0 — demo.mp4 + demo.gif produced and non-empty
#   1 — missing input pane
#   2 — usage error
#   3 — ffmpeg/ffprobe missing or encode failed
#
set -euo pipefail

# ─── flag / env parsing ────────────────────────────────────────────────

RUN_DIR="${RUN_DIR:-}"
TITLE="${TITLE:-}"
SUBTITLE="${SUBTITLE:-}"
CANVAS_W="${CANVAS_W:-1920}"
CANVAS_H="${CANVAS_H:-1080}"
GIF_FPS="${GIF_FPS:-12}"
GIF_W="${GIF_W:-960}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-dir) RUN_DIR="$2"; shift 2 ;;
    --title)   TITLE="$2";   shift 2 ;;
    --help|-h)
      sed -n '2,46p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "compose.sh: unknown arg '$1'" >&2; exit 2 ;;
  esac
done

[[ -n "$RUN_DIR" ]] || { echo "compose.sh: RUN_DIR is required" >&2; exit 2; }

command -v ffmpeg  >/dev/null 2>&1 || { echo "compose.sh: ffmpeg not found"  >&2; exit 3; }
command -v ffprobe >/dev/null 2>&1 || { echo "compose.sh: ffprobe not found" >&2; exit 3; }

PANES="$RUN_DIR/panes"
TERM_IN="$PANES/terminal.webm"
PORTAL_IN="$PANES/portal.webm"
REPORT_VID="$PANES/report.webm"
REPORT_IMG="$PANES/report.png"

[[ -s "$TERM_IN"   ]] || { echo "compose.sh: missing $TERM_IN"   >&2; exit 1; }
[[ -s "$PORTAL_IN" ]] || { echo "compose.sh: missing $PORTAL_IN" >&2; exit 1; }

REPORT_IS_VIDEO=0
if [[ -s "$REPORT_VID" ]]; then
  REPORT_IS_VIDEO=1
elif [[ -s "$REPORT_IMG" ]]; then
  REPORT_IS_VIDEO=0
else
  echo "compose.sh: need one of $REPORT_VID or $REPORT_IMG" >&2
  exit 1
fi

# ─── geometry ──────────────────────────────────────────────────────────
# Top row = two equal cells; bottom row = full-width report, same height as
# one top cell. So canvas height splits 50/50 between (top row) and (report).

HALF_W=$(( CANVAS_W / 2 ))
HALF_H=$(( CANVAS_H / 2 ))

# ─── duration: master = longest of terminal/portal ─────────────────────

dur_of() {
  ffprobe -v error -show_entries format=duration -of csv=p=0 "$1" 2>/dev/null \
    | awk '{printf "%.3f", ($1==""?0:$1)}'
}
T_DUR=$(dur_of "$TERM_IN")
P_DUR=$(dur_of "$PORTAL_IN")
DUR=$(awk -v a="$T_DUR" -v b="$P_DUR" 'BEGIN{print (a>b?a:b)}')
[[ "$(awk -v d="$DUR" 'BEGIN{print (d>0)}')" == "1" ]] || DUR=20
echo "compose.sh: master duration ${DUR}s (terminal ${T_DUR}s, portal ${P_DUR}s)"

# ─── optional lower-third (drawtext) ───────────────────────────────────
# Font detection is best-effort; if no font is found the caption is dropped
# rather than failing the encode.

FONT=""
if [[ -n "$TITLE" ]]; then
  for f in \
    /run/current-system/sw/share/X11/fonts/DejaVuSans.ttf \
    /nix/var/nix/profiles/system/sw/share/X11/fonts/DejaVuSans.ttf \
    /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf \
    "$(fc-match -f '%{file}' DejaVuSans 2>/dev/null || true)"; do
    [[ -n "$f" && -f "$f" ]] && { FONT="$f"; break; }
  done
fi

# ─── top title bar (HEADER) ────────────────────────────────────────────
# TITLE becomes a prominent top banner (left), with an optional SUBTITLE env
# rendered smaller on the right. Falls back gracefully if no font is found.
HDR_H=96
HEADER_FILTER=""
HEADER_LABEL="[stack]"
if [[ -n "$TITLE" && -n "$FONT" ]]; then
  ESC_TITLE=${TITLE//:/\\:}; ESC_TITLE=${ESC_TITLE//\'/}
  ESC_SUB=${SUBTITLE//:/\\:}; ESC_SUB=${ESC_SUB//\'/}
  SUB_DRAW=""
  [[ -n "$ESC_SUB" ]] && SUB_DRAW=",drawtext=fontfile='${FONT}':text='${ESC_SUB}':x=w-text_w-40:y=(${HDR_H}-text_h)/2:fontsize=26:fontcolor=0x8b949e"
  HEADER_FILTER="color=c=0x161b22:s=${CANVAS_W}x${HDR_H}:d=${DUR}[hdrbg];[hdrbg]drawtext=fontfile='${FONT}':text='${ESC_TITLE}':x=40:y=(${HDR_H}-text_h)/2:fontsize=34:fontcolor=white${SUB_DRAW},drawbox=x=0:y=${HDR_H}-3:w=${CANVAS_W}:h=3:color=0x58a6ff:t=fill[hdr];"
  HEADER_LABEL="[hdrstack]"
elif [[ -n "$TITLE" ]]; then
  echo "compose.sh: no font found — skipping title bar" >&2
fi

# ─── build filter graph ────────────────────────────────────────────────
# Each pane: scale to its cell preserving aspect, pad to exact cell, extend
# (tpad clone) to master duration so panes that end early hold their last
# frame instead of going black.

PAD_TOP="scale=${HALF_W}:${HALF_H}:force_original_aspect_ratio=decrease,pad=${HALF_W}:${HALF_H}:(ow-iw)/2:(oh-ih)/2:color=0x0d1117,tpad=stop_mode=clone:stop_duration=${DUR},trim=duration=${DUR},setpts=PTS-STARTPTS,setsar=1"
PAD_BOT="scale=${CANVAS_W}:${HALF_H}:force_original_aspect_ratio=decrease,pad=${CANVAS_W}:${HALF_H}:(ow-iw)/2:(oh-ih)/2:color=0x0d1117,setsar=1"

OUT_MP4="$RUN_DIR/demo.mp4"
OUT_GIF="$RUN_DIR/demo.gif"

if [[ "$REPORT_IS_VIDEO" == "1" ]]; then
  REPORT_INPUT=(-i "$REPORT_VID")
  REPORT_FILTER="[2:v]${PAD_BOT},tpad=stop_mode=clone:stop_duration=${DUR},trim=duration=${DUR},setpts=PTS-STARTPTS[bot]"
else
  REPORT_INPUT=(-loop 1 -t "$DUR" -i "$REPORT_IMG")
  REPORT_FILTER="[2:v]${PAD_BOT}[bot]"
fi

# Optional header gets vstacked above the three-pane stack.
HEADER_VSTACK=""
[[ -n "$HEADER_FILTER" ]] && HEADER_VSTACK="[hdr][stack]vstack=inputs=2[hdrstack];"

FILTER="\
${HEADER_FILTER}\
[0:v]${PAD_TOP}[tl];\
[1:v]${PAD_TOP}[tr];\
${REPORT_FILTER};\
[tl][tr]hstack=inputs=2[top];\
[top][bot]vstack=inputs=2[stack];\
${HEADER_VSTACK}\
${HEADER_LABEL}format=yuv420p[v]"

echo "compose.sh: encoding $OUT_MP4 ..."
ffmpeg -nostdin -hide_banner -loglevel error -y \
  -i "$TERM_IN" \
  -i "$PORTAL_IN" \
  "${REPORT_INPUT[@]}" \
  -filter_complex "$FILTER" \
  -map "[v]" \
  -t "$DUR" \
  -c:v libx264 -preset medium -crf 22 -pix_fmt yuv420p -movflags +faststart \
  -an "$OUT_MP4"

[[ -s "$OUT_MP4" ]] || { echo "compose.sh: mp4 encode produced empty file" >&2; exit 3; }

# ─── gif (two-pass palette) ────────────────────────────────────────────

echo "compose.sh: encoding $OUT_GIF ..."
ffmpeg -nostdin -hide_banner -loglevel error -y -i "$OUT_MP4" \
  -vf "fps=${GIF_FPS},scale=${GIF_W}:-2:flags=lanczos,split[s0][s1];[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" \
  "$OUT_GIF"

[[ -s "$OUT_GIF" ]] || { echo "compose.sh: gif encode produced empty file" >&2; exit 3; }

MP4_SZ=$(du -h "$OUT_MP4" | cut -f1)
GIF_SZ=$(du -h "$OUT_GIF" | cut -f1)
echo "compose.sh: ✓ demo.mp4 (${MP4_SZ})  demo.gif (${GIF_SZ})"
echo "  $OUT_MP4"
echo "  $OUT_GIF"
