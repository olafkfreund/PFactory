/**
 * PFactory planning-portal demo walkthrough.
 *
 * Drives the single-origin portal (SPA + API on one port, default
 * http://127.0.0.1:3198, override with PFACTORY_PORTAL_URL) via Playwright:
 *   1. seeds 3 demo sessions through the API so the List/Board/detail are populated,
 *   2. records a video while walking the UI and taking named per-screen PNGs.
 *
 * Output:
 *   docs/static/img/screenshots/NN-*.png   (per-screen stills)
 *   docs/static/recordings/tmp/*.webm      (raw video; make-demo.sh -> mp4/gif)
 *
 * Run: tsx scripts/demo-planning-walkthrough.ts   (server must be up; see make-demo.sh)
 */

import { chromium, type Page } from 'playwright';
import { readFileSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const PORTAL = process.env.PFACTORY_PORTAL_URL ?? 'http://127.0.0.1:3198';
const ROOT = join(__dirname, '..');
const SHOTS = join(ROOT, 'docs', 'static', 'img', 'screenshots');
const VIDEO_DIR = join(ROOT, 'docs', 'static', 'recordings', 'tmp');
const TOKEN = readFileSync(join(homedir(), '.pfactory', '.token'), 'utf8').trim();

mkdirSync(SHOTS, { recursive: true });
mkdirSync(VIDEO_DIR, { recursive: true });

// ── seed content ─────────────────────────────────────────────────────────
const INFRA_SOW = `# Statement of Work — Orders Platform: Multi-Region Microservices on EKS

A multi-region, highly-available orders platform on AWS EKS with disaster recovery.

## Acceptance Criteria
- AC#1: order-api, inventory, payments and fulfilment-worker each deploy to EKS with a Horizontal Pod Autoscaler and a default-deny NetworkPolicy.
- AC#2: The primary region runs RDS PostgreSQL Multi-AZ; a cross-region read replica supports fail-over (DR runbook documented).
- AC#3: ElastiCache Redis backs sessions and the scoreboard; traffic enters via a public ALB on 443 only.
- AC#4: Pods authenticate to AWS via IRSA; mTLS between services; secrets in AWS Secrets Manager; least-privilege IAM.
`;

// Deliberately free of cloud keywords (no deploy/microservice/redis/eks/…) so the
// AWS probe is skipped for this one — it then passes gates and can be approved,
// which surfaces the Emit tab for the taxonomy money-shot.
const FEATURE_PLAN = `# Refund flow — orders web app feature

Add a refund flow to the Orders web application.

## Acceptance Criteria
- AC#1: A "Refund" button on the order detail screen opens a confirmation dialog.
- AC#2: Only users with the finance role may issue a refund (role-based authorization).
- AC#3: Submitting a refund updates the order status to "refunded" and shows a toast.
- AC#4: Unit and integration tests cover the happy path and a double-refund guard.
`;

const SIMPLE_PLAN = `# Tic-tac-toe web game

A browser tic-tac-toe game.

## Acceptance Criteria
- AC#1: Two players alternate X and O on a 3x3 grid in the browser.
- AC#2: The game detects a win or a draw and shows the result.
`;

// ── API helpers ────────────────────────────────────────────────────────────
async function api(path: string, method = 'GET', body?: unknown): Promise<any> {
  const res = await fetch(PORTAL + path, {
    method,
    headers: body ? { 'content-type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${method} ${path} -> ${res.status} ${await res.text()}`);
  return res.json();
}

async function ingest(text: string, title: string, category = '', template = ''): Promise<string> {
  const s = await api('/api/plan/sessions/ingest-text', 'POST', { text, title, category, template, channel: 'portal' });
  return s.session_id;
}

async function seed(): Promise<{ A: string; B: string }> {
  console.log('seeding demo sessions…');
  const A = await ingest(INFRA_SOW, 'Orders Platform — Multi-Region EKS SOW', 'infrastructure', 'infra-change');
  await api(`/api/plan/sessions/${A}/process`, 'POST'); // live AWS feasibility ~20-40s
  console.log('  · A (infra SOW) processed');

  const B = await ingest(FEATURE_PLAN, 'Refund API feature', 'software');
  const bp = await api(`/api/plan/sessions/${B}/process`, 'POST');
  const bSession = bp.session ?? bp;
  if (bSession?.review?.gates_passed) {
    await api(`/api/plan/sessions/${B}/approve`, 'POST', { approver: 'olaf' });
    console.log('  · B (feature) processed + approved');
  } else {
    console.warn('  · B gates did not pass — Emit tab may be unavailable');
  }

  await ingest(SIMPLE_PLAN, 'Tic-tac-toe web game', 'software'); // C: fresh → Plans ready
  console.log('  · C (fresh) ingested');
  return { A, B };
}

// ── screenshot helpers ──────────────────────────────────────────────────────
async function shot(page: Page, name: string): Promise<void> {
  try {
    await page.screenshot({ path: join(SHOTS, `${name}.png`) });
    console.log(`  📸 ${name}`);
  } catch (e) {
    console.warn(`  ! shot ${name} failed:`, (e as Error).message);
  }
}
const settle = (page: Page, ms = 900) => page.waitForTimeout(ms);

async function clickTab(page: Page, name: string, panelTestId: string, file: string): Promise<void> {
  try {
    await page.getByRole('tab', { name }).first().click();
    await page.waitForSelector(`[data-testid="${panelTestId}"]`, { timeout: 8000 });
    await settle(page);
    await shot(page, file);
  } catch (e) {
    console.warn(`  ! tab ${name} skipped:`, (e as Error).message);
  }
}

// ── walkthrough ─────────────────────────────────────────────────────────────
async function main(): Promise<void> {
  const { A, B } = await seed();

  // On NixOS the npm-downloaded chromium can't load system libs (libnspr4 …); use
  // a working browser via executablePath (PFACTORY_CHROME), e.g. the Nix-built
  // chrome-headless-shell or google-chrome-stable.
  const executablePath = process.env.PFACTORY_CHROME || undefined;
  const browser = await chromium.launch(executablePath ? { executablePath } : {});
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    recordVideo: { dir: VIDEO_DIR, size: { width: 1440, height: 900 } },
  });
  await ctx.addInitScript((t: string) => {
    localStorage.setItem('pfactory-token', t);
    localStorage.setItem('pfactory-theme', 'dark');
    localStorage.setItem('pfactory-color-theme', 'gruvbox');
  }, TOKEN);

  const page = await ctx.newPage();
  await page.goto(PORTAL, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-testid="planning-view"]', { timeout: 30000 });
  await settle(page, 1500);
  await shot(page, '01-portal-list');

  // Configuration tabs (only in the empty/no-session state)
  for (const [tab, file] of [['Registry', '02-registry'], ['Templates', '03-templates'], ['Providers', '04-providers']] as const) {
    try {
      await page.getByRole('tab', { name: tab }).first().click();
      await settle(page);
      await shot(page, file);
    } catch (e) { console.warn(`  ! config ${tab}:`, (e as Error).message); }
  }

  // New-plan dialog: file mode, then text mode (shows category/template picker)
  try {
    await page.getByTestId('new-plan-btn').click();
    await page.waitForSelector('[data-testid="plan-upload-form"]', { timeout: 8000 });
    await settle(page);
    await shot(page, '05-new-plan-file');
    await page.getByRole('button', { name: 'Paste text' }).click();
    await settle(page);
    await shot(page, '06-new-plan-text');
    await page.keyboard.press('Escape');
    await settle(page, 500);
  } catch (e) { console.warn('  ! new-plan dialog:', (e as Error).message); }

  // Board view
  try {
    await page.getByRole('button', { name: 'Board' }).click();
    await page.waitForSelector('[data-testid="plan-board"]', { timeout: 8000 });
    await settle(page, 1200);
    await shot(page, '07-board');
    await page.getByRole('button', { name: 'List' }).click();
    await settle(page, 600);
  } catch (e) { console.warn('  ! board:', (e as Error).message); }

  // Session A — the fully-populated detail (all tabs)
  await openSession(page, A);
  await clickTab(page, 'Edit', 'plan-editor', '08-edit');
  await clickTab(page, 'Pipeline', 'pipeline-panel', '09-pipeline');
  await clickTab(page, 'Feasibility', 'feasibility-panel', '10-feasibility');
  await clickTab(page, 'AI Context', 'enrichment-panel', '11-ai-context');
  await clickTab(page, 'Review', 'review-panel', '12-review');
  await clickTab(page, 'Suggestions', 'annotation-panel', '13-suggestions');
  await clickTab(page, 'Approval', 'approval-panel', '14-approval');
  await goBack(page);

  // Session B — the Emit preview: run a dry-run so the tagged epic + children
  // (taxonomy label chips + pfactory:meta) actually render.
  await openSession(page, B);
  try {
    await page.getByRole('tab', { name: 'Emit' }).first().click();
    await page.waitForSelector('[data-testid="emit-panel"]', { timeout: 8000 });
    await page.getByTestId('emit-repo-input').fill('olafkfreund/demo-pfactory');
    await settle(page, 400);
    await page.getByTestId('emit-btn').click(); // aria-label is "Emit plan", so use testid
    // wait for the planned epic+children to render (label chips), then screenshot
    await page.waitForSelector('text=/pfactory/', { timeout: 8000 }).catch(() => {});
    await settle(page, 1200);
    await shot(page, '15-emit');
  } catch (e) { console.warn('  ! emit:', (e as Error).message); }
  await goBack(page);

  // Tasks board (renamed columns) — best-effort (project-scoped)
  try {
    await page.getByText('Tasks', { exact: true }).first().click();
    await settle(page, 1500);
    await shot(page, '16-tasks-board');
  } catch (e) { console.warn('  ! tasks board:', (e as Error).message); }

  await ctx.close(); // flush video
  await browser.close();
  console.log('walkthrough complete.');
}

async function openSession(page: Page, id: string): Promise<void> {
  try {
    await page.getByTestId(`session-row-${id}`).click();
    await page.waitForSelector('[data-testid="session-detail"]', { timeout: 10000 });
    await settle(page, 1000);
  } catch (e) { console.warn(`  ! open session ${id}:`, (e as Error).message); }
}

async function goBack(page: Page): Promise<void> {
  try {
    await page.getByLabel('Back to sessions').click();
    await settle(page, 600);
  } catch { /* already on list */ }
}

main().catch((e) => { console.error(e); process.exit(1); });
