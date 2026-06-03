/**
 * @vitest-environment jsdom
 *
 * Tests for <PFactoryTaskList> — Task 10 (#11) commit 2.
 *
 * Uses the API client's ``fetchFn`` injection (Task 10 commit 1) to
 * stub the network. No MSW or fetch-mock required.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { PFactoryTaskList, statusColor } from '../PFactoryTaskList';

beforeEach(() => {
  localStorage.setItem('pfactory-token', 'test-token');
});

// ── Fake fetch helper ─────────────────────────────────────────────────

function makeFetch(opts: {
  ok?: boolean;
  status?: number;
  statusText?: string;
  jsonBody?: unknown;
  delay?: number;
}): typeof fetch {
  const {
    ok = true, status = 200, statusText = 'OK', jsonBody, delay = 0,
  } = opts;
  return vi.fn().mockImplementation(async () => {
    if (delay > 0) {
      await new Promise((r) => setTimeout(r, delay));
    }
    return {
      ok,
      status,
      statusText,
      json: async () => jsonBody,
      text: async () => '',
    };
  }) as unknown as typeof fetch;
}

const sampleTask = (overrides: Partial<{
  spec_id: string; project_id: string; status: string | null;
  phase: string | null; updated_at: string; task_id: string;
}> = {}) => ({
  task_id: overrides.task_id ?? overrides.spec_id ?? 'st0',
  project_id: overrides.project_id ?? 'demo',
  spec_id: overrides.spec_id ?? 'st0',
  status: overrides.status ?? 'evaluated',
  phase: overrides.phase ?? 'evaluator_complete',
  updated_at: overrides.updated_at ?? '2026-05-28T10:00:00+00:00',
});

// ── statusColor unit ──────────────────────────────────────────────────

describe('statusColor', () => {
  it.each([
    ['triaged', 'green'],
    ['evaluated', 'green'],
    ['triager_failed', 'red'],
    ['evaluator_failed', 'red'],
    ['planner_failed', 'red'],
    ['stuck', 'red'],
    ['stalled', 'red'],
    ['replan_needed', 'red'],
    ['planning', 'blue'],
    ['generating', 'blue'],
    ['evaluating', 'blue'],
    ['triaging', 'blue'],
    ['triaged_empty', 'yellow'],
    ['evaluated_empty', 'yellow'],
    ['pending', 'gray'],
    ['idle', 'gray'],
  ])('maps %s → %s', (status, expected) => {
    expect(statusColor(status)).toBe(expected);
  });

  it('null status → gray', () => {
    expect(statusColor(null)).toBe('gray');
  });
});

// ── Loading state ────────────────────────────────────────────────────

describe('<PFactoryTaskList> loading state', () => {
  it('shows a loading indicator before the fetch resolves', async () => {
    // Long delay so the loading state is visible
    const fetchFn = makeFetch({ jsonBody: { tasks: [], count: 0 }, delay: 100 });
    render(<PFactoryTaskList fetchFn={fetchFn} />);
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.getByText(/Loading tasks/i)).toBeInTheDocument();
  });
});

// ── Empty state ──────────────────────────────────────────────────────

describe('<PFactoryTaskList> empty state', () => {
  it('renders the empty message when the API returns no tasks', async () => {
    const fetchFn = makeFetch({ jsonBody: { tasks: [], count: 0 } });
    render(<PFactoryTaskList fetchFn={fetchFn} />);

    await waitFor(() =>
      expect(screen.getByText(/No PFactory tasks yet/i)).toBeInTheDocument(),
    );
  });
});

// ── Error state ──────────────────────────────────────────────────────

describe('<PFactoryTaskList> error state', () => {
  it('shows an alert when the API returns a non-2xx', async () => {
    const fetchFn = makeFetch({
      ok: false, status: 500, statusText: 'Internal Server Error',
      jsonBody: { detail: 'database down' },
    });
    render(<PFactoryTaskList fetchFn={fetchFn} />);
    await waitFor(() =>
      expect(screen.getByRole('alert')).toBeInTheDocument(),
    );
    expect(screen.getByText(/database down/)).toBeInTheDocument();
  });
});

// ── Populated list ───────────────────────────────────────────────────

describe('<PFactoryTaskList> populated', () => {
  it('renders a row per task', async () => {
    const fetchFn = makeFetch({
      jsonBody: {
        tasks: [
          sampleTask({ spec_id: 'task-a' }),
          sampleTask({ spec_id: 'task-b' }),
          sampleTask({ spec_id: 'task-c' }),
        ],
        count: 3,
      },
    });
    render(<PFactoryTaskList fetchFn={fetchFn} />);
    await waitFor(() =>
      expect(screen.getByTestId('pfactory-task-list')).toBeInTheDocument(),
    );
    expect(screen.getByTestId('task-row-task-a')).toBeInTheDocument();
    expect(screen.getByTestId('task-row-task-b')).toBeInTheDocument();
    expect(screen.getByTestId('task-row-task-c')).toBeInTheDocument();
  });

  it('displays project, phase, and updated_at columns', async () => {
    const fetchFn = makeFetch({
      jsonBody: {
        tasks: [sampleTask({
          spec_id: 'task-a', project_id: 'projx',
          phase: 'evaluator_complete',
          updated_at: '2026-05-28T10:00:00+00:00',
        })],
        count: 1,
      },
    });
    render(<PFactoryTaskList fetchFn={fetchFn} />);
    await waitFor(() => screen.getByText('task-a'));
    expect(screen.getByText('projx')).toBeInTheDocument();
    expect(screen.getByText('evaluator_complete')).toBeInTheDocument();
    // updated_at is humanised to a short absolute date ("May 28") plus a
    // relative label, rather than the raw ISO string.
    expect(screen.getByText('May 28')).toBeInTheDocument();
  });

  it('colours status badges by bucket', async () => {
    const fetchFn = makeFetch({
      jsonBody: {
        tasks: [
          sampleTask({ spec_id: 'a', status: 'triaged' }),
          sampleTask({ spec_id: 'b', status: 'evaluating' }),
          sampleTask({ spec_id: 'c', status: 'triager_failed' }),
          sampleTask({ spec_id: 'd', status: 'evaluated_empty' }),
        ],
        count: 4,
      },
    });
    render(<PFactoryTaskList fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('task-row-a'));

    const badges = screen.getAllByTestId('status-badge');
    expect(badges).toHaveLength(4);
    expect(badges[0]).toHaveAttribute('data-status-color', 'green');
    expect(badges[1]).toHaveAttribute('data-status-color', 'blue');
    expect(badges[2]).toHaveAttribute('data-status-color', 'red');
    expect(badges[3]).toHaveAttribute('data-status-color', 'yellow');
  });

  it('handles a null status gracefully', async () => {
    // Build the task object directly — sampleTask helper's `??`
    // coalesces null, so we'd lose the null status through it.
    const fetchFn = makeFetch({
      jsonBody: {
        tasks: [{
          task_id: 'a',
          project_id: 'demo',
          spec_id: 'a',
          status: null,
          phase: null,
          updated_at: '2026-05-28T10:00:00+00:00',
        }],
        count: 1,
      },
    });
    render(<PFactoryTaskList fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('task-row-a'));
    const badge = screen.getByTestId('status-badge');
    expect(badge).toHaveAttribute('data-status-color', 'gray');
    expect(badge.textContent).toBe('—');
  });
});

// ── Click → onSelectTask ──────────────────────────────────────────────

describe('<PFactoryTaskList> row click', () => {
  it('fires onSelectTask with the spec_id when a row is clicked', async () => {
    const onSelect = vi.fn();
    const fetchFn = makeFetch({
      jsonBody: {
        tasks: [
          sampleTask({ spec_id: 'task-a' }),
          sampleTask({ spec_id: 'task-b' }),
        ],
        count: 2,
      },
    });
    render(<PFactoryTaskList fetchFn={fetchFn} onSelectTask={onSelect} />);
    await waitFor(() => screen.getByTestId('task-row-task-b'));

    fireEvent.click(screen.getByTestId('task-row-task-b'));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith('task-b');
  });

  it('is safe to click without an onSelectTask handler', async () => {
    const fetchFn = makeFetch({
      jsonBody: { tasks: [sampleTask({ spec_id: 'a' })], count: 1 },
    });
    render(<PFactoryTaskList fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('task-row-a'));
    // Should not throw
    fireEvent.click(screen.getByTestId('task-row-a'));
  });
});

// ── Effect cleanup ────────────────────────────────────────────────────

describe('<PFactoryTaskList> cleanup', () => {
  it('does not crash if unmounted before the fetch resolves', async () => {
    const fetchFn = makeFetch({
      jsonBody: { tasks: [], count: 0 }, delay: 200,
    });
    const { unmount } = render(<PFactoryTaskList fetchFn={fetchFn} />);
    unmount();
    // Wait long enough for the fetch promise to settle. The effect's
    // ``cancelled`` flag guards against setState-after-unmount.
    await new Promise((r) => setTimeout(r, 250));
    // No throw, no warning — the test passes by NOT crashing.
  });
});
