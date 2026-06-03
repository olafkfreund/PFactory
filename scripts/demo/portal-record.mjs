import {chromium} from '@playwright/test';
import * as fs from 'node:fs';
import * as path from 'node:path';

const TOKEN = fs.readFileSync(process.env.HOME + '/.pfactory/.token', 'utf8').trim();
const CHROME = process.env.CHROME_PATH;
const OUT = process.argv[2]; // target webm path
const FRONT = process.env.FRONT_URL || 'http://localhost:3100';
const SPEC = process.env.SPEC || ''; // when set, drill into this task + walk its tabs

const vdir = fs.mkdtempSync('/tmp/portalrec-');
const browser = await chromium.launch({headless: true, executablePath: CHROME});
const ctx = await browser.newContext({
  viewport: {width: 1366, height: 850},
  recordVideo: {dir: vdir, size: {width: 1366, height: 850}},
});
const page = await ctx.newPage();

// Pre-auth: inject the token so the SPA skips the login wall.
await page.addInitScript((t) => {
  try { localStorage.setItem('pfactory-token', t); } catch {}
}, TOKEN);

async function settle(ms) { await page.waitForTimeout(ms); }

try {
  await page.goto(FRONT + '/', {waitUntil: 'domcontentloaded'});
  await settle(1400);
  // If still on the login screen, fill the token field + login.
  const tokenInput = page.locator('input[placeholder="Enter your token"]');
  if (await tokenInput.count()) {
    await tokenInput.fill(TOKEN);
    await settle(300);
    const loginBtn = page.getByRole('button', {name: /login/i});
    if (await loginBtn.count()) { await loginBtn.click(); }
    await settle(1800);
  }
  await settle(1000);
  // Open the PFactory Tests/Tasks surface.
  for (const re of [/tests/i, /pfactory/i, /tasks/i, /triage/i]) {
    const link = page.getByRole('link', {name: re}).first();
    if (await link.count().catch(() => 0)) { await link.click().catch(() => {}); break; }
  }
  if (SPEC) {
    // Wait for the task list to actually LOAD (clears the "preparing your
    // workspace / initialising agents" splash) before drilling in — otherwise
    // the pane records the splash + the generic list, identical across demos.
    const row = page.getByText(SPEC, {exact: false}).first();
    try { await row.waitFor({state: 'visible', timeout: 20000}); } catch {}
    await settle(600);
    await row.click().catch(() => {});
    // Wait for the task detail, then open Verdicts — the distinctive content.
    const verdictsTab = page.getByText('Verdicts', {exact: false}).first();
    try { await verdictsTab.waitFor({state: 'visible', timeout: 15000}); } catch {}
    await verdictsTab.click().catch(() => {});
    await settle(1500);
    // LINGER on THIS scenario's verdicts: slow-scroll the 5-signal cards down,
    // back up, and partway down again — the bulk of the clip is scenario data.
    for (let i = 0; i < 7; i++) { await page.mouse.wheel(0, 300); await settle(820); }
    await page.mouse.wheel(0, -2100); await settle(900);
    for (let i = 0; i < 4; i++) { await page.mouse.wheel(0, 320); await settle(820); }
  } else {
    for (let i = 0; i < 3; i++) { await page.mouse.wheel(0, 300); await settle(900); }
  }
  await settle(1500);
} catch (e) {
  console.error('portal_rec warning:', e?.message || e);
}

await ctx.close(); // flush video
await browser.close();
const webm = fs.readdirSync(vdir).find((f) => f.endsWith('.webm'));
if (!webm) { console.error('no video produced'); process.exit(1); }
fs.copyFileSync(path.join(vdir, webm), OUT);
console.log('wrote', OUT);
