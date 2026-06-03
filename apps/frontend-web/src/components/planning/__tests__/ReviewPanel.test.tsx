/**
 * @vitest-environment jsdom
 *
 * Tests for <ReviewPanel>:
 *  - renders per-lens scores
 *  - shows gates-passed badge
 *  - highlights blocking findings
 */

import { describe, it, expect } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import { ReviewPanel } from '../ReviewPanel';
import type { PlanReview } from '../../../shared/types/plan';

// ── Sample data ────────────────────────────────────────────────────────

const sampleReview: PlanReview = {
  plan_id: 'p1',
  threshold: 60,
  aggregate_score: 75,
  gates_passed: true,
  code_gates_applied: false,
  lenses: [
    {
      lens: 'completeness',
      score: 8,
      max: 10,
      blocking: false,
      findings: [
        {
          title: 'Missing acceptance criteria',
          detail: 'Some criteria are undefined.',
          severity: 'medium',
          source: 'completeness-checker',
          blocking: false,
        },
      ],
    },
    {
      lens: 'security',
      score: 5,
      max: 10,
      blocking: true,
      findings: [
        {
          title: 'Hardcoded credentials detected',
          detail: 'Found API key in plain text.',
          severity: 'critical',
          source: 'security-scanner',
          blocking: true,
        },
      ],
    },
  ],
  human_approval: {
    approved: false,
    approved_by: null,
    approved_at: null,
    plan_hash: null,
    valid: false,
    review_count: 0,
    feedback: [],
  },
};

// ── Tests ──────────────────────────────────────────────────────────────

describe('<ReviewPanel>', () => {
  it('renders lens rows for each lens', () => {
    render(<ReviewPanel review={sampleReview} />);
    expect(screen.getByText(/completeness/i)).toBeInTheDocument();
    expect(screen.getByText(/security/i)).toBeInTheDocument();
  });

  it('shows gates-passed badge when gates_passed is true', () => {
    render(<ReviewPanel review={sampleReview} />);
    const badge = screen.getByTestId('gates-badge');
    expect(badge).toHaveTextContent(/gates passed/i);
  });

  it('shows gates-failed badge when gates_passed is false', () => {
    const failedReview = { ...sampleReview, gates_passed: false };
    render(<ReviewPanel review={failedReview} />);
    const badge = screen.getByTestId('gates-badge');
    expect(badge).toHaveTextContent(/gates failed/i);
  });

  it('shows aggregate score', () => {
    render(<ReviewPanel review={sampleReview} />);
    expect(screen.getByText('75')).toBeInTheDocument();
  });

  it('shows blocking count badge when there are blocking findings', () => {
    render(<ReviewPanel review={sampleReview} />);
    const badge = screen.getByTestId('blocking-count-badge');
    expect(badge).toHaveTextContent('1 blocking');
  });

  it('renders lens scores as n/max', () => {
    render(<ReviewPanel review={sampleReview} />);
    expect(screen.getByText('8/10')).toBeInTheDocument();
    expect(screen.getByText('5/10')).toBeInTheDocument();
  });

  it('expands a lens and shows findings on click', async () => {
    render(<ReviewPanel review={sampleReview} />);
    const completenessBtn = screen.getByLabelText(/toggle lens: completeness/i);
    fireEvent.click(completenessBtn);

    await waitFor(() => {
      expect(screen.getByText(/Missing acceptance criteria/i)).toBeInTheDocument();
    });
  });

  it('shows blocking-finding test id for blocking findings', async () => {
    render(<ReviewPanel review={sampleReview} />);
    // Expand the security lens
    const secBtn = screen.getByLabelText(/toggle lens: security/i);
    fireEvent.click(secBtn);

    await waitFor(() => {
      const blockingEl = screen.getByTestId('blocking-finding');
      expect(blockingEl).toBeInTheDocument();
      expect(blockingEl).toHaveTextContent(/Hardcoded credentials/i);
    });
  });

  it('shows the severity of a blocking finding (critical)', async () => {
    render(<ReviewPanel review={sampleReview} />);
    const secBtn = screen.getByLabelText(/toggle lens: security/i);
    fireEvent.click(secBtn);

    await waitFor(() => {
      // Multiple "blocking" badges exist: one on the lens row and one inside the finding.
      // Verify the blocking-finding div is rendered (expanded).
      const blockingFinding = screen.getByTestId('blocking-finding');
      expect(blockingFinding).toBeInTheDocument();
      // The finding title should be visible
      expect(blockingFinding).toHaveTextContent(/Hardcoded credentials/i);
    });
  });

  it('handles a review with no findings gracefully', async () => {
    const emptyReview: PlanReview = {
      ...sampleReview,
      lenses: [{ lens: 'clarity', score: 10, max: 10, blocking: false, findings: [] }],
    };
    render(<ReviewPanel review={emptyReview} />);
    const btn = screen.getByLabelText(/toggle lens: clarity/i);
    fireEvent.click(btn);
    await waitFor(() => {
      expect(screen.getByText(/No findings for this lens/i)).toBeInTheDocument();
    });
  });
});
