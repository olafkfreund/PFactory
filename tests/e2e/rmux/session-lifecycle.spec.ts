/**
 * Scenario 1 — rmux session lifecycle.
 *
 * The session table in the rmux daemon must contain
 * ``pfactory-task-<spec_id>`` while the task is running, and the
 * registry's attach endpoint must resolve it.  After the task ends,
 * both should be cleaned up.
 *
 * This scenario is API-driven (no browser); it lives in this suite
 * because it exercises the same bridge contract the browser specs
 * depend on, and CI runs them together.
 */

import { test, expect } from './fixtures';

test.describe('rmux session lifecycle', () => {
  test('attach endpoint resolves the session while the task runs', async ({
    request,
    activeTask,
  }) => {
    // The activeTask fixture has already proven the session exists
    // (it polled /attach until 200/409).  This test just locks down
    // that we can attach + detach cleanly with a fresh connection_id.
    const attachRes = await request.post(
      `/api/tasks/${encodeURIComponent(activeTask.specId)}/agent-console/attach`,
      { data: { connection_id: 'lifecycle-test' } }
    );
    // Either we get the lock (200) or someone else already holds it
    // (409).  Both prove the session exists in the registry.
    expect(attachRes.status()).toBeOneOf([200, 409]);

    if (attachRes.status() === 200) {
      const body = await attachRes.json();
      expect(body.status).toBe('attached');
      expect(body.connection_id).toBe('lifecycle-test');

      const detachRes = await request.post(
        `/api/tasks/${encodeURIComponent(activeTask.specId)}/agent-console/detach`,
        { data: { connection_id: 'lifecycle-test' } }
      );
      expect(detachRes.status()).toBe(200);
      const detachBody = await detachRes.json();
      expect(detachBody.status).toBe('detached');
    }
  });

  test('unknown spec returns 404', async ({ request }) => {
    const r = await request.post(
      '/api/tasks/this-spec-does-not-exist-abcd/agent-console/attach',
      { data: { connection_id: 'doesnt-matter' } }
    );
    expect(r.status()).toBe(404);
  });
});
