/**
 * @vitest-environment jsdom
 *
 * Tests for <LaneStatusGrid> — Task 10 (#11) commit 3; reskinned in Task 0
 * (#16) commit 3 for the v0.2 lane spine (unit/browser/api/integration/mutation);
 * full reskin in Task 15 (#31) commit 1 — all 5 lanes can now be independently lit.
 *
 * Presentational component; no fetching. Tests assert the visual
 * state mapping, per-lane independence, and v0.1 compat shim.
 */

import { describe, it, expect } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { render, screen } from '@testing-library/react';

import {
  LANES,
  LaneStatusGrid,
  laneCardState,
  unitLaneState,
  type LaneId,
} from '../LaneStatusGrid';

// ── laneCardState mapping ─────────────────────────────────────────────

describe('laneCardState', () => {
  it.each([
    ['triaged', 'success'],
    ['evaluated', 'success'],
    ['evaluator_failed', 'failure'],
    ['triager_failed', 'failure'],
    ['stuck', 'failure'],
    ['planning', 'in_flight'],
    ['generating', 'in_flight'],
    ['evaluating', 'in_flight'],
    ['triaging', 'in_flight'],
    ['triaged_empty', 'warning'],
    ['evaluated_empty', 'warning'],
    ['pending', 'idle'],
  ])('maps %s → %s', (status, expected) => {
    expect(laneCardState('unit', status)).toBe(expected);
  });

  it('null status → idle', () => {
    expect(laneCardState('unit', null)).toBe('idle');
  });

  it('same mapping applies for browser lane', () => {
    expect(laneCardState('browser', 'triaged')).toBe('success');
    expect(laneCardState('browser', 'generating')).toBe('in_flight');
    expect(laneCardState('browser', null)).toBe('idle');
  });

  it('same mapping applies for api lane', () => {
    expect(laneCardState('api', 'evaluated')).toBe('success');
    expect(laneCardState('api', 'evaluator_failed')).toBe('failure');
  });

  it('same mapping applies for integration lane', () => {
    expect(laneCardState('integration', 'triaging')).toBe('in_flight');
    expect(laneCardState('integration', 'triaged_empty')).toBe('warning');
  });

  it('same mapping applies for mutation lane', () => {
    expect(laneCardState('mutation', 'stuck')).toBe('failure');
    expect(laneCardState('mutation', 'triaged')).toBe('success');
  });
});

// ── unitLaneState — v0.1 compat alias ────────────────────────────────

describe('unitLaneState (v0.1 compat alias)', () => {
  it.each([
    ['triaged', 'success'],
    ['evaluator_failed', 'failure'],
    ['planning', 'in_flight'],
    ['triaged_empty', 'warning'],
    ['pending', 'idle'],
  ])('maps %s → %s', (status, expected) => {
    expect(unitLaneState(status)).toBe(expected);
  });

  it('null status → idle', () => {
    expect(unitLaneState(null)).toBe('idle');
  });
});

// ── LANES table ──────────────────────────────────────────────────────

describe('LANES definition', () => {
  it('declares 5 v0.2 lanes in roadmap order', () => {
    expect(LANES.map((l) => l.id)).toEqual([
      'unit', 'browser', 'api', 'integration', 'mutation',
    ]);
  });

  it('assigns Phase 1-5 in order', () => {
    expect(LANES.map((l) => l.phase)).toEqual([1, 2, 3, 4, 5]);
  });

  it('uses the v0.2 modality labels', () => {
    expect(LANES.map((l) => l.label)).toEqual([
      'Unit', 'Browser', 'API', 'Integration', 'Mutation',
    ]);
  });

  it('each lane has accentBorder / accentBg / accentIcon classes', () => {
    for (const lane of LANES) {
      expect(lane.accentBorder).toBeTruthy();
      expect(lane.accentBg).toBeTruthy();
      expect(lane.accentIcon).toBeTruthy();
    }
  });
});

// ── Grid rendering — v0.1 compat (unitStatus prop) ────────────────────

