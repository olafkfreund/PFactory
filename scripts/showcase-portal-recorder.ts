/**
 * showcase-portal-recorder.ts
 *
 * Playwright-headless recorder that:
 *   1. Opens the PFactory portal in a headless Chromium browser
 *   2. Subscribes to the backend WebSocket event stream
 *   3. Polls GET /api/pfactory/tasks/{specId} whenever a task:update /
 *      task:status WS event fires for the target task, detecting phase
 *      transitions in status_json.{status,phase}
 *   4. Takes two screenshots per transition:
 *        {seq:02d}-{phase}-fullpage.png
 *        {seq:02d}-{phase}-grid.png  (element: [data-testid="lane-status-grid"])
 *   5. Terminates on triaged / triaged_empty / triager_failed, timeout, or
 *      WS close error
 *
 * Real WS message shape (from apps/web-server/server/websockets/events.py):
 *   { "type": "<event_type>", "payload": { "taskId": "<id>", ... } }
 *
 * Phase polling endpoint:
 *   GET /api/pfactory/tasks/{specId}
 *   → { status_json: { status: string, phase: string|null, ... } }
 *
 * Usage (Node 22+ with --experimental-strip-types OR tsx):
 *   node --experimental-strip-types scripts/showcase-portal-recorder.ts \
 *     --project-id pfactory-demo \
 *     --spec-id 001-greeting-generator \
 *     --token "$(cat ~/.pfactory/.token)" \
 *     --output-dir ~/.pfactory/workspaces/pfactory-demo/specs/001-greeting-generator/findings/portal-screenshots \
 *     --portal-url http://localhost:3110 \
 *     --ws-url ws://localhost:3102/ws/events \
 *     --max-wait-minutes 15
 */

import { chromium } from 'playwright';
import type { Browser, BrowserContext, Page, ElementHandle } from 'playwright';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as https from 'node:https';
import * as http from 'node:http';
import { URL } from 'node:url';
import { WebSocket } from 'ws';

// ─── CLI argument parsing ────────────────────────────────────────────────────

interface CliArgs {
  projectId: string;
  specId: string;
  token: string;
  outputDir: string;
  portalUrl: string;
  wsUrl: string;
  maxWaitMinutes: number;
}

function parseArgs(argv: string[]): CliArgs {
  const args: Record<string, string> = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const key = a.slice(2);
      const val = argv[i + 1];
      if (val !== undefined && !val.startsWith('--')) {
        args[key] = val;
        i++;
      } else {
        args[key] = '';
      }
    }
  }

  const projectId = args['project-id'] ?? '';
  const specId = args['spec-id'] ?? '';
  if (!projectId) throw new Error('--project-id is required');
  if (!specId) throw new Error('--spec-id is required');

  const home = process.env['HOME'] ?? '/tmp';
  const defaultOutputDir = path.join(
    home,
    '.pfactory',
    'workspaces',
    projectId,
    'specs',
    specId,
    'findings',
    'portal-screenshots',
  );

  return {
    projectId,
    specId,
    token: args['token'] ?? '',
    outputDir: args['output-dir'] ? path.resolve(args['output-dir'].replace(/^~/, home)) : defaultOutputDir,
    portalUrl: args['portal-url'] ?? 'http://localhost:3110',
    wsUrl: args['ws-url'] ?? 'ws://localhost:3102/ws/events',
    maxWaitMinutes: args['max-wait-minutes'] ? Number(args['max-wait-minutes']) : 15,
  };
}

// ─── Phase helpers ──────────────────────────────────────────────────────────

const TERMINAL_PHASES = new Set([
  'triaged',
  'triaged_empty',
  'triager_failed',
  'stuck',
  'planner_failed',
  'gen_functional_failed',
]);

/**
 * Normalise status + phase from status_json into a single phase string.
 * The planner writes status=planning / planned; gen_functional writes
 * status=generating / generated; evaluator=evaluating / evaluated;
 * triager=triaging / triaged / triaged_empty / triager_failed.
 */
function resolvePhase(statusJson: Record<string, unknown>): string {
  const status = typeof statusJson['status'] === 'string' ? statusJson['status'] : '';
  const phase = typeof statusJson['phase'] === 'string' ? statusJson['phase'] : '';
  // Prefer `status` as it's more fine-grained in PFactory's pipeline
  return status || phase || 'pending';
}

// ─── HTTP helpers (plain node — no extra deps) ───────────────────────────────

