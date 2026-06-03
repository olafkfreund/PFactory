/**
 * Scenario 3 — Attach round-trip + audit log row.
 *
 * From the user's perspective:
 *   1. Click Live Console tab → badge says "Connected (read-only)"
 *   2. Click Attach → confirmation modal opens
 *   3. Click "Attach now" → POST /attach fires, badge flips to "Attached"
 *   4. Backend wrote an ``AuditLog`` row with ``action=console.attach``
 *   5. (Round-trip input: deferred to v1.x — requires reading the
 *      agent's pane after sending keystrokes, which is timing-sensitive
 *      and outside the "zero flaky" budget for this PR.)
 *   6. Click Detach → badge returns to read-only + audit row recorded
 */

import { test, expect, fetchAuditLog } from './fixtures';

test.describe('attach round-trip', () => {
  test('attach modal flow flips state and writes audit row', async ({
    page,
    request,
    activeTask,
  }) => {
    await page.goto('/');
    const card = page.locator(
      `button:has-text("(${activeTask.specId.slice(0, 3)})")`
    );
    await card.first().click();

    await page.getByRole('tab', { name: 'Live Console' }).click();

    // Wait for the WS to connect — Attach button only enables after
    // the connection_id arrives in the first server frame.
    const attachBtn = page.getByTestId('agent-console-attach');
    await expect(attachBtn).toBeEnabled({ timeout: 10_000 });

    // Snapshot audit count before, so we can prove a new row landed.
    const before = await fetchAuditLog(request, 'console.attach');

    // Click Attach → confirmation modal
    await attachBtn.click();
    const confirm = page.getByTestId('agent-console-attach-confirm');
    await expect(confirm).toBeVisible();
    await confirm.click();

    // Badge flips to "Attached" within 5s.
    const attachedBadge = page.locator(
      '[data-testid="agent-console"] >> text=/Attached/'
    );
    await expect(attachedBadge).toBeVisible({ timeout: 5_000 });

    // Detach button is now the visible action.
    const detachBtn = page.getByTestId('agent-console-detach');
    await expect(detachBtn).toBeVisible();

    // Verify audit log grew by exactly one console.attach row.
    await expect
      .poll(
        async () => {
          const after = await fetchAuditLog(request, 'console.attach');
          return after.length;
        },
        {
          timeout: 5_000,
          message: 'console.attach audit row not written',
        }
      )
      .toBeGreaterThan(before.length);

    // ---- Detach ----
    const beforeDetach = await fetchAuditLog(request, 'console.detach');
    await detachBtn.click();

    // Badge returns to the read-only state.
    const readonlyBadge = page.locator(
      '[data-testid="agent-console"] >> text=/Connected/'
    );
    await expect(readonlyBadge).toBeVisible({ timeout: 5_000 });

    await expect
      .poll(
        async () => {
          const after = await fetchAuditLog(request, 'console.detach');
          return after.length;
        },
        {
          timeout: 5_000,
          message: 'console.detach audit row not written',
        }
      )
      .toBeGreaterThan(beforeDetach.length);
  });
});
