import { spawn } from 'node:child_process';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { chromium } from '@playwright/test';

const port = 4177;
const base = `http://127.0.0.1:${port}`;
const out = await mkdtemp(join(tmpdir(), 'pfactory-shell-browser-'));
const server = spawn('npm', ['run', 'dev', '--', '--host', '127.0.0.1', '--port', String(port), '--strictPort'], { cwd: new URL('..', import.meta.url), stdio: 'ignore' });

const project = {
  id: 'alpha', name: 'Alpha', path: '/src/alpha', autoBuildPath: '.pfactory',
  settings: { model: 'claude-sonnet-4-5-20250929', memoryBackend: 'file', notifications: {}, graphitiMcpEnabled: false },
  createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
};
const sessions = Array.from({ length: 9 }, (_, i) => ({
  session_id: `done-${i}`, project_id: 'alpha', title: `Done ${i}`, status: 'approved', board_state: 'done',
  target_kind: 'software', plan_type: 'feature', children: 2, gates_passed: true, created_at: '2026-06-01T10:00:00Z',
}));

async function dismissOverlays(page) {
  for (let i = 0; i < 4; i++) {
    if (await page.getByRole('button', { name: 'Skip for now' }).isVisible()) {
      await page.getByRole('button', { name: 'Skip for now' }).click();
    } else if (await page.getByRole('button', { name: 'Skip Setup' }).isVisible()) {
      await page.getByRole('button', { name: 'Skip Setup' }).click();
    } else break;
  }
  if (await page.locator('[role="dialog"]').count()) throw new Error('Setup overlay still covers the board');
}

let browser;
try {
  let ready = false;
  for (let i = 0; i < 100; i++) {
    try { ready = (await fetch(base)).ok; } catch { /* server starting */ }
    if (ready) break;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  if (!ready) throw new Error('Vite did not start');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || '/run/current-system/sw/bin/chromium', headless: true, args: ['--no-sandbox'] });

  for (const width of [1280, 390]) {
    for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: theme });
      await context.route('**/api/**', async (route) => {
        const path = new URL(route.request().url()).pathname;
        let body = {};
        if (path === '/api/projects') body = [project];
        else if (path === '/api/settings') body = { onboardingCompleted: true, theme };
        else if (path === '/api/plan/sessions') body = { sessions };
        else if (path === '/api/pfactory/tasks') body = { tasks: [{ task_id: 'task-1', project_id: 'alpha', spec_id: 'run-1', status: 'planning', phase: 'planner', updated_at: '2026-06-01T10:00:00Z' }], count: 1 };
        else if (path === '/api/pfactory/tasks/run-1') body = { task_id: 'task-1', project_id: 'alpha', spec_id: 'run-1', status_json: { status: 'planning' }, artefacts: {} };
        else if (path === '/api/llm-endpoints') body = [];
        else if (path === '/api/claude-code/version') body = { installed: '1.0.0' };
        else if (path === '/api/settings/auth-status') body = { hasToken: true };
        else if (path === '/api/git/status') body = { isGitRepo: true, hasCommits: true };
        else if (path.endsWith('/tasks')) body = [];
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
      });
      const page = await context.newPage();
      await page.addInitScript(() => {
        window.__logCloses = 0;
        const NativeWebSocket = window.WebSocket;
        window.WebSocket = class extends NativeWebSocket {
          close(...args) {
            if (this.url.includes('/pfactory/tasks/run-1/logs/stream')) window.__logCloses++;
            return super.close(...args);
          }
        };
      });
      page.on('pageerror', (error) => console.error(error.stack));
      await page.goto(`${base}/p/alpha/planning`);
      await page.getByTestId('plan-board').waitFor();
      await dismissOverlays(page);
      await page.getByTestId('plan-board').waitFor();
      await page.getByText('Show 1 more').waitFor();
      await page.locator('[aria-label="Expand alpha · Done 0"]').click();
      await page.reload();
      await dismissOverlays(page);
      await page.locator('[data-session-id="done-0"] .f-card--expanded').waitFor();
      if (await page.evaluate(() => document.documentElement.classList.contains('dark')) !== (theme === 'dark')) throw new Error(`${width}px ${theme}: wrong theme`);
      await page.keyboard.press('Tab');
      if (!(await page.evaluate(() => document.activeElement instanceof HTMLElement && document.activeElement.matches('button, a, input')))) throw new Error('Keyboard focus did not reach a control');
      await page.screenshot({ path: join(out, `${width}-${theme}.png`) });
      await page.goto(`${base}/p/alpha/settings`);
      await page.getByTestId('project-settings-page').waitFor();
      await page.goto(`${base}/preferences`);
      await page.getByTestId('preferences-page').waitFor();
      await page.screenshot({ path: join(out, `${width}-${theme}-preferences.png`) });
      if (width === 1280 && theme === 'light') {
        await page.goto(`${base}/pfactory`);
        await page.getByTestId('task-row-run-1').click();
        await page.getByRole('tab', { name: 'Logs' }).click();
        await page.getByTestId('panel-logs').waitFor();
        const closes = await page.evaluate(() => window.__logCloses);
        await page.locator('.f-user-menu__trigger').click();
        await page.locator('.f-user-menu__item', { hasText: 'Preferences' }).click();
        await page.getByTestId('preferences-page').waitFor();
        await page.getByRole('button', { name: 'Plans' }).click();
        await page.getByTestId('panel-logs').waitFor();
        if (await page.evaluate(() => window.__logCloses) !== closes) throw new Error('Plans log socket closed during navigation');
      }
      await context.close();
    }
  }

  const frames = ['1280-light', '1280-dark', '390-light', '390-dark'];
  const inputs = frames.flatMap((name) => ['-loop', '1', '-t', '1', '-i', join(out, `${name}.png`)]);
  const filters = frames.map((_, i) => `[${i}:v]scale=1280:900:force_original_aspect_ratio=decrease,pad=1280:900:(ow-iw)/2:(oh-ih)/2[v${i}]`).join(';');
  const ffmpeg = spawn('ffmpeg', ['-y', ...inputs, '-filter_complex', `${filters};${frames.map((_, i) => `[v${i}]`).join('')}concat=n=4:v=1:a=0,fps=1[out]`, '-map', '[out]', join(out, 'shared-shell.gif')], { stdio: 'ignore' });
  if (await new Promise((resolve) => ffmpeg.on('exit', resolve)) !== 0) throw new Error('GIF recording failed');
  console.log(`Browser gate passed; screenshots and GIF: ${out}`);
} finally {
  await browser?.close();
  server.kill('SIGTERM');
}
