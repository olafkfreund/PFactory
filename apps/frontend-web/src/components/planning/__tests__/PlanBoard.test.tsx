/**
 * @vitest-environment jsdom
 *
 * Tests for <PlanBoard> with shared PipelineStrip, WorkColumn and WorkCard (#3876).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '../../../shared/i18n';
import { PlanBoard } from '../PlanBoard';
import { PlanningView } from '../PlanningView';
import { usePlanStore } from '../../../stores/plan-store';
import type { SessionSummary } from '../../../shared/types/plan';

const makeSession = (overrides: Partial<SessionSummary> = {}): SessionSummary => ({
  session_id: 's-1',
  title: 'Checkout Redesign',
  status: 'ingested',
  target_kind: 'software',
  plan_type: 'feature',
  children: 4,
  gates_passed: null,
  created_at: '2026-06-01T10:00:00Z',
  ...overrides,
});

beforeEach(() => {
  localStorage.clear();
  usePlanStore.setState({ actionBusy: null, actionError: null, fetchFn: undefined, sessions: [], sessionsLoaded: false, loading: false, error: null, currentSession: null });
});

describe('PlanBoard shared WorkColumn, WorkCard and PipelineStrip', () => {
  it('renders Backlog (including processing), Review and Approved stages, collapses Approved/discarded cards by default, and keeps selection, plan type, issue count and gates-failed cue', () => {
    const onSelect = vi.fn();
    const sessions: SessionSummary[] = [
      makeSession({
        session_id: 's-proc',
        title: 'Processing Plan',
        status: 'processing',
        board_state: 'in_progress',
        plan_type: 'migration',
        children: 3,
      }),
      makeSession({
        session_id: 's-rev',
        title: 'Blocked Review Plan',
        status: 'processed',
        board_state: 'human_review',
        plan_type: 'feature',
        children: 5,
        gates_passed: false,
      }),
      makeSession({
        session_id: 's-done',
        title: 'Approved Plan',
        status: 'approved',
        board_state: 'done',
        plan_type: 'bugfix',
        children: 2,
        gates_passed: true,
      }),
    ];

    const { container } = render(
      <PlanBoard sessions={sessions} selectedId="s-rev" onSelect={onSelect} projectId="proj-1" />
    );

    const toolbar = screen.getByRole('toolbar', { name: /Pipeline stages/i });
    expect(within(toolbar).getByText('Backlog')).toBeInTheDocument();
    expect(within(toolbar).getByText('Review')).toBeInTheDocument();
    expect(within(toolbar).getByText('Approved')).toBeInTheDocument();

    // Processing plan is folded into Backlog with plan_type and issue count
    expect(screen.getByText('Unassigned project · migration · 3 issues')).toBeInTheDocument();

    // Review plan shows plan_type, issue count, gates-failed "needs attention" cue, and selection ring
    const revWrapper = container.querySelector('[data-session-id="s-rev"]')!;
    expect(revWrapper).toHaveAttribute('data-selected', 'true');
    expect(within(revWrapper as HTMLElement).getByText('Unassigned project · feature · 5 issues')).toBeInTheDocument();
    expect(within(revWrapper as HTMLElement).getByText('needs attention')).toBeInTheDocument();

    // Clicking title opens the session in the list/detail pane
    fireEvent.click(screen.getByRole('button', { name: 'Unassigned · Blocked Review Plan' }));
    expect(onSelect).toHaveBeenCalledWith('s-rev');

    // Approved card is collapsed by default and expands on chevron click
    const doneCard = container.querySelector('[data-session-id="s-done"] .f-card')!;
    expect(doneCard).toHaveClass('f-card--collapsed');
    fireEvent.click(screen.getByRole('button', { name: 'Expand Unassigned · Approved Plan' }));
    expect(container.querySelector('[data-session-id="s-done"] .f-card')).toHaveClass('f-card--expanded');
  });

  it('flares on the first failure after a clean poll and filters failure-only items', async () => {
    const clean = makeSession({
      session_id: 's-clean',
      title: 'Clean Review',
      status: 'processed',
      board_state: 'human_review',
      gates_passed: true,
    });
    const failed = makeSession({
      session_id: 's-fail',
      title: 'Failed Review',
      status: 'rejected',
      board_state: 'human_review',
      gates_passed: false,
    });

    const view = render(<PlanBoard sessions={[clean]} selectedId={null} onSelect={() => {}} />);
    expect(view.container.querySelector('.f-strip__flare')).toBeNull();

    view.rerender(<PlanBoard sessions={[clean, failed]} selectedId={null} onSelect={() => {}} />);
    await waitFor(() => expect(view.container.querySelector('.f-strip__flare')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Filter Review failures/i }));
    expect(screen.queryByText('Unassigned · Clean Review')).toBeNull();
    expect(screen.getByText('Unassigned · Failed Review')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Clear filter/i }));
    expect(screen.getByText('Unassigned · Clean Review')).toBeInTheDocument();
    expect(screen.getByText('Unassigned · Failed Review')).toBeInTheDocument();
  });

  it('handles item actions while pending and after rejection', async () => {
    let rejectCall: (err: Error) => void = () => undefined;
    let callCount = 0;
    const fetchFn = vi.fn().mockImplementation(() =>
      ++callCount === 1
        ? new Promise((_resolve, reject) => {
            rejectCall = reject;
          })
        : Promise.resolve({
            ok: true,
            status: 200,
            statusText: 'OK',
            json: async () => ({
              session_id: 's-pending',
              status: 'processed',
              board_state: 'human_review',
              review: { gates_passed: true },
              epic: { children: [{ key: '1' }, { key: '2' }] },
              sessions: [],
            }),
          })
    ) as unknown as typeof fetch;

    const pending = makeSession({
      session_id: 's-pending',
      title: 'Pending Plan',
      status: 'ingested',
      board_state: 'backlog',
    });
    const rejected = makeSession({
      session_id: 's-rejected',
      title: 'Rejected Plan',
      status: 'rejected',
      board_state: 'human_review',
      gates_passed: false,
    });

    render(<PlanBoard sessions={[pending, rejected]} selectedId={null} onSelect={() => {}} fetchFn={fetchFn} />);

    const processBtn = screen.getByRole('button', { name: 'Process' });
    const rerunBtn = screen.getByRole('button', { name: 'Re-run' });
    fireEvent.click(processBtn);

    expect(fetchFn).toHaveBeenCalledWith(
      '/api/plan/sessions/s-pending/process',
      expect.objectContaining({ method: 'POST' })
    );
    expect(processBtn).toBeDisabled();
    expect(rerunBtn).toBeDisabled();

    await act(async () => {
      rejectCall(new Error('Pipeline unavailable'));
    });

    expect(screen.getByRole('alert')).toHaveTextContent('Pipeline unavailable');
    expect(processBtn).toBeEnabled();
    expect(rerunBtn).toBeEnabled();

    fireEvent.click(processBtn);
    await waitFor(() => expect(vi.mocked(fetchFn).mock.calls.filter(([url]) => String(url).endsWith('/process'))).toHaveLength(2));
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  });

  it('refuses dismissal when the server has approved a stale failed card', async () => {
    const failed = makeSession({ status: 'failed', gates_passed: false });
    const fetchFn = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ session_id: 's-1', status: 'approved', review: { gates_passed: true }, sessions: [{ ...failed, status: 'approved', gates_passed: true }] }),
    }) as unknown as typeof fetch;
    render(<PlanBoard sessions={[failed]} selectedId={null} onSelect={() => {}} fetchFn={fetchFn} />);
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    await screen.findByRole('alert');
    expect(screen.getByRole('alert')).toHaveTextContent('no longer eligible');
    expect(fetchFn).not.toHaveBeenCalledWith(expect.stringContaining('/discard'), expect.anything());
  });

  it('labels cards with their owning project and restores expansion among nine collapsed cards', () => {
    const sessions = Array.from({ length: 9 }, (_, index) => makeSession({
      session_id: `done-${index}`,
      title: `Done ${index}`,
      status: 'approved',
      board_state: 'done',
      project_id: index === 0 ? 'alpha' : 'beta',
    }));
    const view = render(<PlanBoard sessions={sessions} selectedId={null} onSelect={() => {}} projectId="alpha" />);
    expect(screen.getByText('Show 1 more')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'alpha · Done 0' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Expand alpha · Done 0' }));
    expect(view.container.querySelector('[data-session-id="done-0"] .f-card')).toHaveClass('f-card--expanded');
    view.unmount();
    const restored = render(<PlanBoard sessions={sessions} selectedId={null} onSelect={() => {}} projectId="alpha" />);
    expect(restored.container.querySelector('[data-session-id="done-0"] .f-card')).toHaveClass('f-card--expanded');
  });

  it('renders both Board and List views over one data set with shared strip filters, in-place detail, and one-line empty state', async () => {
    const sessions: SessionSummary[] = [
      makeSession({
        session_id: 's-backlog',
        title: 'Backlog Plan',
        status: 'ingested',
        board_state: 'backlog',
        project_id: 'alpha',
      }),
      makeSession({
        session_id: 's-review',
        title: 'Review Plan',
        status: 'processed',
        board_state: 'human_review',
        gates_passed: false,
        project_id: 'alpha',
      }),
    ];

    let currentList: SessionSummary[] = [];
    const fetchFn = vi.fn().mockImplementation((url: string) => {
      if (String(url).endsWith('/sessions')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ sessions: currentList }),
        });
      }
      const id = String(url).split('/').pop()!;
      const found = sessions.find((s) => s.session_id === id) ?? sessions[0];
      return Promise.resolve({
        ok: true,
        json: async () => ({
          session_id: found.session_id,
          status: found.status,
          created_at: found.created_at,
          updated_at: found.created_at,
          plan: {
            plan_id: found.session_id,
            title: found.title,
            description: 'Plan description',
            source_format: 'markdown',
            source_channel: null,
            criteria: [],
            target_kind: 'software',
            plan_type: 'feature',
            enrichment: { infra: [], knowledge: [] },
            content_hash: 'abc',
            ingested_at: found.created_at,
          },
          epic: null,
          review: null,
          approval: null,
          emission: null,
          readiness: null,
          annotation: null,
        }),
      });
    }) as unknown as typeof fetch;

    // 1. Empty state: one line plus one action
    const view = render(<PlanningView fetchFn={fetchFn} />);
    const emptyState = await screen.findByTestId('plan-empty-state');
    expect(within(emptyState).getByText('No plans yet')).toBeInTheDocument();
    expect(within(emptyState).getByRole('button', { name: 'New plan' })).toBeInTheDocument();
    // Configuration panel is no longer on the planning page
    expect(screen.queryByText('Configuration')).toBeNull();

    // 2. Load data set and verify Board view (three lanes)
    currentList = sessions;
    await act(async () => {
      await usePlanStore.getState().loadSessions();
    });
    expect(view.container.querySelectorAll('.f-board .f-column')).toHaveLength(3);
    expect(screen.getByRole('button', { name: 'alpha · Backlog Plan' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'alpha · Review Plan' })).toBeInTheDocument();

    // Selecting a plan in Board view opens its detail in place inside the expanded card
    fireEvent.click(screen.getByRole('button', { name: 'alpha · Backlog Plan' }));
    const boardCard = view.container.querySelector('[data-session-id="s-backlog"] .f-card')!;
    expect(boardCard).toHaveClass('f-card--expanded');
    expect(await within(boardCard as HTMLElement).findByTestId('session-detail')).toBeInTheDocument();

    // 3. Switch to List view: single WorkColumn over the same plan collection + split detail pane
    fireEvent.click(screen.getByRole('button', { name: 'List' }));
    const listView = screen.getByTestId('plan-list-view');
    expect(listView.querySelectorAll('.f-column')).toHaveLength(1);
    expect(within(listView).getByRole('button', { name: 'alpha · Backlog Plan' })).toBeInTheDocument();
    expect(within(listView).getByRole('button', { name: 'alpha · Review Plan' })).toBeInTheDocument();
    expect(screen.getByTestId('plan-list-detail-pane')).toBeInTheDocument();

    // Strip filters apply to List view as well
    fireEvent.click(screen.getByRole('button', { name: /Filter Review failures/i }));
    expect(within(listView).queryByRole('button', { name: 'alpha · Backlog Plan' })).toBeNull();
    expect(within(listView).getByRole('button', { name: 'alpha · Review Plan' })).toBeInTheDocument();
    expect(screen.queryByTestId('plan-list-detail-pane')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Board' }));
    expect(screen.queryByRole('button', { name: 'alpha · Backlog Plan' })).toBeNull();
    view.rerender(<PlanningView fetchFn={fetchFn} focusSessionId="s-backlog" />);
    expect(await screen.findByRole('button', { name: 'alpha · Backlog Plan' })).toBeInTheDocument();
    expect(await within(view.container.querySelector('[data-session-id="s-backlog"]') as HTMLElement).findByTestId('session-detail')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Filter Review failures/i }));
    expect(screen.queryByTestId('session-detail')).toBeNull();
    expect(screen.queryByRole('button', { name: 'alpha · Backlog Plan' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Clear filter/i }));
    fireEvent.click(screen.getByRole('button', { name: 'alpha · Backlog Plan' }));

    fireEvent.click(screen.getByRole('button', { name: 'List' }));
    expect(screen.getByTestId('plan-list-detail-pane')).toBeInTheDocument();
    expect(within(screen.getByTestId('plan-list-view')).getByRole('button', { name: 'alpha · Backlog Plan' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Filter Review failures/i }));
    expect(screen.queryByTestId('plan-list-detail-pane')).toBeNull();
  });

  it('shows loading and request errors with retry, then an empty state only after success', async () => {
    let rejectFirst: (error: Error) => void = () => undefined;
    const fetchFn = vi.fn()
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectFirst = reject; }))
      .mockResolvedValue({ ok: true, json: async () => ({ sessions: [] }) }) as unknown as typeof fetch;
    render(<PlanningView fetchFn={fetchFn} />);
    expect(screen.getByRole('status')).toHaveTextContent('Loading plans');
    expect(screen.queryByTestId('plan-empty-state')).toBeNull();
    await act(async () => rejectFirst(new Error('Sessions unavailable')));
    expect(screen.getByRole('alert')).toHaveTextContent('Sessions unavailable');
    expect(screen.queryByTestId('plan-empty-state')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByTestId('plan-empty-state')).toHaveTextContent('No plans yet');
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