function fetchJson(url: string, token: string): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const lib: typeof http | typeof https = parsed.protocol === 'https:' ? https : http;
    const opts: http.RequestOptions = {
      hostname: parsed.hostname,
      port: parsed.port,
      path: parsed.pathname + parsed.search,
      method: 'GET',
      headers: {
        Authorization: token ? `Bearer ${token}` : '',
        Accept: 'application/json',
      },
    };
    const req = lib.request(opts, (res) => {
      const chunks: Buffer[] = [];
      res.on('data', (c: Buffer) => chunks.push(c));
      res.on('end', () => {
        const body = Buffer.concat(chunks).toString('utf8');
        if (!res.statusCode || res.statusCode >= 400) {
          reject(new Error(`HTTP ${res.statusCode ?? '?'} from ${url}: ${body}`));
          return;
        }
        try {
          resolve(JSON.parse(body) as Record<string, unknown>);
        } catch {
          reject(new Error(`Non-JSON body from ${url}: ${body.slice(0, 200)}`));
        }
      });
    });
    req.on('error', reject);
    req.end();
  });
}

// ─── Screenshot logic ────────────────────────────────────────────────────────

interface ScreenshotResult {
  fullpageName: string;
  gridName: string;
  fullpageDims: string;
  gridDims: string;
}

async function captureScreenshots(
  page: Page,
  outputDir: string,
  seq: number,
  phase: string,
): Promise<ScreenshotResult> {
  // Wait for React to settle
  await page.waitForTimeout(500);

  const seqStr = String(seq).padStart(2, '0');
  const safephase = phase.replace(/[^A-Za-z0-9_-]/g, '_');
  const fullpageName = `${seqStr}-${safephase}-fullpage.png`;
  const gridName = `${seqStr}-${safephase}-grid.png`;
  const fullpagePath = path.join(outputDir, fullpageName);
  const gridPath = path.join(outputDir, gridName);

  // Full-page screenshot
  await page.screenshot({ path: fullpagePath, fullPage: true });
  const fpBuf = fs.readFileSync(fullpagePath);
  const fpDims = await getImageDims(fpBuf);

  // Grid element screenshot — fall back to full page if grid not present
  let gridDims = fpDims;
  const gridEl: ElementHandle<HTMLElement> | null = await page.$('[data-testid="lane-status-grid"]');
  if (gridEl !== null) {
    await gridEl.screenshot({ path: gridPath });
    const gridBuf = fs.readFileSync(gridPath);
    gridDims = await getImageDims(gridBuf);
  } else {
    // Copy the full-page screenshot as a fallback so the sequence is complete
    fs.copyFileSync(fullpagePath, gridPath);
  }

  return { fullpageName, gridName, fullpageDims: fpDims, gridDims };
}

/** Read width×height from a PNG buffer (bytes 16-24). */
function getImageDims(buf: Buffer): Promise<string> {
  return new Promise((resolve) => {
    if (buf.length < 24) {
      resolve('?×?');
      return;
    }
    // PNG signature is 8 bytes; IHDR chunk starts at byte 8.
    // Width at offset 16, height at offset 20 (big-endian uint32).
    const w = buf.readUInt32BE(16);
    const h = buf.readUInt32BE(20);
    resolve(`${w}×${h}`);
  });
}

// ─── Retrospective single-shot capture (pipeline already done) ──────────────

async function captureRetroshot(
  page: Page,
  outputDir: string,
  phase: string,
): Promise<void> {
  console.log(`[retrospective] Pipeline already at phase=${phase} — capturing final state`);
  try {
    await page.reload({ waitUntil: 'networkidle', timeout: 10_000 });
  } catch {
    // timeout is fine — just capture whatever is there
  }
  const res = await captureScreenshots(page, outputDir, 1, phase);
  console.log(
    `[seq=01] phase=${phase} → saved ${res.fullpageName} + ${res.gridName}` +
    ` (${res.fullpageDims} + ${res.gridDims})`,
  );
}

// ─── WebSocket subscriber ───────────────────────────────────────────────────

interface WsEvent {
  type: string;
  payload: Record<string, unknown>;
}

function parseWsMessage(raw: string): WsEvent | null {
  try {
    const obj = JSON.parse(raw) as unknown;
    if (
      obj !== null &&
      typeof obj === 'object' &&
      'type' in obj &&
      typeof (obj as Record<string, unknown>)['type'] === 'string'
    ) {
      const o = obj as Record<string, unknown>;
      return {
        type: o['type'] as string,
        payload: (typeof o['payload'] === 'object' && o['payload'] !== null
          ? o['payload']
          : {}) as Record<string, unknown>,
      };
    }
  } catch {
    // ignore malformed messages
  }
  return null;
}

// ─── Auth helpers ────────────────────────────────────────────────────────────

/**
 * Authenticate the portal page.
 *
 * The portal's auth-store reads the token from localStorage key
 * `pfactory-auth-token` on load.  We also try navigating with a
 * `?token=` query param as a belt-and-suspenders measure.
 */