describe('<LaneStatusGrid> — v0.1 compat (unitStatus prop)', () => {
  it('renders all five lane cards', () => {
    render(<LaneStatusGrid unitStatus="triaged" />);
    for (const lane of LANES) {
      expect(screen.getByTestId(`lane-card-${lane.id}`)).toBeInTheDocument();
    }
  });

  it('lights only unit lane; others are idle when only unitStatus given', () => {
    render(<LaneStatusGrid unitStatus="triaged" />);
    expect(screen.getByTestId('lane-card-unit')).toHaveAttribute('data-lane-state', 'success');
    for (const id of ['browser', 'api', 'integration', 'mutation'] as LaneId[]) {
      expect(screen.getByTestId(`lane-card-${id}`)).toHaveAttribute('data-lane-state', 'idle');
    }
  });

  it('lights the unit lane with the given status', () => {
    render(<LaneStatusGrid unitStatus="triaged" />);
    const card = screen.getByTestId('lane-card-unit');
    expect(card).toHaveAttribute('data-lane-state', 'success');
    const detail = screen.getByTestId('lane-unit-detail');
    expect(detail.textContent).toBe('triaged');
  });

  it.each([
    ['evaluating', 'in_flight'],
    ['triager_failed', 'failure'],
    ['evaluated_empty', 'warning'],
    ['pending', 'idle'],
  ])('unit state for %s → %s', (status, expectedState) => {
    render(<LaneStatusGrid unitStatus={status} />);
    expect(screen.getByTestId('lane-card-unit')).toHaveAttribute(
      'data-lane-state', expectedState,
    );
  });

  it('null unitStatus → idle state', () => {
    render(<LaneStatusGrid unitStatus={null} />);
    expect(screen.getByTestId('lane-card-unit')).toHaveAttribute(
      'data-lane-state', 'idle',
    );
  });

  it('default prop (no unitStatus) → idle state', () => {
    render(<LaneStatusGrid />);
    expect(screen.getByTestId('lane-card-unit')).toHaveAttribute(
      'data-lane-state', 'idle',
    );
  });
});

// ── Grid rendering — v0.2 (laneStatuses prop) ─────────────────────────

