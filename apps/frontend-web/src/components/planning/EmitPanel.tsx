/**
 * EmitPanel — emit the approved plan to a repository.
 *
 * Repo input + Dry-run toggle + Emit button.
 * Renders emit_result.planned (epic + children) and any errors.
 */

import { useState } from 'react';
import { Send, Loader2, CheckCircle2, AlertTriangle, GitBranch } from 'lucide-react';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Badge } from '../ui/badge';
import { cn } from '../../lib/utils';
import { usePlanStore } from '../../stores/plan-store';
import type { PlanSession } from '../../shared/types/plan';

interface Props {
  session: PlanSession;
  onUpdated?: (session: PlanSession) => void;
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

          {/* Planned items */}
          <div className="flex flex-col gap-2">
            {emitResult.planned.epic && (
              <div className="flex items-center gap-2">
                <GitBranch className="h-3.5 w-3.5 text-muted-foreground shrink-0" aria-hidden />
                <span className="text-xs font-semibold text-foreground">Epic:</span>
                <span className="text-xs font-mono text-primary">{emitResult.planned.epic}</span>
              </div>
            )}
            {emitResult.planned.children.length > 0 && (
              <div className="flex flex-col gap-1">
                <p className="text-xs font-semibold text-foreground mb-0.5">
                  Children ({emitResult.planned.children.length}):
                </p>
                <ul className="space-y-0.5">
                  {emitResult.planned.children.map((key, i) => (
                    <li key={i} className="flex items-center gap-2 text-xs text-foreground/80">
                      <span className="h-1 w-1 rounded-full bg-muted-foreground" aria-hidden />
                      <span className="font-mono">{key}</span>
                    </li>
                  ))}
                </ul>
              </div>
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
