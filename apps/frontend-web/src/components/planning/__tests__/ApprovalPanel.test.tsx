/**
 * @vitest-environment jsdom
 *
 * Tests for <ApprovalPanel>:
 *  - Approve button disabled when !review.gates_passed
 *  - Approve button enabled and calls approve when gates_passed
 *  - 409 "gates not passed" error is surfaced
 *  - Reject flow works
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import { ApprovalPanel } from '../ApprovalPanel';
import { usePlanStore } from '../../../stores/plan-store';
import type { PlanSession } from '../../../shared/types/plan';

beforeEach(() => {
  localStorage.setItem('pfactory-token', 'test-token');
  usePlanStore.setState({
    sessions: [],
    currentSession: null,
    loading: false,
    sessionLoading: false,
    error: null,
    fetchFn: undefined,
  });
});

// ── Helpers ────────────────────────────────────────────────────────────

function makeSession(gatesPassed: boolean, status: PlanSession['status'] = 'processed'): PlanSession {
  return {
    session_id: 'sess-test',
    status,
    plan: {
      plan_id: 'p1',
      title: 'Test',
      description: '',
      source_format: 'text',
      source_channel: null,
      criteria: [],
      target_kind: 'software',
      plan_type: 'feature',
      enrichment: { infra: [], knowledge: [] },
      content_hash: 'abc',
      ingested_at: '2026-06-01T00:00:00Z',
    },
    epic: null,
    artifacts: [],
    review: {
      plan_id: 'p1',
      threshold: 60,
      aggregate_score: gatesPassed ? 80 : 30,
      gates_passed: gatesPassed,
      code_gates_applied: false,
      lenses: [],
      human_approval: {
        approved: false,
        approved_by: null,
        approved_at: null,
        plan_hash: null,
        valid: false,
        review_count: 0,
        feedback: [],
      },
    },
    emit_result: null,
    created_at: '2026-06-01T00:00:00Z',
  };
}

function makeFetch(jsonBody: unknown, ok = true, status = 200): typeof fetch {
  return vi.fn().mockResolvedValue({
    ok,
    status,
    statusText: ok ? 'OK' : 'Error',
    json: async () => jsonBody,
    text: async () => '',
  }) as unknown as typeof fetch;
}

// ── Approve disabled when gates not passed ──────────────────────────

describe('<ApprovalPanel> — gates not passed', () => {
  it('approve button is disabled when gates_passed is false', () => {
    const session = makeSession(false);
    usePlanStore.setState({ currentSession: session });
    render(<ApprovalPanel session={session} />);

    const approveBtn = screen.getByTestId('approve-btn');
    expect(approveBtn).toBeDisabled();
  });

  it('shows gates warning when gates_passed is false', () => {
    const session = makeSession(false);
    usePlanStore.setState({ currentSession: session });
    render(<ApprovalPanel session={session} />);

    expect(screen.getByTestId('gates-warning')).toBeInTheDocument();
  });
});

// ── Approve enabled when gates passed ────────────────────────────────

describe('<ApprovalPanel> — gates passed', () => {
  it('approve button is enabled when gates_passed is true (after entering approver name)', async () => {
    const session = makeSession(true);
    usePlanStore.setState({ currentSession: session });
    render(<ApprovalPanel session={session} />);

    const approveBtn = screen.getByTestId('approve-btn');
    // Disabled before approver name entered
    expect(approveBtn).toBeDisabled();

    // Enter approver name
    fireEvent.change(screen.getByTestId('approver-input'), {
      target: { value: 'alice' },
    });

    // Should now be enabled
    await waitFor(() => expect(approveBtn).not.toBeDisabled());
  });

  it('calls approveSession when Approve is clicked with gates passed', async () => {
    const approvedSession = makeSession(true, 'approved');
    const fetchFn = makeFetch(approvedSession);
    const session = makeSession(true);
    usePlanStore.setState({ currentSession: session, fetchFn });
    const onUpdated = vi.fn();
    render(<ApprovalPanel session={session} onUpdated={onUpdated} />);

    fireEvent.change(screen.getByTestId('approver-input'), {
      target: { value: 'alice' },
    });
    fireEvent.click(screen.getByTestId('approve-btn'));

    await waitFor(() => {
      expect(fetchFn).toHaveBeenCalled();
    });

    const [url] = (fetchFn as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toContain('/approve');
  });

  it('shows 409 error when approve returns gates not passed', async () => {
    const fetchFn = makeFetch({ detail: 'gates not passed' }, false, 409);
    const session = makeSession(true);
    usePlanStore.setState({ currentSession: session, fetchFn });
    render(<ApprovalPanel session={session} />);

    fireEvent.change(screen.getByTestId('approver-input'), {
      target: { value: 'bob' },
    });
    fireEvent.click(screen.getByTestId('approve-btn'));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/gates not passed/i);
    });
  });
});

// ── Reject flow ───────────────────────────────────────────────────────

describe('<ApprovalPanel> — reject flow', () => {
  it('shows reject form when reject toggle is clicked', async () => {
    const session = makeSession(true);
    usePlanStore.setState({ currentSession: session });
    render(<ApprovalPanel session={session} />);

    fireEvent.click(screen.getByTestId('reject-toggle-btn'));
    await waitFor(() => {
      expect(screen.getByTestId('reject-feedback-input')).toBeInTheDocument();
    });
  });

  it('calls rejectSession when confirm reject is clicked', async () => {
    const rejectedSession = makeSession(false, 'rejected');
    const fetchFn = makeFetch(rejectedSession);
    const session = makeSession(true);
    usePlanStore.setState({ currentSession: session, fetchFn });
    render(<ApprovalPanel session={session} />);

    fireEvent.change(screen.getByTestId('approver-input'), { target: { value: 'carol' } });
    fireEvent.click(screen.getByTestId('reject-toggle-btn'));

    await waitFor(() => screen.getByTestId('reject-feedback-input'));
    fireEvent.change(screen.getByTestId('reject-feedback-input'), {
      target: { value: 'Not ready for production' },
    });

    fireEvent.click(screen.getByTestId('reject-confirm-btn'));

    await waitFor(() => {
      expect(fetchFn).toHaveBeenCalled();
    });

    const [url] = (fetchFn as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toContain('/reject');
  });
});

// ── Already approved/rejected ─────────────────────────────────────────

describe('<ApprovalPanel> — terminal states', () => {
  it('shows "Session approved" status when status is approved', () => {
    const session = makeSession(true, 'approved');
    usePlanStore.setState({ currentSession: session });
    render(<ApprovalPanel session={session} />);
    expect(screen.getByRole('status', { name: /session approved/i })).toBeInTheDocument();
    // No approve/reject buttons
    expect(screen.queryByTestId('approve-btn')).not.toBeInTheDocument();
  });

  it('shows "Session rejected" status when status is rejected', () => {
    const session = makeSession(false, 'rejected');
    usePlanStore.setState({ currentSession: session });
    render(<ApprovalPanel session={session} />);
    expect(screen.getByRole('status', { name: /session rejected/i })).toBeInTheDocument();
  });
});