describe('<LaneStatusGrid> — v0.2 (laneStatuses prop)', () => {
  it('lights each lane independently from laneStatuses map', () => {
    render(
      <LaneStatusGrid
        laneStatuses={{
          unit: 'triaged',
          browser: 'evaluating',
          api: 'triaged_empty',
          integration: 'evaluator_failed',
          mutation: null,
        }}
      />,
    );
    expect(screen.getByTestId('lane-card-unit')).toHaveAttribute('data-lane-state', 'success');
    expect(screen.getByTestId('lane-card-browser')).toHaveAttribute('data-lane-state', 'in_flight');
    expect(screen.getByTestId('lane-card-api')).toHaveAttribute('data-lane-state', 'warning');
    expect(screen.getByTestId('lane-card-integration')).toHaveAttribute('data-lane-state', 'failure');
    expect(screen.getByTestId('lane-card-mutation')).toHaveAttribute('data-lane-state', 'idle');
  });

  it('all-5-lit: all success when all lanes are triaged', () => {
    const allTriaged: Record<LaneId, string> = {
      unit: 'triaged', browser: 'triaged', api: 'triaged',
      integration: 'triaged', mutation: 'triaged',
    };
    render(<LaneStatusGrid laneStatuses={allTriaged} />);
    for (const lane of LANES) {
      expect(screen.getByTestId(`lane-card-${lane.id}`)).toHaveAttribute(
        'data-lane-state', 'success',
      );
    }
  });

  it('all-5-in-flight when all lanes are generating', () => {
    const allFlight: Record<LaneId, string> = {
      unit: 'generating', browser: 'generating', api: 'planning',
      integration: 'triaging', mutation: 'evaluating',
    };
    render(<LaneStatusGrid laneStatuses={allFlight} />);
    for (const lane of LANES) {
      expect(screen.getByTestId(`lane-card-${lane.id}`)).toHaveAttribute(
        'data-lane-state', 'in_flight',
      );
    }
  });

  it('partial statuses: missing lanes default to idle', () => {
    render(<LaneStatusGrid laneStatuses={{ unit: 'triaged' }} />);
    expect(screen.getByTestId('lane-card-unit')).toHaveAttribute('data-lane-state', 'success');
    for (const id of ['browser', 'api', 'integration', 'mutation'] as LaneId[]) {
      expect(screen.getByTestId(`lane-card-${id}`)).toHaveAttribute('data-lane-state', 'idle');
    }
  });

  it('detail text shows the raw status string for each lane', () => {
    render(
      <LaneStatusGrid
        laneStatuses={{
          unit: 'triaged',
          browser: 'evaluating',
          api: null,
          integration: 'generating',
          mutation: 'stuck',
        }}
      />,
    );
    expect(screen.getByTestId('lane-unit-detail').textContent).toBe('triaged');
    expect(screen.getByTestId('lane-browser-detail').textContent).toBe('evaluating');
    expect(screen.getByTestId('lane-integration-detail').textContent).toBe('generating');
    expect(screen.getByTestId('lane-mutation-detail').textContent).toBe('stuck');
  });

  it('browser lane can be lit independently (all others idle)', () => {
    render(<LaneStatusGrid laneStatuses={{ browser: 'triaged' }} />);
    expect(screen.getByTestId('lane-card-browser')).toHaveAttribute('data-lane-state', 'success');
    for (const id of ['unit', 'api', 'integration', 'mutation'] as LaneId[]) {
      expect(screen.getByTestId(`lane-card-${id}`)).toHaveAttribute('data-lane-state', 'idle');
    }
  });

  it('api lane can be lit independently', () => {
    render(<LaneStatusGrid laneStatuses={{ api: 'evaluating' }} />);
    expect(screen.getByTestId('lane-card-api')).toHaveAttribute('data-lane-state', 'in_flight');
  });

  it('integration lane can be lit independently', () => {
    render(<LaneStatusGrid laneStatuses={{ integration: 'triager_failed' }} />);
    expect(screen.getByTestId('lane-card-integration')).toHaveAttribute('data-lane-state', 'failure');
  });

  it('mutation lane can be lit independently', () => {
    render(<LaneStatusGrid laneStatuses={{ mutation: 'triaged_empty' }} />);
    expect(screen.getByTestId('lane-card-mutation')).toHaveAttribute('data-lane-state', 'warning');
  });

  it('laneStatuses overrides unitStatus when both provided', () => {
    render(
      <LaneStatusGrid
        laneStatuses={{ unit: 'triaged', browser: 'evaluating' }}
        unitStatus="evaluator_failed"
      />,
    );
    // laneStatuses wins — unit should be success, not failure
    expect(screen.getByTestId('lane-card-unit')).toHaveAttribute('data-lane-state', 'success');
    expect(screen.getByTestId('lane-card-browser')).toHaveAttribute('data-lane-state', 'in_flight');
  });

  it('empty laneStatuses map renders all 5 cards as idle', () => {
    render(<LaneStatusGrid laneStatuses={{}} />);
    for (const lane of LANES) {
      expect(screen.getByTestId(`lane-card-${lane.id}`)).toHaveAttribute('data-lane-state', 'idle');
    }
  });

  it('all-5-failed scenario is correctly represented', () => {
    const allFailed: Record<LaneId, string> = {
      unit: 'evaluator_failed', browser: 'triager_failed',
      api: 'stuck', integration: 'evaluator_failed', mutation: 'triager_failed',
    };
    render(<LaneStatusGrid laneStatuses={allFailed} />);
    for (const lane of LANES) {
      expect(screen.getByTestId(`lane-card-${lane.id}`)).toHaveAttribute(
        'data-lane-state', 'failure',
      );
    }
  });

  it('mixed success / failure / in-flight / warning across all 5 lanes', () => {
    render(
      <LaneStatusGrid
        laneStatuses={{
          unit: 'triaged',
          browser: 'generating',
          api: 'evaluated_empty',
          integration: 'triager_failed',
          mutation: 'pending',
        }}
      />,
    );
    expect(screen.getByTestId('lane-card-unit')).toHaveAttribute('data-lane-state', 'success');
    expect(screen.getByTestId('lane-card-browser')).toHaveAttribute('data-lane-state', 'in_flight');
    expect(screen.getByTestId('lane-card-api')).toHaveAttribute('data-lane-state', 'warning');
    expect(screen.getByTestId('lane-card-integration')).toHaveAttribute('data-lane-state', 'failure');
    expect(screen.getByTestId('lane-card-mutation')).toHaveAttribute('data-lane-state', 'idle');
  });
});
