// Record a self-playing local HTML page to a webm (for animated panes).
// Usage: CHROME_PATH=<chrome> node record-html.mjs <in.html> <out.webm> [seconds] [w] [h]
import {chromium} from '@playwright/test';
import * as fs from 'node:fs';
import * as path from 'node:path';

const IN = path.resolve(process.argv[2]);
const OUT = process.argv[3];
const SECS = Number(process.argv[4] || 9);
const W = Number(process.argv[5] || 960);
const H = Number(process.argv[6] || 720);
const CHROME = process.env.CHROME_PATH;

const vdir = fs.mkdtempSync('/tmp/htmlrec-');
const browser = await chromium.launch({headless: true, executablePath: CHROME});
const ctx = await browser.newContext({
  viewport: {width: W, height: H},
  recordVideo: {dir: vdir, size: {width: W, height: H}},
});
const page = await ctx.newPage();
try {
  await page.goto('file://' + IN, {waitUntil: 'domcontentloaded'});
  await page.waitForTimeout(SECS * 1000);
} catch (e) {
  console.error('record-html warning:', e?.message || e);
}
await ctx.close(); // flush video
await browser.close();
const webm = fs.readdirSync(vdir).find((f) => f.endsWith('.webm'));
if (!webm) { console.error('no video produced'); process.exit(1); }
fs.copyFileSync(path.join(vdir, webm), OUT);
console.log('wrote', OUT);
