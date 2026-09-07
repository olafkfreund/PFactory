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

import { translate } from './i18n-mock';

// Resolves against the real `en` resource, so a key missing from the locale
// files fails here instead of rendering as a raw key in the browser.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: translate }),
}));

import { ApprovalPanel } from '../ApprovalPanel';
import { usePlanStore } from '../../../stores/plan-store';
import type { PlanReview, PlanSession } from '../../../shared/types/plan';

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
      readiness: null,
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

// ── Why the gate failed (PFactory#719) ───────────────────────────────

// A plan can fail its gate two independent ways. Reporting the wrong one sends
// the reader hunting for blocking findings that do not exist, with nothing to
// act on — which is exactly what the panel used to do for every failure.
function withLenses(session: PlanSession, lenses: PlanReview['lenses']): PlanSession {
  return { ...session, review: { ...session.review!, threshold: 0.75, lenses } };
}

describe('<ApprovalPanel> — why the gate failed', () => {
  it('names the lens and its score when a lens is below threshold', () => {
    const session = withLenses(makeSession(false), [
      { lens: 'compliance', score: 0.55, max: 1, findings: [], blocking: false },
      { lens: 'security', score: 1, max: 1, findings: [], blocking: false },
    ]);
    usePlanStore.setState({ currentSession: session });
    render(<ApprovalPanel session={session} />);

    const scores = screen.getByTestId('gates-warning-scores');
    expect(scores).toHaveTextContent('compliance scored 0.55');
    expect(scores).toHaveTextContent('threshold is 0.75');
    // The lens that passed must not be blamed.
    expect(scores).not.toHaveTextContent('security');
    // No blocking finding exists, so the panel must not claim one does.
    expect(screen.queryByTestId('gates-warning-blocking')).not.toBeInTheDocument();
  });

  it('names the blocking findings when a lens carries one', () => {
    const session = withLenses(makeSession(false), [
      {
        lens: 'security',
        score: 1,
        max: 1,
        blocking: true,
        findings: [
          {
            title: 'Secret in plaintext',
            detail: '',
            severity: 'critical',
            source: 'security',
            blocking: true,
          },
        ],
      },
    ]);
    usePlanStore.setState({ currentSession: session });
    render(<ApprovalPanel session={session} />);

    expect(screen.getByTestId('gates-warning-blocking')).toHaveTextContent(
      'Secret in plaintext',
    );
    // Every lens is at/above threshold, so no score line.
    expect(screen.queryByTestId('gates-warning-scores')).not.toBeInTheDocument();
  });
});

  it('explains itself when the gate failed with no lens results at all', () => {
    // The backend records an empty `lenses` when none ran. Reporting only
    // "gates have not passed" here is the same dead end this change removes.
    const session = withLenses(makeSession(false), []);
    usePlanStore.setState({ currentSession: session });
    render(<ApprovalPanel session={session} />);

    expect(screen.getByTestId('gates-warning-nodetail')).toHaveTextContent(
      /no lens results/i,
    );
    expect(screen.queryByTestId('gates-warning-scores')).not.toBeInTheDocument();
    expect(screen.queryByTestId('gates-warning-blocking')).not.toBeInTheDocument();
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
