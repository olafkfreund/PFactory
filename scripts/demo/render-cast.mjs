/**
 * render-cast.mjs — fallback renderer for cast-to-video.sh.
 *
 * Plays an asciinema .cast in a headless Chromium page (via asciinema-player)
 * and screen-records it to a webm. Used only when `agg` is unavailable.
 *
 *   node scripts/demo/render-cast.mjs INPUT.cast OUTPUT.webm
 *
 * asciinema-player is loaded from a local install if present
 * (node_modules/asciinema-player), else from a pinned jsDelivr CDN URL — so
 * the first run needs network access.
 *
 * On NixOS the bundled Chromium often fails to launch; set
 * PLAYWRIGHT_CHROMIUM_EXECUTABLE to the nix-managed Chrome/Chromium binary,
 * the same way scripts/record-portal-walkthrough.ts does.
 */
import {chromium} from '@playwright/test';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as os from 'node:os';

const PLAYER_VERSION = '3.7.1';
const CDN_CSS = `https://cdn.jsdelivr.net/npm/asciinema-player@${PLAYER_VERSION}/dist/bundle/asciinema-player.css`;
const CDN_JS = `https://cdn.jsdelivr.net/npm/asciinema-player@${PLAYER_VERSION}/dist/bundle/asciinema-player.min.js`;

async function main() {
  const [, , castPath, outPath] = process.argv;
  if (!castPath || !outPath) {
    console.error('usage: render-cast.mjs IN.cast OUT.webm');
    process.exit(2);
  }
  const cast = fs.readFileSync(castPath, 'utf8');

  // Compute cast duration so we know when playback finishes. Both asciicast
  // v2 and v3 are NDJSON (header line then event lines), but they differ:
  //   v2: [absolute_time, code, data]      → total = last absolute time
  //   v3: [interval_delta, code, data]     → total = sum of interval deltas
  let duration = 20;
  try {
    const lines = cast.trim().split('\n');
    let version = 2;
    try { version = JSON.parse(lines[0]).version || 2; } catch { /* keep v2 */ }
    let total = 0;
    for (const ln of lines.slice(1)) {
      const ev = JSON.parse(ln);
      if (!Array.isArray(ev) || typeof ev[0] !== 'number') continue;
      total = version >= 3 ? total + ev[0] : ev[0];
    }
    if (total > 0) duration = Math.ceil(total) + 2;
  } catch {
    /* fall back to default */
  }

  // Prefer a locally installed player bundle; else CDN.
  const localDir = path.resolve('node_modules/asciinema-player/dist/bundle');
  const localJs = path.join(localDir, 'asciinema-player.min.js');
  const localCss = path.join(localDir, 'asciinema-player.css');
  const haveLocal = fs.existsSync(localJs) && fs.existsSync(localCss);

  const cssTag = haveLocal
    ? `<style>${fs.readFileSync(localCss, 'utf8')}</style>`
    : `<link rel="stylesheet" href="${CDN_CSS}">`;
  const jsTag = haveLocal
    ? `<script>${fs.readFileSync(localJs, 'utf8')}</script>`
    : `<script src="${CDN_JS}"></script>`;

  const castB64 = Buffer.from(cast, 'utf8').toString('base64');
  const html = `<!doctype html><html><head><meta charset="utf-8">${cssTag}
<style>html,body{margin:0;background:#0d1117}#term{width:1000px}</style></head>
<body><div id="term"></div>${jsTag}
<script>
  const castText = atob("${castB64}");
  const url = "data:text/plain;base64,${castB64}";
  AsciinemaPlayer.create(url, document.getElementById('term'),
    { autoPlay: true, loop: false, speed: 1.3, fit: 'width', theme: 'monokai', controls: false });
</script></body></html>`;

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'castrender-'));
  const htmlPath = path.join(tmp, 'index.html');
  fs.writeFileSync(htmlPath, html);
  const videoDir = path.join(tmp, 'video');
  fs.mkdirSync(videoDir, {recursive: true});

  const executablePath = process.env['PLAYWRIGHT_CHROMIUM_EXECUTABLE'] || undefined;
  const browser = await chromium.launch({headless: true, executablePath});
  const context = await browser.newContext({
    viewport: {width: 1000, height: 600},
    recordVideo: {dir: videoDir, size: {width: 1000, height: 600}},
  });
  const page = await context.newPage();
  await page.goto('file://' + htmlPath, {waitUntil: 'load'});
  await page.waitForTimeout(Math.min(duration, 90) * 1000);
  await context.close(); // flushes the .webm
  await browser.close();

  const webm = fs.readdirSync(videoDir).find((f) => f.endsWith('.webm'));
  if (!webm) {
    console.error('render-cast.mjs: Playwright produced no webm');
    process.exit(1);
  }
  fs.copyFileSync(path.join(videoDir, webm), outPath);
  console.log('render-cast.mjs: wrote', outPath);
}

main().catch((e) => {
  console.error('render-cast.mjs:', e?.message || e);
  process.exit(1);
});
