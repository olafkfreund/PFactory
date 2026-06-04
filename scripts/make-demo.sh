#!/usr/bin/env bash
# Produce PFactory demo assets: an MP4 walkthrough, a derived GIF, and per-screen
# PNG stills — captured from the live single-origin portal via Playwright.
#
#   bash scripts/make-demo.sh
#
# Assumes the single-origin server (SPA + API) is reachable at PFACTORY_PORTAL_URL
# (default http://127.0.0.1:3198). If it isn't, build the SPA and start it first:
#   (cd apps/frontend-web && npx vite build --outDir ../web-server/static --emptyOutDir)
#   (cd apps/web-server && APP_DISABLE_AUTH=true APP_PORT=3198 AWS_PROFILE=default \
#      PFACTORY_ENRICH_ADAPTERS=aws PFACTORY_BUDGET_MONTHLY_USD=500 \
#      python -m uvicorn server.main:app --port 3198)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORTAL="${PFACTORY_PORTAL_URL:-http://127.0.0.1:3198}"
VIDEO_TMP="$ROOT/docs/static/recordings/tmp"
VIDEO_OUT="$ROOT/docs/static/videos/pfactory-walkthrough.mp4"
GIF_OUT="$ROOT/docs/static/img/pfactory-walkthrough.gif"
SHOTS="$ROOT/docs/static/img/screenshots"

echo "▶ preflight"
command -v ffmpeg >/dev/null || { echo "ffmpeg required"; exit 1; }
mkdir -p "$VIDEO_TMP" "$(dirname "$VIDEO_OUT")" "$(dirname "$GIF_OUT")" "$SHOTS"

SERVER_PID=""
cleanup() { [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null || true; }
trap cleanup EXIT

echo "▶ checking portal at $PORTAL"
if ! curl -sf "$PORTAL/api/health" >/dev/null 2>&1; then
  echo "  not up — building SPA + launching single-origin server on :3198…"
  (cd "$ROOT/apps/frontend-web" && npx vite build --outDir ../web-server/static --emptyOutDir >/dev/null 2>&1)
  PY="${PFACTORY_PYTHON:-/tmp/pf-full-venv/bin/python}"
  LIBSTDCXX_DIR="$(dirname "$(gcc -print-file-name=libstdc++.so.6)")"
  ( cd "$ROOT/apps/web-server" && \
    LD_LIBRARY_PATH="$LIBSTDCXX_DIR" APP_DISABLE_AUTH=true APP_PORT=3198 APP_HOST=127.0.0.1 \
    APP_CORS_ORIGINS='["http://127.0.0.1:3198"]' \
    AWS_PROFILE="${AWS_PROFILE:-default}" AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-eu-west-2}" \
    PFACTORY_ENRICH_ADAPTERS=aws PFACTORY_ENRICH_CONNECTORS=best-practices,git-markdown \
    PFACTORY_BUDGET_MONTHLY_USD=500 \
    PYTHONPATH="$ROOT/apps/web-server:$ROOT/apps/backend" \
    "$PY" -m uvicorn server.main:app --host 127.0.0.1 --port 3198 >/tmp/pf-demo-server.log 2>&1 ) &
  SERVER_PID=$!
  for _ in $(seq 1 40); do curl -sf "$PORTAL/api/health" >/dev/null 2>&1 && break; sleep 1; done
  curl -sf "$PORTAL/api/health" >/dev/null 2>&1 || { echo "✗ server failed to start"; tail -5 /tmp/pf-demo-server.log; exit 1; }
  echo "  server up (pid $SERVER_PID)"
fi

echo "▶ resolving a launchable browser"
# Prefer the Nix-built chrome-headless-shell (correct rpath/libs on NixOS) or a
# system google-chrome/chromium. The npm-downloaded chromium in ~/.cache often
# can't load system libs (libnspr4 …) on NixOS, so it's the last resort.
if [ -z "${PFACTORY_CHROME:-}" ]; then
  for c in \
    "${PLAYWRIGHT_BROWSERS_PATH:-/nix/store}"/*chromium_headless_shell*/chrome-headless-shell-linux64/chrome-headless-shell \
    /nix/store/*/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell \
    "$(command -v google-chrome-stable || true)" \
    "$(command -v chromium || true)" \
    "$HOME"/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell; do
    [ -n "$c" ] && [ -x "$c" ] && { PFACTORY_CHROME="$c"; break; }
  done
fi
echo "  browser: ${PFACTORY_CHROME:-<playwright default>}"

echo "▶ capturing walkthrough (seeds data + records video + PNGs)…"
rm -f "$VIDEO_TMP"/*.webm 2>/dev/null || true
PFACTORY_PORTAL_URL="$PORTAL" PFACTORY_CHROME="${PFACTORY_CHROME:-}" \
  node_modules/.bin/tsx "$ROOT/scripts/demo-planning-walkthrough.ts"

WEBM="$(ls -t "$VIDEO_TMP"/*.webm 2>/dev/null | head -1)"
[ -n "$WEBM" ] || { echo "✗ no video recorded"; exit 1; }

echo "▶ encoding MP4 → $VIDEO_OUT"
ffmpeg -y -loglevel error -i "$WEBM" \
  -vf "scale=1440:-2:flags=lanczos" -c:v libx264 -pix_fmt yuv420p -movflags +faststart "$VIDEO_OUT"

echo "▶ deriving GIF → $GIF_OUT"
PALETTE="$VIDEO_TMP/palette.png"
GIF_FILTER="fps=12,scale=1000:-1:flags=lanczos"
ffmpeg -y -loglevel error -i "$VIDEO_OUT" -vf "$GIF_FILTER,palettegen=stats_mode=diff" "$PALETTE"
ffmpeg -y -loglevel error -i "$VIDEO_OUT" -i "$PALETTE" \
  -lavfi "$GIF_FILTER [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=3" "$GIF_OUT"

GIF_MB=$(du -m "$GIF_OUT" | cut -f1)
echo "▶ done"
echo "  MP4 : $VIDEO_OUT ($(du -h "$VIDEO_OUT" | cut -f1))"
echo "  GIF : $GIF_OUT (${GIF_MB} MB)"
echo "  PNGs: $SHOTS/ ($(ls "$SHOTS"/*.png 2>/dev/null | wc -l) files)"
[ "${GIF_MB:-0}" -gt 12 ] && echo "  ⚠ GIF > 12 MB — consider lowering fps/scale in make-demo.sh"
echo "  (intermediate webm/palette left in $VIDEO_TMP — gitignored)"
