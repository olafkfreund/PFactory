#!/usr/bin/env bash
#
# cast-to-video.sh — render an asciinema .cast into a webm the compositor can
# tile into the terminal pane.
#
# Two paths, tried in order:
#   1. agg (asciinema gif generator) → gif → ffmpeg webm.   FAST + crisp.
#      Install on NixOS:  nix shell nixpkgs#asciinema-agg
#   2. Playwright + asciinema-player fallback (render-cast.mjs).  Used when agg
#      is absent. Plays the cast in a headless Chromium page and screen-records
#      it. Needs network access the first time (loads asciinema-player from a
#      pinned CDN) OR a local node_modules/asciinema-player.
#
# Usage:
#   scripts/demo/cast-to-video.sh INPUT.cast OUTPUT.webm
#
# Env vars:
#   CAST_COLS / CAST_ROWS   terminal geometry hint for agg (default 100x30)
#   CAST_THEME              agg theme (default "monokai")
#
# Exit codes:
#   0 — OUTPUT.webm written and non-empty
#   1 — both render paths failed
#   2 — usage error
#
set -euo pipefail

IN="${1:-}"
OUT="${2:-}"
[[ -n "$IN" && -n "$OUT" ]] || { echo "usage: cast-to-video.sh IN.cast OUT.webm" >&2; exit 2; }
[[ -s "$IN" ]] || { echo "cast-to-video.sh: input cast missing: $IN" >&2; exit 2; }

command -v ffmpeg >/dev/null 2>&1 || { echo "cast-to-video.sh: ffmpeg not found" >&2; exit 1; }

CAST_THEME="${CAST_THEME:-monokai}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─── path 1: agg (direct on PATH, or via `nix shell`) ──────────────────
AGG=""
if command -v agg >/dev/null 2>&1; then
  AGG="agg"
elif command -v nix >/dev/null 2>&1; then
  # NixOS: agg isn't installed globally but is one fetch away.
  AGG="nix shell nixpkgs#asciinema-agg -c agg"
fi
if [[ -n "$AGG" ]]; then
  echo "cast-to-video.sh: rendering via agg ($AGG)"
  TMP_GIF="$(mktemp --suffix=.gif)"
  trap 'rm -f "$TMP_GIF"' EXIT
  $AGG --theme "$CAST_THEME" "$IN" "$TMP_GIF"
  # gif → webm (vp8). agg GIFs carry an alpha channel that libvpx's
  # auto-alt-ref rejects, so flatten with format=yuv420p + -auto-alt-ref 0.
  ffmpeg -hide_banner -loglevel error -y -i "$TMP_GIF" \
    -vf "fps=20,scale=-2:540:flags=lanczos,crop=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p" \
    -c:v libvpx -b:v 1500k -auto-alt-ref 0 -an "$OUT"
  [[ -s "$OUT" ]] && { echo "cast-to-video.sh: ✓ $OUT (agg)"; exit 0; }
  echo "cast-to-video.sh: agg path produced empty output, falling back" >&2
fi

# ─── path 2: Playwright + asciinema-player ─────────────────────────────
if command -v npx >/dev/null 2>&1; then
  echo "cast-to-video.sh: rendering via Playwright fallback (render-cast.mjs)"
  if node "$HERE/render-cast.mjs" "$IN" "$OUT"; then
    [[ -s "$OUT" ]] && { echo "cast-to-video.sh: ✓ $OUT (playwright)"; exit 0; }
  fi
fi

echo "cast-to-video.sh: ✗ no working render path." >&2
echo "  Install agg:  nix shell nixpkgs#asciinema-agg" >&2
echo "  or ensure Playwright + network for the asciinema-player fallback." >&2
exit 1
