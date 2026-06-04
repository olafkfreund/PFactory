/**
 * EmitPanel — emit the approved plan to a repository.
 *
 * Repo input + Dry-run toggle + Emit button.
 * Renders emit_result.planned (epic + children) and any errors.
 */

import { useState } from 'react';
import { Send, Loader2, CheckCircle2, AlertTriangle, GitBranch, ChevronRight, ChevronDown } from 'lucide-react';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Badge } from '../ui/badge';
import { cn } from '../../lib/utils';
import { usePlanStore } from '../../stores/plan-store';
import type { PlanSession, EmitPlannedItem } from '../../shared/types/plan';

interface Props {
  session: PlanSession;
  onUpdated?: (session: PlanSession) => void;
}

function labelTone(label: string): string {
  if (label === 'pfactory') return 'bg-primary/15 text-primary';
  if (label.startsWith('handoff:')) return 'bg-blue-500/15 text-blue-600';
  if (label.startsWith('sev:') || label.startsWith('priority:p0')) return 'bg-destructive/15 text-destructive';
  if (label === 'epic') return 'bg-muted text-muted-foreground';
  return 'bg-muted text-muted-foreground';
}

function PlannedItemRow({ item }: { item: EmitPlannedItem }) {
  const [open, setOpen] = useState(false);
  const isEpic = item.kind === 'epic';
  return (
    <div className="rounded-lg border border-border bg-card/40 px-3 py-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start gap-2 text-left"
        aria-expanded={open}
      >
        <GitBranch className={cn('mt-0.5 h-3.5 w-3.5 shrink-0', isEpic ? 'text-primary' : 'text-muted-foreground')} aria-hidden />
        <div className="flex-1 min-w-0">
          <p className="text-xs font-medium leading-snug">
            {isEpic ? 'Epic · ' : item.key ? `${item.key} · ` : ''}{item.title}
          </p>
          <div className="mt-1 flex flex-wrap gap-1">
            {item.labels.map((l) => (
              <span key={l} className={cn('rounded px-1.5 py-0.5 text-[10px] font-mono', labelTone(l))}>{l}</span>
            ))}
          </div>
        </div>
        {open ? <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-60" aria-hidden /> : <ChevronRight className="h-3.5 w-3.5 shrink-0 opacity-60" aria-hidden />}
      </button>
      {open && item.body && (
        <pre className="mt-2 max-h-64 overflow-auto rounded bg-muted/40 p-2 text-[11px] leading-relaxed whitespace-pre-wrap">
          {item.body}
        </pre>
      )}
    </div>
  );
}

export function EmitPanel({ session, onUpdated }: Props) {
  const store = usePlanStore();
  const { sessionLoading, error } = store;

  const [repo, setRepo] = useState('');
  const [dryRun, setDryRun] = useState(true);
  const [localError, setLocalError] = useState<string | null>(null);

  const emitResult = session.emit_result;
  const isEmitted = session.status === 'emitted';

  const handleEmit = async () => {
    if (!repo.trim()) {
      setLocalError('Repository path or URL is required.');
      return;
    }
    setLocalError(null);
    store.clearError();
    try {
      await store.emitCurrentSession({ repo: repo.trim(), dry_run: dryRun });
      const updated = store.currentSession;
      if (updated) onUpdated?.(updated);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
    }
  };

  const displayError = localError ?? error;

  return (
    <div className="flex flex-col gap-5" data-testid="emit-panel">
      {/* Emit form */}
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="emit-repo" className="text-sm font-medium text-foreground">
            Repository <span className="text-destructive">*</span>
          </label>
          <Input
            id="emit-repo"
            placeholder="e.g. github.com/org/repo or /path/to/repo"
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            aria-label="Repository"
            data-testid="emit-repo-input"
          />
        </div>

        <div className="flex items-center gap-2">
          <input
            id="dry-run"
            type="checkbox"
            checked={dryRun}
            onChange={(e) => setDryRun(e.target.checked)}
            className="h-4 w-4 accent-primary"
            aria-label="Dry run"
            data-testid="dry-run-checkbox"
          />
          <label htmlFor="dry-run" className="text-sm font-medium text-foreground cursor-pointer">
            Dry run
          </label>
          <span className="text-xs text-muted-foreground">
            (preview the plan without creating issues)
          </span>
        </div>

        {displayError && (
          <div role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {displayError}
          </div>
        )}

        <Button
          onClick={handleEmit}
          disabled={sessionLoading || !repo.trim()}
          data-testid="emit-btn"
          aria-label="Emit plan"
        >
          {sessionLoading ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
          ) : (
            <Send className="mr-2 h-4 w-4" aria-hidden />
          )}
          {dryRun ? 'Dry-run emit' : 'Emit to repo'}
        </Button>
      </div>

      {/* Emit result */}
      {emitResult && (
        <div className="flex flex-col gap-4 rounded-xl border border-border/60 bg-card/40 p-4">
          <div className="flex items-center gap-2">
            {emitResult.errors.length === 0
              ? <CheckCircle2 className="h-4 w-4 text-success" aria-hidden />
              : <AlertTriangle className="h-4 w-4 text-warning" aria-hidden />
            }
            <span className="text-sm font-medium text-foreground">
              {emitResult.dry_run ? 'Dry-run result' : 'Emit result'}
            </span>
            {emitResult.dry_run && (
              <Badge variant="muted">dry run</Badge>
            )}
            {isEmitted && (
              <Badge variant="success">emitted</Badge>
            )}
          </div>

          {/* Planned items — epic + children with their taxonomy labels */}
          <div className="flex flex-col gap-2">
            {emitResult.planned.map((item, i) => (
              <PlannedItemRow key={i} item={item} />
            ))}
            {emitResult.planned.length === 0 && (
              <p className="text-xs text-muted-foreground">Nothing planned.</p>
            )}
          </div>

          {/* Errors */}
          {emitResult.errors.length > 0 && (
            <div
              role="alert"
              className="flex flex-col gap-1.5 rounded-md bg-destructive/10 px-3 py-2"
            >
              <p className="text-xs font-semibold text-destructive">Errors:</p>
              {emitResult.errors.map((e, i) => (
                <p key={i} className="text-xs text-destructive">{e}</p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
