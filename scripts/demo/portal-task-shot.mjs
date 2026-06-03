// Screenshot a PFactory task-detail view (clicks a task row, optional tab).
// Usage: CHROME_PATH=<chrome> FRONT_URL=http://localhost:8400 \
//   node scripts/demo/portal-task-shot.mjs <out.png> <rowText> [tabName]
import {chromium} from '@playwright/test';
import * as fs from 'node:fs';

const TOKEN = fs.readFileSync(process.env.HOME + '/.pfactory/.token', 'utf8').trim();
const OUT = process.argv[2] || '/tmp/task.png';
const ROW = process.argv[3] || '';
const TAB = process.argv[4] || '';
const FRONT = process.env.FRONT_URL || 'http://localhost:8400';
const CHROME = process.env.CHROME_PATH;

const browser = await chromium.launch({headless: true, executablePath: CHROME});
const ctx = await browser.newContext({viewport: {width: 1440, height: 900}});
const page = await ctx.newPage();
await page.addInitScript((t) => { try { localStorage.setItem('pfactory-token', t); } catch {} }, TOKEN);
try {
  await page.goto(FRONT + '/', {waitUntil: 'domcontentloaded'});
  await page.waitForTimeout(3000);
  if (ROW) {
    const row = page.getByText(ROW, {exact: false}).first();
    if (await row.count()) { await row.click(); await page.waitForTimeout(2500); }
  }
  if (TAB) {
    const tab = page.getByText(TAB, {exact: false}).first();
    if (await tab.count()) { await tab.click(); await page.waitForTimeout(1500); }
  }
  await page.waitForTimeout(1200);
  await page.screenshot({path: OUT, fullPage: false});
  console.log('SHOT_OK ' + OUT);
} finally {
  await browser.close();
}
