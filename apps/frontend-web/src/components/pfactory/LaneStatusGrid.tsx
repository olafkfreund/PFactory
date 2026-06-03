/**
 * Lane status grid — Task 10 (#11) commit 3; reskinned in Task 0 (#16) commit 3;
 * full reskin in Task 15 (#31) commit 1 — all 5 lanes are now independently lit.
 *
 * Presentational grid showing the FIVE v0.2 lanes:
 *
 *   - unit         (Phase 1 — pytest/Jest for the changed feature)
 *   - browser      (Phase 2 — Playwright via the live app; runs against AppRuntime)
 *   - api          (Phase 3 — HTTP/contract checks against the running service)
 *   - integration  (Phase 4 — service-to-service via testcontainers)
 *   - mutation     (Phase 5 — assertion-strength mutation testing)
 *
 * v0.1 used `functional/sast/dast/fuzz/mutation`. The v0.2 spine swap is the
 * modality-based decomposition from `docs/plans/2026-05-28-enterprise-test-frameworks-design.md`
 * (Decision 2). Task 15 fully reskins all 5 cards — each lane card now lights
 * independently via its own status from `laneStatuses`.
 *
 * Props (Task 15 API change):
 *   laneStatuses?: Record<LaneId, string | null>  — per-lane status strings
 *   unitStatus?   — v0.1 compat shim: treated as { unit: unitStatus }
 *
 * Consumer (PFactoryTaskDetail) should derive laneStatuses from
 * `status_json.lane_progress` and fall back to { unit: status } for v0.1.
 */

import {
  CheckCircle2,
  Circle,
  Clock,
  AlertCircle,
  CheckSquare,
  Globe,
  Plug,
  Network,
  Zap,
} from 'lucide-react';

import { statusColor } from './PFactoryTaskList';

// ── Lane definitions ──────────────────────────────────────────────────

export type LaneId = 'unit' | 'browser' | 'api' | 'integration' | 'mutation';

export interface LaneDef {
  id: LaneId;
  label: string;
  phase: number;
  description: string;
  /** Tailwind classes for the lane's accent border/bg when lit. */
  accentBorder: string;
  accentBg: string;
  accentIcon: string;
}

export const LANES: readonly LaneDef[] = [
  {
    id: 'unit', label: 'Unit', phase: 1,
    description: 'pytest / Jest unit tests for the changed feature.',
    accentBorder: 'border-blue-300',
    accentBg: 'bg-info/10',
    accentIcon: 'text-info',
  },
  {
    id: 'browser', label: 'Browser', phase: 2,
    description: 'Playwright UI tests run against the live app via AppRuntime.',
    accentBorder: 'border-purple-300',
    accentBg: 'bg-purple-50',
    accentIcon: 'text-purple-600',
  },
  {
    id: 'api', label: 'API', phase: 3,
    description: 'HTTP / contract checks against the running service surface.',
    accentBorder: 'border-green-300',
    accentBg: 'bg-success/10',
    accentIcon: 'text-success',
  },
  {
    id: 'integration', label: 'Integration', phase: 4,
    description: 'Service-to-service integration via testcontainers.',
    accentBorder: 'border-orange-300',
    accentBg: 'bg-orange-50',
    accentIcon: 'text-orange-600',
  },
  {
    id: 'mutation', label: 'Mutation', phase: 5,
    description: 'Whole-suite mutation testing for assertion strength.',
    accentBorder: 'border-red-300',
    accentBg: 'bg-destructive/10',
    accentIcon: 'text-destructive',
  },
] as const;

// ── Card states ──────────────────────────────────────────────────────

export type LaneCardState =
  | 'idle'         // pending / no work yet
  | 'in_flight'    // running
  | 'success'      // green (evaluated / triaged)
  | 'warning'      // yellow (empty bucket, replan, etc.)
  | 'failure';     // red (any *_failed)

/**
 * Derive the LaneCardState for any lane from its backend status string.
 *
 * Mirrors PFactoryTaskList's statusColor bucket mapping so the grid
 * + the task list stay visually consistent.
 *
 * Renamed from `unitLaneState` in Task 15; the old name is preserved
 * as an alias for backward compat with existing tests.
 */
export function laneCardState(
  _laneId: LaneId,
  status: string | null,
): LaneCardState {
  const color = statusColor(status);
  if (color === 'green') return 'success';
  if (color === 'red') return 'failure';
  if (color === 'yellow') return 'warning';
  if (color === 'blue') return 'in_flight';
  return 'idle';
}

/**
 * v0.1 compat alias — unit-only variant kept for tests that import it
 * by the old name (Task 10 tests).
 */
export function unitLaneState(status: string | null): LaneCardState {
  return laneCardState('unit', status);
}

// ── Card visuals (state-level) ────────────────────────────────────────

const STATE_VISUALS: Record<
  LaneCardState,
  { borderClass: string; bgClass: string; iconColorClass: string; label: string }
