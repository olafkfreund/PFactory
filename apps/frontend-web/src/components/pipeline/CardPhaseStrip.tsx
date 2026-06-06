/**
 * CardPhaseStrip — a compact horizontal strip of execution-phase chips
 * rendered at the bottom of a TaskCard (inside, as an overlay append).
 *
 * Chip states:
 *   done    — filled accent circle + check
 *   active  — accent colour with glowing pulse ring (CSS animation)
 *   failed  — Gruvbox red (#fb4934) with X mark
 *   pending — muted, no emphasis
 *
 * Phase derivation from Task:
 *   • phases listed in STRIP_PHASES (ordered meaningful subset of EXECUTION_PHASES)
 *   • phases BEFORE executionProgress.phase index → done
 *   • executionProgress.phase === this phase, status in_progress → active
 *   • executionProgress.phase === 'failed' → last completed strip phase = failed
 *   • phases AFTER → pending
 *   • task with no executionProgress / idle:
 *       - status 'done' → all done
 *       - otherwise    → all pending
 *
 * CSS animations defined in index.css (.cardphase-* classes).
 * prefers-reduced-motion: pulse suppressed globally in the existing
 * reduced-motion block in index.css.
 */

import { Check, X } from 'lucide-react';
import { cn } from '../../lib/utils';
import { PHASE_ORDER_INDEX } from '../../shared/constants/phase-protocol';
import type { Task } from '../../shared/types';
import type { ExecutionPhase } from '../../shared/constants/phase-protocol';

// ─── Strip config ─────────────────────────────────────────────────────────────

/**
 * The pipeline phases shown as chips in the card strip.
 * Excludes control/terminal states (idle, plan_review, complete, failed)
 * because those are workflow pause/exit states, not forward-progress stages.
 */
const STRIP_PHASES: ExecutionPhase[] = [
  'spec_creation',
  'planning',
  'coding',
  'qa_review',
  'qa_fixing',
];

/** Terse label for each strip chip. */
const CHIP_LABEL: Partial<Record<ExecutionPhase, string>> = {
  spec_creation: 'spec',
  planning:      'plan',
  coding:        'code',
  qa_review:     'qa',
  qa_fixing:     'fix',
};

/** Gruvbox accent per phase (from the project's Gruvbox palette). */
const PHASE_ACCENT: Partial<Record<ExecutionPhase, string>> = {
  spec_creation: '#fabd2f',  // yellow — ideation
  planning:      '#fabd2f',  // yellow
  coding:        '#fe8019',  // orange — active build
  qa_review:     '#d3869b',  // purple — scanning
  qa_fixing:     '#fe8019',  // orange — still fixing
};

/** Gruvbox red for a failed chip. */
const FAILED_COLOR = '#fb4934';

// ─── State resolution ─────────────────────────────────────────────────────────

type ChipState = 'done' | 'active' | 'failed' | 'pending';

function resolveChipStates(task: Task): Partial<Record<ExecutionPhase, ChipState>> {
  const result: Partial<Record<ExecutionPhase, ChipState>> = {};
  const phase = task.executionProgress?.phase;

  // No live phase data (not started or idle)
  if (!phase || phase === 'idle') {
    const allDone = task.status === 'done';
    STRIP_PHASES.forEach((p) => { result[p] = allDone ? 'done' : 'pending'; });
    return result;
  }

  // All phases done (complete terminal)
  if (phase === 'complete') {
    STRIP_PHASES.forEach((p) => { result[p] = 'done'; });
    return result;
  }

  const currentIdx = PHASE_ORDER_INDEX[phase];

  // Assign done/active/pending based on ordering
  STRIP_PHASES.forEach((p) => {
    const pIdx = PHASE_ORDER_INDEX[p];
    if (pIdx < currentIdx) {
      result[p] = 'done';
    } else if (p === phase) {
      result[p] = 'active';
    } else {
      result[p] = 'pending';
    }
  });

  // Failed terminal: find the last 'done' chip and flip it to failed,
  // or if nothing was done yet, mark the first strip phase failed.
  if (phase === 'failed') {
    let lastDonePos = -1;
    STRIP_PHASES.forEach((p, i) => {
      if (result[p] === 'done') lastDonePos = i;
    });
    if (lastDonePos >= 0) {
      result[STRIP_PHASES[lastDonePos]] = 'failed';
    } else {
      result[STRIP_PHASES[0]] = 'failed';
    }
    // Everything after the failed chip stays pending
    const failedPos = lastDonePos >= 0 ? lastDonePos : 0;
    STRIP_PHASES.forEach((p, i) => {
      if (i > failedPos && result[p] !== 'done') {
        result[p] = 'pending';
      }
    });
  }

  return result;
}

// ─── Single chip ─────────────────────────────────────────────────────────────

function PhaseChip({
  phase,
  state,
}: {
  phase: ExecutionPhase;
  state: ChipState;
}) {
  const label = CHIP_LABEL[phase] ?? phase;
  const accent = PHASE_ACCENT[phase] ?? '#a89984';
  const chipColor = state === 'failed' ? FAILED_COLOR : accent;

  return (
    <div
      className={cn(
        'cardphase-chip',
        state === 'done'    && 'cardphase-chip--done',
        state === 'active'  && 'cardphase-chip--active',
        state === 'failed'  && 'cardphase-chip--failed',
        state === 'pending' && 'cardphase-chip--pending',
      )}
      style={
        state !== 'pending'
          ? ({ '--cp': chipColor } as React.CSSProperties)
          : undefined
      }
      aria-label={`${label}: ${state}`}
      title={`${label} — ${state}`}
    >
      {/* Dot / icon inside the chip */}
      <span className="cardphase-dot" aria-hidden="true">
        {state === 'done'   && <Check strokeWidth={3} className="cardphase-icon" />}
        {state === 'failed' && <X     strokeWidth={3} className="cardphase-icon" />}
      </span>

      {/* Short text label */}
      <span className="cardphase-label">{label}</span>

      {/* Glowing pulse ring — only rendered while active (CSS drives the animation) */}
      {state === 'active' && (
        <span className="cardphase-pulse-ring" aria-hidden="true" />
      )}
    </div>
  );
}

// ─── CardPhaseStrip ───────────────────────────────────────────────────────────

interface CardPhaseStripProps {
  task: Task;
  className?: string;
}

export function CardPhaseStrip({ task, className }: CardPhaseStripProps) {
  // Skip rendering on archived tasks — they are visually de-emphasised already
  if (task.metadata?.archivedAt) return null;

  const states = resolveChipStates(task);

  return (
    <div
      className={cn('cardphase-strip', className)}
      aria-label="Execution phase strip"
      role="list"
    >
      {STRIP_PHASES.map((phase, idx) => (
        <div key={phase} className="cardphase-strip-item" role="listitem">
          <PhaseChip phase={phase} state={states[phase] ?? 'pending'} />
          {/* Connector between chips */}
          {idx < STRIP_PHASES.length - 1 && (
            <span className="cardphase-sep" aria-hidden="true" />
          )}
        </div>
      ))}
    </div>
  );
}
