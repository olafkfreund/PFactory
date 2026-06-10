/**
 * PlanBoard — kanban of planning sessions across the workflow columns
 * (Plans ready → Human Review → Done), bucketed by the session's board_state.
 * Read-only: a plan moves columns automatically as it is processed / reviewed /
 * approved — you open a card to act on it, you don't drag.
 *
 * The transient "In Progress" / "AI Review" stages are intentionally not shown
 * as columns — a plan only passes through them momentarily during process(), so
 * they fold into "Plans ready" until the plan lands in Human Review. This keeps
 * the Planning board on the same three-stage brand language as the other
 * factory cockpits.
 */

import { useMemo } from 'react';
import { cn } from '../../lib/utils';
import { TASK_STATUS_LABELS } from '../../shared/constants/task';
import type { SessionSummary, BoardColumn } from '../../shared/types/plan';
import { PipelineRail } from '../pipeline/PipelineRail';
import type { TaskStatus } from '../../shared/types';

// Visible board columns / rail stages (transient in_progress + ai_review folded
// into backlog — see file header).
type VisibleColumn = 'backlog' | 'human_review' | 'done';
const COLUMNS: VisibleColumn[] = ['backlog', 'human_review', 'done'];

const COLUMN_ACCENT: Record<VisibleColumn, string> = {
  backlog: 'border-t-muted-foreground/40',
  human_review: 'border-t-fuchsia-500',
  done: 'border-t-emerald-500',
};

// Project a session onto one of the three visible columns. board_state (or, for
// older payloads, status) is collapsed so the transient processing/reviewing
// states show under "Plans ready" rather than vanishing.
function columnFor(s: SessionSummary): VisibleColumn {
  const raw: BoardColumn =
    s.board_state ??
    (s.status === 'approved' || s.status === 'emitted'
      ? 'done'
      : s.status === 'rejected' || s.status === 'processed'
        ? 'human_review'
        : 'backlog');
  if (raw === 'done') return 'done';
  if (raw === 'human_review') return 'human_review';
  return 'backlog'; // backlog + transient in_progress / ai_review
}

interface Props {
  sessions: SessionSummary[];
  selectedId: string | null;
  onSelect: (sessionId: string) => void;
}

export function PlanBoard({ sessions, selectedId, onSelect }: Props) {
  const buckets = useMemo(() => {
    const map: Record<VisibleColumn, SessionSummary[]> = {
      backlog: [], human_review: [], done: [],
    };
    for (const s of sessions) map[columnFor(s)].push(s);
    return map;
  }, [sessions]);

  return (
    <div className="flex flex-col gap-4" data-testid="plan-board">
      {/* Animated stage rail — ring medallions + marching-ant connectors,
          driven by the SAME plan sessions shown in the columns below (counts
          per board_state). Restores the pipeline overview removed in v0.6.5. */}
      <PipelineRail
        tasksByStatus={buckets as unknown as Record<TaskStatus, { length: number }>}
        stages={COLUMNS as unknown as readonly TaskStatus[]}
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {COLUMNS.map((col) => (
        <section key={col} className="flex flex-col gap-2">
          <header
            className={cn(
              'flex items-center justify-between rounded-t-md border-t-2 bg-card/40 px-2.5 py-1.5',
              COLUMN_ACCENT[col],
            )}
          >
            <span className="text-xs font-semibold">{TASK_STATUS_LABELS[col] ?? col}</span>
            <span className="rounded-full bg-muted px-1.5 text-[10px] text-muted-foreground tabular-nums">
              {buckets[col].length}
            </span>
          </header>
          <div className="flex flex-col gap-2">
            {buckets[col].length === 0 ? (
              <p className="px-2 py-3 text-[11px] text-muted-foreground/60">—</p>
            ) : (
              buckets[col].map((s) => (
                <button
                  key={s.session_id}
                  type="button"
                  onClick={() => onSelect(s.session_id)}
                  className={cn(
                    'rounded-lg border border-border bg-card px-3 py-2 text-left transition-colors hover:border-primary/50',
                    selectedId === s.session_id && 'ring-1 ring-primary',
                  )}
                  data-testid="plan-card"
                >
                  <p className="truncate text-xs font-medium">{s.title}</p>
                  <div className="mt-1 flex items-center gap-1.5 text-[10px] text-muted-foreground">
                    {s.plan_type && (
                      <span className="rounded bg-muted px-1.5 py-0.5">{s.plan_type}</span>
                    )}
                    {s.children > 0 && <span>{s.children} issues</span>}
                    {s.gates_passed === false && (
                      <span className="ml-auto text-destructive">needs attention</span>
                    )}
                  </div>
                </button>
              ))
            )}
          </div>
        </section>
        ))}
      </div>
    </div>
  );
}
