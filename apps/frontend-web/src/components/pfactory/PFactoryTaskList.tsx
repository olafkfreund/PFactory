/**
 * PFactory Task List — Task 10 (#11) commit 2.
 *
 * Table of PFactory workspaces sorted by ``updated_at`` desc (newest
 * first). Each row is clickable; ``onSelectTask`` fires with the
 * selected ``spec_id`` so the parent can route to a detail page.
 *
 * Loading / empty / error states are all rendered inline — the
 * parent is purely a routing host and doesn't need to handle them.
 *
 * Tests inject ``fetchFn`` so they can mock the API without MSW or
 * fetch-mock.
 */

import { useEffect, useState } from 'react';
import { Loader2, AlertTriangle, Inbox } from 'lucide-react';

import {
  listTasks,
  type PFactoryTaskSummary,
} from '../../lib/pfactory-api';
import { formatRelativeTime } from '../../lib/utils';

// Locale-independent short date ("May 28") for the row's secondary timestamp.
const _MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function shortDate(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : `${_MONTHS[d.getMonth()]} ${d.getDate()}`;
}

interface Props {
  /** Called when a row is clicked. */
  onSelectTask?: (specId: string) => void;
  /** Test seam: inject a fake fetch (default: global fetch). */
  fetchFn?: typeof fetch;
  /**
   * Background auto-refresh interval in ms so live status changes (e.g. a
   * watchdog `stalled` flip, #95) appear without a manual reload. Set to 0
   * to disable. Default 5000.
   */
  pollMs?: number;
}

// ── Status → Tailwind colour ────────────────────────────────────────

/**
 * Map a backend status string to a Tailwind colour palette name.
 * Used to colour the badge in each row.
 *
 * Buckets:
 *   - success: evaluated, triaged
 *   - active:  planning, generating, evaluating, triaging, in-flight
 *   - error:   *_failed, stuck, stalled, replan_needed
 *   - empty:   *_empty
 *   - default: pending / unknown → gray
 */
export function statusColor(status: string | null): string {
  if (!status) return 'gray';
  if (status === 'triaged' || status === 'evaluated') return 'green';
  if (
    status.endsWith('_failed') ||
    status === 'stuck' ||
    status === 'stalled' ||
    status === 'replan_needed'
  ) {
    return 'red';
  }
  if (status.endsWith('_empty')) return 'yellow';
  if (status === 'pending' || status === 'idle') return 'gray';
  // Anything else is in-flight (planning, generating, evaluating, triaging, ...)
  return 'blue';
}

// Per-status visual language: badge (pill), the leading dot, the left accent
// edge, and the dot's offset ring. Surgical colour — verdict is the hero signal.
const BADGE_CLASSES: Record<string, string> = {
  green: 'bg-success/10 text-success ring-1 ring-inset ring-success/25',
  red: 'bg-destructive/10 text-destructive ring-1 ring-inset ring-destructive/25',
  blue: 'bg-info/10 text-info ring-1 ring-inset ring-info/25',
  yellow: 'bg-warning/10 text-warning ring-1 ring-inset ring-warning/25',
  gray: 'bg-muted text-muted-foreground ring-1 ring-inset ring-border',
};
const DOT_CLASSES: Record<string, string> = {
  green: 'bg-success', red: 'bg-destructive', blue: 'bg-info',
  yellow: 'bg-warning', gray: 'bg-muted-foreground',
};
const ACCENT_CLASSES: Record<string, string> = {
  green: 'bg-success', red: 'bg-destructive', blue: 'bg-info',
  yellow: 'bg-warning', gray: 'bg-border',
};

function StatusBadge({ status }: { status: string | null }) {
  const color = statusColor(status);
  const cls = BADGE_CLASSES[color] || BADGE_CLASSES.gray;
  const live = color === 'blue'; // in-flight → breathing dot
  return (
    <span
      data-testid="status-badge"
      data-status-color={color}
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium tracking-tight ${cls}`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${DOT_CLASSES[color]} ${live ? 'animate-pulse' : ''}`}
        aria-hidden
      />
      {status ?? '—'}
    </span>
  );
}

// ── Component states ────────────────────────────────────────────────