> = {
  idle: {
    borderClass: 'border-border', bgClass: 'bg-card',
    iconColorClass: 'text-muted-foreground', label: 'Idle',
  },
  in_flight: {
    borderClass: 'border-blue-300', bgClass: 'bg-info/10',
    iconColorClass: 'text-info', label: 'In flight',
  },
  success: {
    borderClass: 'border-green-300', bgClass: 'bg-success/10',
    iconColorClass: 'text-success', label: 'Complete',
  },
  warning: {
    borderClass: 'border-yellow-300', bgClass: 'bg-warning/10',
    iconColorClass: 'text-warning', label: 'Needs review',
  },
  failure: {
    borderClass: 'border-red-300', bgClass: 'bg-destructive/10',
    iconColorClass: 'text-destructive', label: 'Failed',
  },
};

/**
 * Per-lane icon — each lane has a unique icon so operators can tell them
 * apart at a glance in the grid, even when all are at the same state.
 */
function LaneIcon({ laneId, colorClass }: { laneId: LaneId; colorClass: string }) {
  const cls = `h-5 w-5 ${colorClass}`;
  switch (laneId) {
    case 'unit':
      return <CheckSquare className={cls} aria-hidden />;
    case 'browser':
      return <Globe className={cls} aria-hidden />;
    case 'api':
      return <Plug className={cls} aria-hidden />;
    case 'integration':
      return <Network className={cls} aria-hidden />;
    case 'mutation':
      return <Zap className={cls} aria-hidden />;
  }
}

function StateIcon({ state }: { state: LaneCardState }) {
  const cls = STATE_VISUALS[state].iconColorClass;
  switch (state) {
    case 'success':
      return <CheckCircle2 className={`h-4 w-4 ${cls}`} aria-hidden />;
    case 'failure':
      return <AlertCircle className={`h-4 w-4 ${cls}`} aria-hidden />;
    case 'in_flight':
      return <Clock className={`h-4 w-4 animate-pulse ${cls}`} aria-hidden />;
    case 'warning':
      return <AlertCircle className={`h-4 w-4 ${cls}`} aria-hidden />;
    case 'idle':
    default:
      return <Circle className={`h-4 w-4 ${cls}`} aria-hidden />;
  }
}

interface LaneCardProps {
  lane: LaneDef;
  state: LaneCardState;
  /** Raw status string from the backend — shown as detail text. */
  detail?: string | null;
}

function LaneCard({ lane, state, detail }: LaneCardProps) {
  // When lit (non-idle), use the lane's own accent colours; otherwise use the
  // state-derived colours. This ensures each lane has a recognisable look
  // even when all lanes are at the same state.
  const isIdle = state === 'idle';
  const borderClass = isIdle
    ? STATE_VISUALS[state].borderClass
    : lane.accentBorder;
  const bgClass = isIdle ? STATE_VISUALS[state].bgClass : lane.accentBg;
  const iconColorClass = isIdle
    ? STATE_VISUALS[state].iconColorClass
    : lane.accentIcon;

  return (
    <div
      data-testid={`lane-card-${lane.id}`}
      data-lane-state={state}
      className={`flex flex-col gap-1 rounded-lg border p-3 ${borderClass} ${bgClass}`}
    >
      <div className="flex items-center gap-2">
        <LaneIcon laneId={lane.id} colorClass={iconColorClass} />
        <span className="font-semibold">{lane.label}</span>
        <span className="ml-auto text-xs text-muted-foreground">
          Phase&nbsp;{lane.phase}
        </span>
      </div>
      <p className="text-xs text-muted-foreground">{lane.description}</p>
      <div className="mt-1 flex items-center gap-1">
        <StateIcon state={state} />
        <p
          data-testid={`lane-${lane.id}-detail`}
          className="text-xs font-medium uppercase tracking-wide"
        >
          {detail ?? STATE_VISUALS[state].label}
        </p>
      </div>
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────

interface Props {
  /**
   * Per-lane status strings. Each key is a LaneId; value is the backend
   * status string (e.g. "triaged", "evaluating") or null for idle.
   *
   * Task 15 (full reskin): all 5 lanes use this map.
   * v0.1 compat: if only `unitStatus` is provided (old API), it is
   * promoted to `{ unit: unitStatus }` internally.
   */
  laneStatuses?: Partial<Record<LaneId, string | null>>;
  /**
   * v0.1 compat prop. If provided and `laneStatuses` is absent, this
   * value drives the unit lane. Deprecated — prefer `laneStatuses`.
   */
  unitStatus?: string | null;
}

export function LaneStatusGrid({ laneStatuses, unitStatus = null }: Props) {
  // Resolve the effective per-lane status map.
  const statuses: Partial<Record<LaneId, string | null>> = laneStatuses ?? {
    unit: unitStatus,
  };

  return (
    <div
      data-testid="lane-status-grid"
      className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5"
    >
      {LANES.map((lane) => {
        const status = statuses[lane.id] ?? null;
        const state = laneCardState(lane.id, status);
        return (
          <LaneCard
            key={lane.id}
            lane={lane}
            state={state}
            detail={status}
          />
        );
      })}
    </div>
  );
}
