#!/usr/bin/env bash
# tests/test_showcase_portal_recorder.sh
#
# Hermetic smoke test for showcase-portal-recorder.ts.
#
# Sets up:
#   1. A tiny Node WebSocket server that emits a canned sequence of
#      task:update events (8 phase transitions), then closes.
#   2. A tiny HTTP server that:
#        GET /api/pfactory/tasks/001-greeting-generator  → status_json
#        GET /                                          → HTML with lane-status-grid
#   3. Runs the recorder against these mocks
#   4. Asserts the expected PNG files appeared in OUTDIR
#   5. Cleans up
#
# Requires: Node 22+, the recorder script's dependencies (playwright, ws)
# available in node_modules (hoisted at repo root).
#
# Exit 0 = pass, exit 1 = fail.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/showcase-portal-recorder.ts"
NODE_MODULES="$REPO_ROOT/node_modules"
OUTDIR="$(mktemp -d)"
SPEC_ID="001-greeting-generator"
PROJECT_ID="pfactory-demo"
TOKEN="smoke-test-token"

# ── port helpers ────────────────────────────────────────────────────────────
# Pick two random high ports that are likely free
WS_PORT=$((30000 + RANDOM % 10000))
HTTP_PORT=$((40000 + RANDOM % 10000))

cleanup() {
  # Kill any background processes we started
  [[ -n "${WS_PID:-}" ]] && kill "$WS_PID" 2>/dev/null || true
  [[ -n "${HTTP_PID:-}" ]] && kill "$HTTP_PID" 2>/dev/null || true
  rm -rf "$OUTDIR"
  rm -f /tmp/smoke_ws_server_$$.mjs /tmp/smoke_http_server_$$.mjs
}
trap cleanup EXIT

echo "[smoke] OUTDIR=$OUTDIR"
echo "[smoke] WS_PORT=$WS_PORT  HTTP_PORT=$HTTP_PORT"

# ── 1. Write the mock WebSocket server ─────────────────────────────────────
#
# Emits 8 task:update events with increasing phase values, then closes.
# Phases: pending → planning → planned → generating → generated
#       → evaluating → evaluated → triaged
#
cat > /tmp/smoke_ws_server_$$.mjs <<EOF
import { createServer } from 'http';
// ws ships an ESM wrapper for Node's native ESM loader
import { WebSocketServer } from '${NODE_MODULES}/ws/wrapper.mjs';

const phases = [
  'pending', 'planning', 'planned',
  'generating', 'generated',
  'evaluating', 'evaluated', 'triaged',
];

const httpSrv = createServer();
const wss = new WebSocketServer({ server: httpSrv });

wss.on('connection', (ws) => {
  let i = 0;
  const iv = setInterval(() => {
    if (i >= phases.length) {
      clearInterval(iv);
      ws.close(1000, 'done');
      return;
    }
    const msg = JSON.stringify({
      type: 'task:update',
      payload: {
        taskId: '${SPEC_ID}',
        phase: phases[i],
      },
    });
    ws.send(msg);
    i++;
  }, 120);

  ws.on('message', () => {}); // ignore pings
});

httpSrv.listen(${WS_PORT}, '127.0.0.1', () => {
  console.log('WS mock listening on', ${WS_PORT});
});
EOF

# ── 2. Write the mock HTTP server ──────────────────────────────────────────
#
# Serves:
#   GET / → HTML with the lane-status-grid testid so Playwright can find it
#   GET /api/pfactory/tasks/{spec_id} → cycles through phases
#   GET /api/health → 200 OK  (prevents auth middleware 401s in some builds)
#
cat > /tmp/smoke_http_server_$$.mjs <<EOF
import { createServer } from 'http';

const phases = [
  'pending', 'planning', 'planned',
  'generating', 'generated',
  'evaluating', 'evaluated', 'triaged',
];
let phaseIdx = 0;