function LoadingState() {
  return (
    <div role="status" className="flex items-center gap-2 p-6 text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      <span>Loading tasks…</span>
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div role="alert" className="flex items-center gap-2 p-6 text-destructive">
      <AlertTriangle className="h-4 w-4" aria-hidden />
      <span>{message}</span>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center gap-2 p-12 text-muted-foreground">
      <Inbox className="h-8 w-8" aria-hidden />
      <p>No PFactory tasks yet.</p>
      <p className="text-xs">Workspaces appear here once an AIFactory handover lands.</p>
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────

export function PFactoryTaskList({ onSelectTask, fetchFn, pollMs = 5000 }: Props) {
  const [tasks, setTasks] = useState<PFactoryTaskSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    listTasks({ fetchFn })
      .then((response) => {
        if (cancelled) return;
        setTasks(response.tasks);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [fetchFn]);

  // Background auto-refresh: re-fetch on an interval so live status changes
  // (e.g. a watchdog `stalled` flip, #95) surface without a manual reload.
  // Updates the list only on success — a transient poll error keeps the
  // last-good rows on screen rather than flipping to the error state.
  useEffect(() => {
    if (!pollMs || pollMs <= 0) return;
    const id = setInterval(() => {
      listTasks({ fetchFn })
        .then((response) => setTasks(response.tasks))
        .catch(() => {
          /* keep last-good list on a transient poll error */
        });
    }, pollMs);
    return () => clearInterval(id);
  }, [pollMs, fetchFn]);

  if (loading) return <LoadingState />;
  if (error) return <ErrorState message={error} />;
  if (tasks.length === 0) return <EmptyState />;

  return (
    <div
      data-testid="pfactory-task-list"
      aria-label="PFactory tasks"
      className="flex flex-col gap-1.5"
    >
      {/* column legend — quiet, lets the rows breathe */}
      <div className="flex items-center px-4 pb-1 text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground/70">
        <span className="flex-1">Spec</span>
        <span className="w-[120px]">Status</span>
        <span className="hidden w-[200px] md:block">Phase</span>
        <span className="w-20 text-right">Updated</span>
      </div>

      {tasks.map((task) => {
        const color = statusColor(task.status);
        const rel = formatRelativeTime(task.updated_at);
        return (
          <button
            type="button"
            key={`${task.project_id}::${task.spec_id}`}
            data-testid={`task-row-${task.spec_id}`}
            data-spec-id={task.spec_id}
            onClick={() => onSelectTask?.(task.spec_id)}
            className="group relative flex items-center gap-4 overflow-hidden rounded-lg border border-border/60 bg-card/40 px-4 py-3 text-left transition-all duration-150 hover:border-border hover:bg-muted/40 hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {/* left accent edge — reveals the status colour on hover */}
            <span
              className={`absolute inset-y-0 left-0 w-[3px] ${ACCENT_CLASSES[color]} opacity-0 transition-opacity duration-150 group-hover:opacity-100`}
              aria-hidden
            />
            {/* status dot */}
            <span
              className={`h-2 w-2 shrink-0 rounded-full ${DOT_CLASSES[color]} ${color === 'blue' ? 'animate-pulse' : ''}`}
              aria-hidden
            />
            {/* spec id + project */}
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="truncate font-mono text-sm font-semibold text-foreground transition-colors group-hover:text-primary">
                {task.spec_id}
              </span>
              <span className="truncate text-xs text-muted-foreground">{task.project_id}</span>
            </span>
            {/* status */}
            <span className="w-[120px] shrink-0">
              <StatusBadge status={task.status} />
            </span>
            {/* phase — mono chip, the data plane */}
            <span className="hidden w-[200px] shrink-0 md:block">
              <span className="inline-block max-w-full truncate rounded bg-muted/60 px-2 py-0.5 align-middle font-mono text-[11px] text-muted-foreground">
                {task.phase ?? '—'}
              </span>
            </span>
            {/* time — relative over absolute, tabular for alignment */}
            <span className="w-20 shrink-0 text-right leading-tight">
              <span className="block text-xs font-medium tabular-nums text-foreground/80">
                {rel || '—'}
              </span>
              <span className="block text-[11px] tabular-nums text-muted-foreground">
                {shortDate(task.updated_at)}
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
