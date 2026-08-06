/**
 * @vitest-environment jsdom
 */
/**
 * Regression tests for ReadinessPanel — specifically that expanding a check
 * does not crash when `evidence` is an object (the backend always sends a dict,
 * even `{}` for passing checks). Rendering the object directly used to throw
 * "Objects are not valid as a React child" and blanked the page.
 */

import { describe, it, expect, vi } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ReadinessPanel } from './ReadinessPanel';
import type { PlanSession, ReadinessCheck } from '../../shared/types/plan';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, def?: unknown) => (typeof def === 'string' ? def : key),
  }),
}));

vi.mock('../../lib/planning-api', () => ({ waiveChecks: vi.fn() }));

function check(overrides: Partial<ReadinessCheck>): ReadinessCheck {
  return {
    check_id: 'c',
    title: 'A check',
    status: 'pass',
    severity: 'info',
    hard: false,
    waivable: true,
    detail: '',
    remediation: '',
    evidence: {},
    ...overrides,
  };
}

function sessionWith(
  results: ReadinessCheck[],
  readinessOverrides: Record<string, unknown> = {},
): PlanSession {
  return {
    session_id: 's1',
    review: {
      readiness: {
        results,
        waivers: [],
        generated_at: '2026-06-18T00:00:00Z',
        ...readinessOverrides,
      },
    },
  } as unknown as PlanSession;
}

describe('ReadinessPanel evidence rendering', () => {
  it('expands a passing check with empty-object evidence without crashing', () => {
    const s = sessionWith([
      check({ check_id: 'c1', title: 'Passing check', status: 'pass', hard: true, evidence: {} }),
    ]);
    render(<ReadinessPanel session={s} onUpdated={() => {}} />);
    fireEvent.click(screen.getByText('Passing check'));
    // The expand renders (no crash); the row is still present.
    expect(screen.getByText('Passing check')).toBeInTheDocument();
  });

  it('renders object evidence as formatted JSON when present', () => {
    const s = sessionWith([
      check({
        check_id: 'c2',
        title: 'Failing check',
        status: 'fail',
        hard: true,
        detail: 'bad',
        evidence: { uncovered_acs: ['AC#1'] },
      }),
    ]);
    render(<ReadinessPanel session={s} onUpdated={() => {}} />);
    fireEvent.click(screen.getByText('Failing check'));
    const ev = screen.getByTestId('readiness-evidence-c2');
    expect(ev).toHaveTextContent('uncovered_acs');
    expect(ev).toHaveTextContent('AC#1');
  });
});

describe('ReadinessPanel verdict freshness (#450)', () => {
  it('flags a verdict computed by older gate logic as needing re-evaluation', () => {
    const s = sessionWith([check({ check_id: 'c1', status: 'fail', hard: true })], {
      stale: true,
    });
    render(<ReadinessPanel session={s} onUpdated={() => {}} />);
    expect(screen.getByTestId('readiness-stale-badge')).toBeInTheDocument();
  });

  it('does not flag a verdict computed by the current gate logic', () => {
    const s = sessionWith([check({ check_id: 'c1', status: 'fail', hard: true })], {
      stale: false,
    });
    render(<ReadinessPanel session={s} onUpdated={() => {}} />);
    expect(screen.queryByTestId('readiness-stale-badge')).not.toBeInTheDocument();
  });

  it('shows the recompute time, not the original compute time, once recomputed', () => {
    const s = sessionWith([check({ check_id: 'c1' })], {
      recomputed_at: '2026-08-06T09:00:00Z',
    });
    render(<ReadinessPanel session={s} onUpdated={() => {}} />);
    expect(
      screen.getByText(new Date('2026-08-06T09:00:00Z').toLocaleString()),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(new Date('2026-06-18T00:00:00Z').toLocaleString()),
    ).not.toBeInTheDocument();
  });
});
