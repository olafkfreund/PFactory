// Capture a themed portal screenshot. Usage:
//   CHROME_PATH=<chrome> node scripts/demo/portal-screenshot.mjs <out.png> [url]
import {chromium} from '@playwright/test';
import * as fs from 'node:fs';

const TOKEN = fs.readFileSync(process.env.HOME + '/.pfactory/.token', 'utf8').trim();
const OUT = process.argv[2] || '/tmp/portal-themed.png';
const URL = process.argv[3] || 'http://localhost:3100';
const CHROME = process.env.CHROME_PATH;

const browser = await chromium.launch({headless: true, executablePath: CHROME});
const ctx = await browser.newContext({viewport: {width: 1440, height: 900}});
const page = await ctx.newPage();
await page.addInitScript((t) => {
  try { localStorage.setItem('pfactory-token', t); } catch {}
}, TOKEN);
try {
  await page.goto(URL + '/', {waitUntil: 'domcontentloaded'});
  await page.waitForTimeout(2500);
  const tokenInput = page.locator('input[placeholder="Enter your token"]');
  if (await tokenInput.count()) {
    await tokenInput.fill(TOKEN);
    await page.waitForTimeout(400);
    const loginBtn = page.getByRole('button', {name: /login/i});
    if (await loginBtn.count()) await loginBtn.click();
    await page.waitForTimeout(3000);
  }
  await page.waitForTimeout(2000);
  await page.screenshot({path: OUT, fullPage: false});
  console.log('SCREENSHOT_OK ' + OUT);
} finally {
  await browser.close();
}
