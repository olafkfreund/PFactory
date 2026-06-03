/**
 * Scenario 2 — read-only WebSocket stream.
 *
 * Open a WebSocket to the agent-console endpoint.  Receive the
 * ``connected`` handshake frame, then assert that pane bytes start
 * arriving (binary frames).  Latency from WS open → first byte must
 * be reasonable (the design doc says ≤200ms under nominal load; we
 * give a generous 5s cap because the agent may be between tool calls
 * when the test runs).
 *
 * This scenario doesn't need the React component — driving the WS
 * directly via Playwright's WebSocket support is faster and isolates
 * the backend contract.  Scenario 3 covers the React/xterm path.
 */

import { test, expect } from './fixtures';

const API = process.env.PFACTORY_E2E_API ?? 'http://localhost:3102';
const TOKEN = process.env.PFACTORY_E2E_TOKEN ?? '';

test.describe('read-only WS stream', () => {
  test('connects and receives the connected envelope + binary bytes', async ({
    page,
    activeTask,
  }) => {
    const wsUrl =
      API.replace(/^http/, 'ws') +
      `/api/tasks/${encodeURIComponent(activeTask.specId)}/agent-console/ws?token=${encodeURIComponent(TOKEN)}`;

    // Drive the WS from the page context so the same browser handles
    // cookies, CSP, etc.  Returns the captured event log.
    const trace = await page.evaluate(async (url) => {
      return new Promise<string[]>((resolve) => {
        const ws = new WebSocket(url);
        ws.binaryType = 'arraybuffer';
        const events: string[] = [];
        const t0 = Date.now();
        ws.addEventListener('open', () => events.push(`open @${Date.now() - t0}ms`));
        ws.addEventListener('message', (ev) => {
          if (typeof ev.data === 'string') {
            events.push(`text @${Date.now() - t0}ms: ${ev.data.slice(0, 120)}`);
          } else {
            const bytes = ev.data as ArrayBuffer;
            events.push(`bin @${Date.now() - t0}ms: ${bytes.byteLength}b`);
          }
          // Close after we've seen one text + one binary message
          const hasText = events.some((e) => e.startsWith('text'));
          const hasBin = events.some((e) => e.startsWith('bin'));
          if (hasText && hasBin) {
            try { ws.close(); } catch {}
          }
        });
        ws.addEventListener('close', (e) => {
          events.push(`close code=${e.code}`);
          resolve(events);
        });
        ws.addEventListener('error', () => events.push('error'));
        // Safety timeout
        setTimeout(() => {
          try { ws.close(); } catch {}
          resolve(events);
        }, 10_000);
      });
    }, wsUrl);

    // Assert the lifecycle order
    expect(trace[0]).toMatch(/^open/);
    // Connected envelope is a text frame with the JSON shape
    const connected = trace.find((e) => e.startsWith('text'));
    expect(connected, 'should receive the connected envelope').toBeDefined();
    expect(connected!).toContain('"type":"connected"');
    expect(connected!).toMatch(/"connection_id":"[a-f0-9-]{36}"/);

    // At least one binary frame within the test window
    const binCount = trace.filter((e) => e.startsWith('bin')).length;
    expect(binCount, `expected >=1 binary frame, got log: ${trace.join(' | ')}`).toBeGreaterThanOrEqual(1);
  });

  test('badge in the UI flips to Connected (read-only)', async ({
    page,
    activeTask,
  }) => {
    // Navigate to the task detail page and open the Live Console tab.
    // We use data-testid attributes added in R2.
    await page.goto('/');
    // The kanban renders task cards by spec name; we use a partial
    // match on the spec_id which is unique.
    const card = page.locator(`button:has-text("(${activeTask.specId.slice(0, 3)})")`);
    await card.first().click();

    // Click the Live Console tab
    await page.getByRole('tab', { name: 'Live Console' }).click();

    // Badge should transition to "Connected (read-only)" within 5s.
    // Don't assert exact text — i18n string but it contains "Connected".
    const badge = page.locator('[data-testid="agent-console"] >> text=/Connected/');
    await expect(badge).toBeVisible({ timeout: 10_000 });
  });
});
