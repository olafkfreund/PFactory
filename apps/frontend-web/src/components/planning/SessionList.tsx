/**
 * SessionList — left-pane list of PlanSession summaries.
 *
 * Shows status badge, target_kind, plan_type, created_at.
 * Calls onSelect when a row is clicked.
 */

import { useEffect } from 'react';
import { Loader2, AlertTriangle, BookOpen, RefreshCw } from 'lucide-react';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { cn } from '../../lib/utils';
import { formatRelativeTime } from '../../lib/utils';
import { usePlanStore } from '../../stores/plan-store';
import type { SessionSummary, PlanSessionStatus } from '../../shared/types/plan';

const STATUS_COLOR: Record<PlanSessionStatus, string> = {
  ingested: 'bg-muted text-muted-foreground',
  processing: 'bg-info/10 text-info',
  reviewing: 'bg-info/10 text-info',
  processed: 'bg-info/10 text-info',
  approved: 'bg-success/10 text-success',
  rejected: 'bg-destructive/10 text-destructive',
  emitted: 'bg-success/10 text-success',
};

function SessionRow({
  session,
  isSelected,
  onSelect,
}: {
  session: SessionSummary;
  isSelected: boolean;
  onSelect: (id: string) => void;
}) {
  const statusCls = STATUS_COLOR[session.status] ?? STATUS_COLOR.ingested;
  const rel = formatRelativeTime(session.created_at);

  return (
    <button
      type="button"
      key={session.session_id}
      data-testid={`session-row-${session.session_id}`}
      onClick={() => onSelect(session.session_id)}
      aria-label={`Select session ${session.session_id}`}
      aria-pressed={isSelected}
      className={cn(
        'group relative flex w-full flex-col gap-1.5 overflow-hidden rounded-lg border px-4 py-3 text-left transition-all duration-150',
        isSelected
          ? 'border-primary/50 bg-primary/5'
          : 'border-border/60 bg-card/40 hover:border-border hover:bg-muted/40'
      )}
    >
      {/* Left accent */}
      <span
        className={cn(
          'absolute inset-y-0 left-0 w-[3px] rounded-l-lg transition-opacity duration-150',
          isSelected ? 'bg-primary opacity-100' : 'bg-primary opacity-0 group-hover:opacity-40'
        )}
        aria-hidden
      />

      <div className="flex items-start gap-2">
        <span className={cn('inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium', statusCls)}>
          {session.status}
        </span>
        <span className="ml-auto text-xs text-muted-foreground shrink-0">{rel}</span>
      </div>

      <span className="truncate text-sm font-medium text-foreground">{session.title || session.session_id}</span>

      <div className="flex items-center gap-1.5 flex-wrap">
        <Badge variant="muted" className="text-[10px] font-normal">{session.target_kind}</Badge>
        {session.plan_type && (
          <Badge variant="outline" className="text-[10px] font-normal font-mono">{session.plan_type}</Badge>
        )}
        {session.children > 0 && (
          <Badge variant="secondary" className="text-[10px]">{session.children} items</Badge>
        )}
        {session.gates_passed === true && (
          <Badge variant="success" className="text-[10px]">gates ok</Badge>
        )}
        {session.gates_passed === false && (
          <Badge variant="destructive" className="text-[10px]">gates fail</Badge>
        )}
      </div>
    </button>
  );
}

interface Props {
  selectedId: string | null;
  onSelect: (sessionId: string) => void;
}

export function SessionList({ selectedId, onSelect }: Props) {
  const store = usePlanStore();
  const { sessions, loading, error } = store;

  useEffect(() => {
    store.loadSessions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex flex-col h-full" data-testid="session-list">
      {/* Header */}
      <div className="flex items-center justify-between pb-3 border-b border-border shrink-0">
        <h2 className="text-sm font-semibold text-foreground">Sessions</h2>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => store.loadSessions()}
          disabled={loading}
          aria-label="Refresh sessions"
        >
          <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} aria-hidden />
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto mt-3 space-y-2">
        {loading && sessions.length === 0 && (
          <div role="status" className="flex items-center gap-2 py-8 text-muted-foreground justify-center">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            <span className="text-sm">Loading…</span>
          </div>
        )}

        {error && (
          <div role="alert" className="flex items-center gap-2 py-4 text-destructive">
            <AlertTriangle className="h-4 w-4" aria-hidden />
            <span className="text-sm">{error}</span>
          </div>
        )}

        {!loading && !error && sessions.length === 0 && (
          <div className="flex flex-col items-center gap-2 py-12 text-muted-foreground">
            <BookOpen className="h-8 w-8" aria-hidden />
            <p className="text-sm">No sessions yet.</p>
            <p className="text-xs">Create a new plan to get started.</p>
          </div>
        )}

        {sessions.map((session) => (
          <SessionRow
            key={session.session_id}
            session={session}
            isSelected={session.session_id === selectedId}
            onSelect={onSelect}
          />
        ))}
      </div>
    </div>
  );
}