const HTML = \`<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>PFactory smoke portal</title></head>
<body>
  <h1>PFactory Portal (mock)</h1>
  <div data-testid="lane-status-grid" style="width:480px;height:140px;background:#f0f4f8;">
    <span id="phase">pending</span>
  </div>
</body>
</html>\`;

createServer((req, res) => {
  const url = req.url ?? '/';

  if (url.startsWith('/api/pfactory/tasks/${SPEC_ID}')) {
    // Advance phase each time the recorder polls
    const phase = phases[Math.min(phaseIdx, phases.length - 1)];
    phaseIdx = Math.min(phaseIdx + 1, phases.length - 1);
    const body = JSON.stringify({
      task_id: '${SPEC_ID}',
      project_id: '${PROJECT_ID}',
      spec_id: '${SPEC_ID}',
      status_json: { status: phase, phase: phase, updated_at: new Date().toISOString() },
      artefacts: {},
    });
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(body);
    return;
  }

  if (url === '/api/health') {
    res.writeHead(200);
    res.end('ok');
    return;
  }

  // All other paths → SPA shell with lane-status-grid
  res.writeHead(200, { 'Content-Type': 'text/html' });
  res.end(HTML);
}).listen(${HTTP_PORT}, '127.0.0.1', () => {
  console.log('HTTP mock listening on', ${HTTP_PORT});
});
EOF

# ── 3. Start mock servers ────────────────────────────────────────────────────
node /tmp/smoke_ws_server_$$.mjs &
WS_PID=$!
node /tmp/smoke_http_server_$$.mjs &
HTTP_PID=$!

# Give them a moment to bind
sleep 1.5

# ── 4. Run the recorder ──────────────────────────────────────────────────────
# Pass system Chrome so the test works without `npx playwright install`
CHROME_PATH="${PLAYWRIGHT_CHROMIUM_EXECUTABLE:-}"
if [[ -z "$CHROME_PATH" ]]; then
  CHROME_PATH="$(which google-chrome-stable 2>/dev/null || which google-chrome 2>/dev/null || which chromium-browser 2>/dev/null || which chromium 2>/dev/null || true)"
fi

RECORDER_EXIT=0
PLAYWRIGHT_CHROMIUM_EXECUTABLE="$CHROME_PATH" \
node --experimental-strip-types "$SCRIPT" \
  --project-id "$PROJECT_ID" \
  --spec-id "$SPEC_ID" \
  --token "$TOKEN" \
  --output-dir "$OUTDIR" \
  --portal-url "http://127.0.0.1:${HTTP_PORT}" \
  --ws-url "ws://127.0.0.1:${WS_PORT}/ws/events" \
  --max-wait-minutes 2 \
  || RECORDER_EXIT=$?

echo "[smoke] recorder exit=$RECORDER_EXIT"

# ── 5. Assert outputs ────────────────────────────────────────────────────────
PNG_COUNT=$(find "$OUTDIR" -name "*.png" | wc -l)
echo "[smoke] PNG files found: $PNG_COUNT"
ls "$OUTDIR"/*.png 2>/dev/null || true

# We expect at least 2 PNGs (fullpage + grid) per transition.
# The recorder fires on each distinct phase from status.json polling.
# With 8 phases and 2 PNGs each we'd get 16, but network timing means
# we may coalesce.  Assert at least 4 (2 transitions × 2 PNGs).
MIN_EXPECTED=4
if [[ "$PNG_COUNT" -lt "$MIN_EXPECTED" ]]; then
  echo "[FAIL] Expected at least $MIN_EXPECTED PNG files, got $PNG_COUNT"
  exit 1
fi

# Assert naming convention: seq-phase-fullpage.png and seq-phase-grid.png
FULLPAGE_COUNT=$(find "$OUTDIR" -name "*-fullpage.png" | wc -l)
GRID_COUNT=$(find "$OUTDIR" -name "*-grid.png" | wc -l)
if [[ "$FULLPAGE_COUNT" -lt 2 || "$GRID_COUNT" -lt 2 ]]; then
  echo "[FAIL] Expected both -fullpage.png and -grid.png files"
  echo "  fullpage=$FULLPAGE_COUNT  grid=$GRID_COUNT"
  exit 1
fi

echo "[PASS] smoke test passed — ${PNG_COUNT} PNGs captured (${FULLPAGE_COUNT} fullpage, ${GRID_COUNT} grid)"
exit 0
