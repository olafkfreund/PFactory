/**
 * @vitest-environment jsdom
 *
 * Tests for <PFactoryPortal> shell — Task 10 (#11) commit 4.
 *
 * Verifies the list ↔ detail toggle + back navigation.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { PFactoryPortal } from '../PFactoryPortal';

beforeEach(() => {
  localStorage.setItem('pfactory-token', 'test-token');
});

// URL-aware fetch mock (same shape as PFactoryTaskDetail tests)
interface UrlResponse {
  ok?: boolean; status?: number; statusText?: string;
  jsonBody?: unknown; textBody?: string;
}
function makeUrlAwareFetch(routes: Record<string, UrlResponse>): typeof fetch {
  return vi.fn().mockImplementation(async (input: RequestInfo) => {
    const url = typeof input === 'string' ? input : input.url;
    let match: UrlResponse | undefined;
    for (const key of Object.keys(routes)) {
      if (url.endsWith(key) || url === key) {
        match = routes[key];
        break;
      }
    }
    const opts: UrlResponse = match ?? { ok: false, status: 404, statusText: 'NF' };
    return {
      ok: opts.ok ?? true,
      status: opts.status ?? 200,
      statusText: opts.statusText ?? 'OK',
      json: async () => opts.jsonBody,
      text: async () => opts.textBody ?? '',
    };
  }) as unknown as typeof fetch;
}

const taskListBody = {
  tasks: [
    {
      task_id: 'spec-a', project_id: 'demo', spec_id: 'spec-a',
      status: 'triaged', phase: 'triager_complete',
      updated_at: '2026-05-28T10:00:00+00:00',
    },
  ],
  count: 1,
};

const detailBody = (specId: string) => ({
  task_id: specId, project_id: 'demo', spec_id: specId,
  status_json: { status: 'triaged' },
  artefacts: {
    test_plan: { path: 'test_plan.json', exists: true },
    verdicts: { path: 'findings/verdicts.json', exists: true },
    triage_report_json: { path: 'findings/triage_report.json', exists: true },
    triage_report_md: { path: 'findings/triage_report.md', exists: true },
    pr_comment_body: { path: 'findings/pr_comment_body.md', exists: false },
  },
});

// ── List view by default ─────────────────────────────────────────────

describe('<PFactoryPortal>', () => {
  it('renders the list view by default', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/api/pfactory/tasks': { jsonBody: taskListBody },
    });
    render(<PFactoryPortal fetchFn={fetchFn} />);
    expect(screen.getByTestId('pfactory-portal')).toHaveAttribute(
      'data-view', 'list',
    );
    expect(screen.getByRole('heading', { name: /PFactory Tasks/i }))
      .toBeInTheDocument();
    await waitFor(() => screen.getByTestId('task-row-spec-a'));
  });

  it('switches to detail view when a task row is clicked', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/api/pfactory/tasks': { jsonBody: taskListBody },
      '/api/pfactory/tasks/spec-a': { jsonBody: detailBody('spec-a') },
    });
    render(<PFactoryPortal fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('task-row-spec-a'));
    fireEvent.click(screen.getByTestId('task-row-spec-a'));

    await waitFor(() =>
      expect(screen.getByTestId('pfactory-portal')).toHaveAttribute(
        'data-view', 'detail',
      ),
    );
    expect(screen.getByTestId('pfactory-task-detail')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'spec-a' })).toBeInTheDocument();
  });

  it('back button returns to list view', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/api/pfactory/tasks': { jsonBody: taskListBody },
      '/api/pfactory/tasks/spec-a': { jsonBody: detailBody('spec-a') },
    });
    render(<PFactoryPortal fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('task-row-spec-a'));
    fireEvent.click(screen.getByTestId('task-row-spec-a'));

    await waitFor(() => screen.getByTestId('portal-back'));
    fireEvent.click(screen.getByTestId('portal-back'));

    await waitFor(() =>
      expect(screen.getByTestId('pfactory-portal')).toHaveAttribute(
        'data-view', 'list',
      ),
    );
    // List view re-renders; row is back
    await waitFor(() => screen.getByTestId('task-row-spec-a'));
  });
});
