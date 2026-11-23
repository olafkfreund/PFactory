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
    expect(screen.getByText('migration · 3 issues')).toBeInTheDocument();

    // Review plan shows plan_type, issue count, gates-failed "needs attention" cue, and selection ring
    const revWrapper = container.querySelector('[data-session-id="s-rev"]')!;
    expect(revWrapper).toHaveAttribute('data-selected', 'true');
    expect(within(revWrapper as HTMLElement).getByText('feature · 5 issues')).toBeInTheDocument();
    expect(within(revWrapper as HTMLElement).getByText('needs attention')).toBeInTheDocument();

    // Clicking title opens the session in the list/detail pane
    fireEvent.click(screen.getByRole('button', { name: 'Blocked Review Plan' }));
    expect(onSelect).toHaveBeenCalledWith('s-rev');

    // Approved card is collapsed by default and expands on chevron click
    const doneCard = container.querySelector('[data-session-id="s-done"] .f-card')!;
    expect(doneCard).toHaveClass('f-card--collapsed');
    fireEvent.click(screen.getByRole('button', { name: 'Expand Approved Plan' }));
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
    expect(screen.queryByText('Clean Review')).toBeNull();
    expect(screen.getByText('Failed Review')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Clear filter/i }));
    expect(screen.getByText('Clean Review')).toBeInTheDocument();
    expect(screen.getByText('Failed Review')).toBeInTheDocument();
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
    await waitFor(() => expect(fetchFn).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  });
});