async function authenticatePage(page: Page, portalUrl: string, token: string): Promise<void> {
  if (token) {
    // Inject token before the app boots so the auth-store picks it up
    await page.goto(portalUrl, { waitUntil: 'domcontentloaded', timeout: 20_000 });
    await page.evaluate((tok: string) => {
      localStorage.setItem('pfactory-auth-token', tok);
      // Some builds also read 'token' directly
      localStorage.setItem('token', tok);
    }, token);
  }
  // Navigate to the portal root (or with token param as fallback)
  const navUrl = token ? `${portalUrl}?token=${encodeURIComponent(token)}` : portalUrl;
  try {
    await page.goto(navUrl, { waitUntil: 'networkidle', timeout: 20_000 });
  } catch {
    // networkidle can time out on heavy apps — accept domcontentloaded
    await page.goto(navUrl, { waitUntil: 'domcontentloaded', timeout: 20_000 });
  }
}

// ─── Main ────────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  const argv = process.argv.slice(2);
  const cli = parseArgs(argv);

  // Prepare output directory (overwrite mode — wipe existing PNGs)
  fs.mkdirSync(cli.outputDir, { recursive: true });
  for (const f of fs.readdirSync(cli.outputDir)) {
    if (f.endsWith('.png')) fs.unlinkSync(path.join(cli.outputDir, f));
  }

  const apiBase = cli.portalUrl.replace(/\/$/, '');
  const taskDetailUrl = `${apiBase}/api/pfactory/tasks/${cli.specId}`;

  // ── Pre-flight: check if pipeline already completed ──────────────────────
  let initialPhase: string | null = null;
  try {
    const detail = await fetchJson(taskDetailUrl, cli.token);
    const statusJson = detail['status_json'] as Record<string, unknown> | undefined;
    if (statusJson) {
      const p = resolvePhase(statusJson);
      if (TERMINAL_PHASES.has(p)) {
        initialPhase = p;
      }
    }
  } catch {
    // Spec not found yet — proceed to watch mode
  }

  let browser: Browser | null = null;
  let ctx: BrowserContext | null = null;
  let page: Page | null = null;

  try {
    // Support system Chrome (NixOS / CI) via env var; Playwright's bundled
  // Chromium requires a separate `npx playwright install` step that may not
  // have been run.  The existing record-portal-walkthrough.ts uses the same
  // pattern (PLAYWRIGHT_CHROMIUM_EXECUTABLE).
  const executablePath = process.env['PLAYWRIGHT_CHROMIUM_EXECUTABLE'];
  browser = await chromium.launch({
    headless: true,
    ...(executablePath ? { executablePath } : {}),
  });
    ctx = await browser.newContext({
      viewport: { width: 1440, height: 900 },
    });
    page = await ctx.newPage();

    await authenticatePage(page, cli.portalUrl, cli.token);

    if (initialPhase !== null) {
      await captureRetroshot(page, cli.outputDir, initialPhase);
      console.log('Captured 1 phase transition over 0:00 (retrospective)');
      return;
    }

    // ── Watch mode ─────────────────────────────────────────────────────────
    //
    // Phase transitions are enqueued and processed serially so that rapid-fire
    // WS events don't cause concurrent Playwright operations.  The browser
    // only closes after the serial capture chain finishes.
    //
    let seq = 0;
    let lastPhase = '';
    const startMs = Date.now();
    const maxMs = cli.maxWaitMinutes * 60 * 1000;

    // captureChain is a promise-chain that serialises all screenshot work.
    // Each new capture is appended as .then() so they execute one at a time.
    let captureChain: Promise<void> = Promise.resolve();

    // Set to true when we want no more captures enqueued (abort / timeout).
    let aborted = false;

    // Resolves the outer await-for-WS promise.
    let outerResolve!: () => void;
    let outerReject!: (e: Error) => void;

    const wsUrl = `${cli.wsUrl}?token=${encodeURIComponent(cli.token)}`;
    console.log(`Connecting to WebSocket: ${wsUrl}`);

    // Wait for both:
    //  1. The WS to indicate we should stop accepting events (finishWs)
    //  2. The capture chain to drain (captureChain)
    const wsSettled = new Promise<Error | null>((res) => {
      outerResolve = () => res(null);
      outerReject = (e) => res(e);
    });

    const ws = new WebSocket(wsUrl);
    let pingInterval: ReturnType<typeof setInterval> | null = null;
    let timeoutHandle: ReturnType<typeof setTimeout> | null = null;

    function stopWs(): void {
      if (pingInterval !== null) { clearInterval(pingInterval); pingInterval = null; }
      if (timeoutHandle !== null) { clearTimeout(timeoutHandle); timeoutHandle = null; }
      aborted = true;
      try { ws.close(); } catch { /* ignore */ }
    }

    // Max-wait timeout
    timeoutHandle = setTimeout(() => {
      console.error(
        `[timeout] Recorder hit --max-wait-minutes=${cli.maxWaitMinutes}. ` +
        `Captured ${seq} transitions so far.`,
      );
      stopWs();
      outerReject(new Error('timeout'));
    }, maxMs);

    ws.on('open', () => {
      console.log('WebSocket connected');
      pingInterval = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send('ping');
      }, 25_000);
    });

    ws.on('message', (rawData: Buffer | string) => {
      if (aborted) return;
      const raw = typeof rawData === 'string' ? rawData : rawData.toString('utf8');
      if (raw === 'pong') return;

      const evt = parseWsMessage(raw);
      if (!evt) return;

      const isTaskEvent = [
        'task:update',
        'task:status',
        'task:progress',
        'task:log',
      ].includes(evt.type);

      if (!isTaskEvent) return;

      const payloadTaskId =
        typeof evt.payload['taskId'] === 'string' ? evt.payload['taskId'] : null;

      // Accept if payload.taskId matches specId, or if there's no taskId
      // (broadcast to all) — this makes the smoke test easier too.
      if (payloadTaskId !== null && payloadTaskId !== cli.specId) return;

      // Poll the REST endpoint for authoritative phase info
      fetchJson(taskDetailUrl, cli.token)
        .then((detail) => {
          if (aborted) return;
          const statusJson = detail['status_json'] as Record<string, unknown> | undefined;
          if (!statusJson) return;
          const phase = resolvePhase(statusJson);

          if (phase === lastPhase) return; // no transition
          lastPhase = phase;
          seq++;

          const capturedSeq = seq;
          const capturedPhase = phase;
          const isTerminal = TERMINAL_PHASES.has(phase);

          const elapsedMs = Date.now() - startMs;
          const elapsedSec = Math.floor(elapsedMs / 1000);
          const mm = String(Math.floor(elapsedSec / 60)).padStart(1, '0');
          const ss = String(elapsedSec % 60).padStart(2, '0');
          console.log(`[seq=${String(capturedSeq).padStart(2, '0')}] phase=${capturedPhase} at ${mm}:${ss}`);

          // Append to the serial capture chain
          captureChain = captureChain.then(async () => {
            if (aborted && !isTerminal) return; // skip non-terminal if aborted
            try {
              const currentPage = page!;
              try {
                await currentPage.reload({ waitUntil: 'domcontentloaded', timeout: 15_000 });
              } catch {
                // continue even if reload times out
              }
              const res = await captureScreenshots(
                currentPage, cli.outputDir, capturedSeq, capturedPhase,
              );
              console.log(
                `[seq=${String(capturedSeq).padStart(2, '0')}] phase=${capturedPhase} → ` +
                `saved ${res.fullpageName} + ${res.gridName}` +
                ` (${res.fullpageDims} + ${res.gridDims})`,
              );
            } catch (captureErr: unknown) {
              // Capture errors are non-fatal — log and continue to next phase
              console.error('[capture error]', captureErr);
            }

            if (isTerminal) {
              const totalSec = Math.floor((Date.now() - startMs) / 1000);
              const tm = String(Math.floor(totalSec / 60)).padStart(1, '0');
              const ts = String(totalSec % 60).padStart(2, '0');
              console.log(`Captured ${capturedSeq} phase transitions over ${tm}:${ts}`);
              stopWs();
              outerResolve();
            }
          });
        })
        .catch((pollErr: unknown) => {
          console.warn('[poll warn]', String(pollErr).slice(0, 120));
        });
    });

    ws.on('error', (err: Error) => {
      console.error('[ws error]', err.message);
      stopWs();
      outerReject(err);
    });

    ws.on('close', (code: number, reason: Buffer) => {
      if (aborted) return; // we initiated the close — already handled
      // Normal close (1000) from mock server — wait for capture chain, then finish
      if (code === 1000) {
        stopWs();
        captureChain.then(() => outerResolve()).catch(() => outerResolve());
      } else {
        const msg = `WebSocket closed: code=${code} reason=${reason.toString('utf8').slice(0, 80)}`;
        console.error(msg);
        stopWs();
        outerReject(new Error(msg));
      }
    });

    // Wait for WS to settle, then let the capture chain finish
    const wsErr = await wsSettled;
    await captureChain; // drain any in-flight captures

    if (wsErr !== null) {
      throw wsErr;
    }

  } finally {
    if (page) await page.close().catch(() => undefined);
    if (ctx) await ctx.close().catch(() => undefined);
    if (browser) await browser.close().catch(() => undefined);
  }
}

main().catch((err: unknown) => {
  console.error('[fatal]', err instanceof Error ? err.message : String(err));
  process.exit(1);
});
