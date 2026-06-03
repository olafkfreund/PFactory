/**
 * Shared fixtures for the rmux E2E scenarios.
 *
 * The ``activeTask`` fixture self-seeds: it registers a fixture project,
 * creates a backlog task, and starts it.  The web-server side honours
 * ``PFACTORY_TEST_AGENT_CMD`` (typically ``sleep 300``) which replaces
 * the agent subprocess with a NOOP — so the rmux session is created
 * (the integration hook fires on ``start_task_execution`` regardless of
 * what the subprocess actually does) but no LLM calls happen.
 *
 * Teardown stops the task.  Project + spec dirs are left for inspection
 * if a test failed; ``PFACTORY_E2E_FIXTURE_DIR`` is the disk root the
 * CI workflow ``mkdir``s before invoking Playwright.
 */

import { test as base, expect, APIRequestContext } from '@playwright/test';

const API = process.env.PFACTORY_E2E_API ?? 'http://localhost:3102';
const TOKEN = process.env.PFACTORY_E2E_TOKEN ?? '';
const FIXTURE_DIR =
  process.env.PFACTORY_E2E_FIXTURE_DIR ?? '/tmp/pfactory-e2e-fixture';

if (!TOKEN) {
  console.warn('[e2e] PFACTORY_E2E_TOKEN not set; expect 401s from the API');
}

const authHeaders = { Authorization: `Bearer ${TOKEN}` };

export interface ActiveTask {
  specId: string;
  taskId: string; // composite project:spec
  projectId: string;
}

/**
 * Find-or-create the fixture project.  Idempotent — multiple test runs
 * share the same project so the projects.json doesn't grow unbounded.
 */
async function ensureFixtureProject(
  request: APIRequestContext
): Promise<string> {
  // Try the create path.  409 means the path is already registered;
  // in that case we list and find the matching project_id.
  const createRes = await request.post(`${API}/api/projects`, {
    headers: { ...authHeaders, 'Content-Type': 'application/json' },
    data: { path: FIXTURE_DIR, name: 'pfactory-e2e-fixture' },
  });

  if (createRes.status() === 201) {
    const body = await createRes.json();
    return body.id;
  }

  if (createRes.status() === 409) {
    // Already exists — list and find by path.
    const listRes = await request.get(`${API}/api/projects`, {
      headers: authHeaders,
    });
    expect(listRes.status()).toBe(200);
    const projects = (await listRes.json()) as Array<{
      id: string;
      path: string;
    }>;
    const match = projects.find((p) => p.path === FIXTURE_DIR);
    if (!match) {
      throw new Error(
        `[e2e] project at ${FIXTURE_DIR} 409'd on create but isn't in list`
      );
    }
    return match.id;
  }

  throw new Error(
    `[e2e] project create failed: ${createRes.status()} ${await createRes.text()}`
  );
}

export const test = base.extend<{
  activeTask: ActiveTask;
}>({
  activeTask: async ({ request }, use) => {
    const projectId = await ensureFixtureProject(request);

    // Create a unique task — title is unique-per-run so reruns don't
    // hit any stale-state issue in the spec_id generator.
    const stamp = Date.now();
    const taskTitle = `e2e-fixture-${stamp}`;
    const createTaskRes = await request.post(
      `${API}/api/projects/${projectId}/tasks`,
      {
        headers: { ...authHeaders, 'Content-Type': 'application/json' },
        data: {
          title: taskTitle,
          description: `Auto-generated E2E fixture task (run ${stamp})`,
        },
      }
    );
    expect(createTaskRes.status()).toBe(200);
    const task = await createTaskRes.json();
    const specId: string = task.specId ?? task.spec_id ?? task.id;
    const taskId: string = task.id ?? `${projectId}:${specId}`;

    // Start the task.  The web-server has PFACTORY_TEST_AGENT_CMD set
    // (e.g. ``sleep 300``), so this spawns a noop subprocess and the
    // rmux integration hook still creates the live session.
    const recoverRes = await request.post(
      `${API}/api/tasks/${encodeURIComponent(taskId)}/recover`,
      {
        headers: { ...authHeaders, 'Content-Type': 'application/json' },
        data: { targetStatus: 'backlog', autoRestart: true },
      }
    );
    expect(
      [200, 201].includes(recoverRes.status()),
      `recover/autoRestart failed: ${recoverRes.status()} ${await recoverRes.text()}`
    ).toBe(true);

    // Poll /attach until the rmux session is registered.  200 = we won
    // the lock, 409 = something else has it — both prove the session
    // exists.  404 = session not yet created (or wrong spec_id).
    //
    // expect.poll returns a thenable that supports the *value* matchers
    // (toBe, toEqual…) but NOT array matchers like toBeOneOf — that
    // belongs on standalone expect().  We wrap the status check in a
    // boolean callback and assert toBe(true).
    await expect
      .poll(
        async () => {
          const r = await request.post(
            `${API}/api/tasks/${encodeURIComponent(specId)}/agent-console/attach`,
            {
              headers: { ...authHeaders, 'Content-Type': 'application/json' },
              data: { connection_id: 'fixture-probe' },
            }
          );
          return [200, 409].includes(r.status());
        },
        {
          timeout: 60_000,
          message: 'rmux session for spec never appeared in registry',
        }
      )
      .toBe(true);

    // Release the probe so the real test can claim a connection_id.
    await request
      .post(
        `${API}/api/tasks/${encodeURIComponent(specId)}/agent-console/detach`,
        {
          headers: { ...authHeaders, 'Content-Type': 'application/json' },
          data: { connection_id: 'fixture-probe' },
        }
      )
      .catch(() => {});

    await use({ specId, taskId, projectId });

    // ---- Teardown ----
    // Stop the task — best effort, swallow errors.  We don't delete
    // the project: it's reused across runs and that's the point.
    await request
      .post(`${API}/api/tasks/${encodeURIComponent(taskId)}/stop`, {
        headers: { ...authHeaders, 'Content-Type': 'application/json' },
        data: {},
      })
      .catch(() => {});
  },
});

export { expect };

/** Helper: read the AuditLog table via the API.  Used by Scenario 3. */
export async function fetchAuditLog(
  request: APIRequestContext,
  action: string
): Promise<any[]> {
  const r = await request.get(
    `${API}/api/orgs/_/audit-logs?action=${encodeURIComponent(action)}&limit=10`,
    { headers: authHeaders }
  );
  if (r.status() !== 200) return [];
  const body = await r.json();
  return body.data ?? body ?? [];
}
