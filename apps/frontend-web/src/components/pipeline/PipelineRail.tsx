/**
 * PipelineRail — animated cockpit-style stage overview rendered above the
 * Kanban board.
 *
 * One large circular ring per board column (the 5 task statuses):
 *   • stage icon inside the ring (reuses the same Lucide icons as the columns)
 *   • count badge top-right (JetBrains Mono)
 *   • bold label + monospace sub-label beneath
 *   • active glow + rotating dashed halo when the stage has tasks
 *   • dashed marching-ant connectors between consecutive rings
 *   • a package-box icon that flies along each connector whenever the
 *     adjacent source ring is active (or on a periodic demo tick when idle)
 *
 * CSS animations are defined in index.css (rail-* keyframes).
 * All animations respect prefers-reduced-motion.
 *
 * Usage:
 *   <PipelineRail tasksByStatus={tasksByStatus} />
 */

import { useMemo } from 'react';
import { Package } from 'lucide-react';
import type { TaskStatus } from '../../shared/types';
import {
  PIPELINE_STAGES,
  STAGE_ACCENT,
  STAGE_ICON,
  STAGE_LABEL,
  STAGE_SUB_LABEL,
} from './stages';
import { cn } from '../../lib/utils';

// ─── Types ────────────────────────────────────────────────────────────────────

interface PipelineRailProps {
  tasksByStatus: Record<TaskStatus, { length: number }>;
  className?: string;
}

// ─── StageRing ────────────────────────────────────────────────────────────────

/**
 * A single circular stage ring with animated SVG arc, halo, icon, count badge,
 * label, and sub-label.
 */
function StageRing({
  status,
  count,
  isActive,
}: {
  status: TaskStatus;
  count: number;
  isActive: boolean;
}) {
  const accent = STAGE_ACCENT[status];
  const Icon = STAGE_ICON[status];
  const label = STAGE_LABEL[status];
  const subLabel = STAGE_SUB_LABEL[status];

  // SVG ring geometry — r=42, circumference ≈ 264
  const cx = 48;
  const cy = 48;
  const r = 42;

  return (
    <div className="rail-stage" style={{ '--rc': accent } as React.CSSProperties}>
      {/* Outer wrapper for count badge + ring */}
      <div className="rail-ring-wrap">
        {/* Count badge — top-right corner, JetBrains Mono */}
        <span
          className={cn('rail-count', count > 0 && 'rail-count--active')}
          aria-label={`${count} task${count !== 1 ? 's' : ''}`}
        >
          {count}
        </span>

        {/* Rotating dashed halo (visible when active) */}
        {isActive && (
          <svg
            className="rail-halo"
            viewBox="0 0 96 96"
            aria-hidden="true"
          >
            <circle
              cx={cx}
              cy={cy}
              r={r}
              fill="none"
              stroke={accent}
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeDasharray="8 6"
              opacity="0.55"
            />
          </svg>
        )}

        {/* Main ring SVG — track + animated arc */}
        <svg
          className="rail-ring-svg"
          viewBox="0 0 96 96"
          aria-hidden="true"
        >
          {/* Track */}
          <circle
            className="rail-ring-track"
            cx={cx}
            cy={cy}
            r={r}
          />
          {/* Animated arc — always visible but dim when count=0 */}
          <circle
            className={cn('rail-ring-arc', isActive && 'rail-ring-arc--active')}
            cx={cx}
            cy={cy}
            r={r}
            transform={`rotate(-90 ${cx} ${cy})`}
          />
        </svg>

        {/* Icon pill in the centre */}
        <div className={cn('rail-icon', isActive && 'rail-icon--active')}>
          <Icon
            className={cn(
              'rail-icon-svg',
              status === 'in_progress' && isActive && 'animate-spin',
            )}
            strokeWidth={1.7}
          />
        </div>
      </div>

      {/* Text below the ring */}
      <p className="rail-label">{label}</p>
      <p className="rail-sublabel">{subLabel}</p>
    </div>
  );
}

// ─── Connector ────────────────────────────────────────────────────────────────

/**
 * Dashed marching-ant connector between two consecutive rings.
 * Renders a flying package-box when the source ring is active.
 */
function Connector({
  sourceActive,
  connectorIndex,
}: {
  sourceActive: boolean;
  connectorIndex: number;
}) {
  // Stagger the fly animation so each connector starts at a different phase.
  // We use animation-delay to offset each connector uniquely.
  const flyDelay = `${connectorIndex * 1.1}s`;

  return (
    <div className="rail-connector" aria-hidden="true">
      <span className="rail-connector-line" />
      {sourceActive && (
        <span
          className="rail-pkg"
          style={{ animationDelay: flyDelay }}
        >
          <Package strokeWidth={1.7} />
        </span>
      )}
    </div>
  );
}

// ─── PipelineRail ─────────────────────────────────────────────────────────────

export function PipelineRail({ tasksByStatus, className }: PipelineRailProps) {
  /**
   * The "active" stage is the rightmost stage that has at least one task.
   * If nothing is in flight we fall back to no active stage (all idle).
   */
  const activeStatus = useMemo<TaskStatus | null>(() => {
    for (let i = PIPELINE_STAGES.length - 1; i >= 0; i--) {
      if (tasksByStatus[PIPELINE_STAGES[i]].length > 0) {
        return PIPELINE_STAGES[i];
      }
    }
    return null;
  }, [tasksByStatus]);

  return (
    <section
      className={cn('rail-panel', className)}
      aria-label="Pipeline stage overview"
    >
      {/* Brand sweep hairline */}
      <div className="rail-panel-sweep" aria-hidden="true" />

      <div className="rail-flow" role="list">
        {PIPELINE_STAGES.map((status, idx) => {
          const count = tasksByStatus[status].length;
          const isActive = count > 0;

          return (
            <div key={status} className="rail-flow-item" role="listitem">
              <StageRing
                status={status}
                count={count}
                isActive={isActive}
              />
              {/* Connector after every ring except the last */}
              {idx < PIPELINE_STAGES.length - 1 && (
                <Connector
                  sourceActive={isActive}
                  connectorIndex={idx}
                />
              )}
            </div>
          );
        })}
      </div>

      {/* Screen-reader summary */}
      <p className="sr-only">
        {activeStatus
          ? `Active stage: ${STAGE_LABEL[activeStatus]}. `
          : 'No active stages. '}
        {PIPELINE_STAGES.map(
          (s) => `${STAGE_LABEL[s]}: ${tasksByStatus[s].length} tasks`
        ).join(', ')}
      </p>
    </section>
  );
}
