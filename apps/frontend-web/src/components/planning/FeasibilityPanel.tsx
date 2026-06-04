/**
 * FeasibilityPanel — surfaces the Phase C feasibility engine output:
 * estimated cloud cost (with per-line breakdown + confidence), effort/time, and
 * technical-access verification (IAM-simulation results). Read-only.
 */

import { DollarSign, Clock, KeyRound, Check, X, HelpCircle } from 'lucide-react';
import { Badge } from '../ui/badge';
import { cn } from '../../lib/utils';
import type { PlanSession } from '../../shared/types/plan';

const confidenceColor: Record<string, string> = {
  high: 'bg-emerald-500/15 text-emerald-600',
  medium: 'bg-amber-500/15 text-amber-600',
  low: 'bg-muted text-muted-foreground',
};

export function FeasibilityPanel({ session }: { session: PlanSession }) {
  const epic = session.epic;
  const cost = epic?.cost_estimate ?? null;
  const effort = epic?.effort_estimate ?? null;
  const access = epic?.access_requirements ?? [];

  if (!cost && !effort && access.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No feasibility data yet — process the plan to estimate cost, time, and access.
      </p>
    );
  }

  return (
    <div className="space-y-5" data-testid="feasibility-panel">
      {/* Cost */}
      {cost && (
        <section className="rounded-xl border border-border bg-card/40 p-4">
          <header className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <DollarSign className="h-4 w-4 text-primary" aria-hidden />
              <h3 className="text-sm font-semibold">Estimated cost</h3>
            </div>
            <Badge className={cn('text-[10px]', confidenceColor[cost.confidence])}>
              {cost.confidence} confidence
            </Badge>
          </header>
          <p className="mt-2 text-2xl font-bold tabular-nums">
            ${cost.monthly_usd.toLocaleString(undefined, { maximumFractionDigits: 0 })}
            <span className="text-sm font-normal text-muted-foreground"> / month</span>
          </p>
          <p className="text-[11px] text-muted-foreground">priced via {cost.source}</p>
          {cost.lines.length > 0 && (
            <ul className="mt-3 space-y-1">
              {cost.lines.map((l, i) => (
                <li key={i} className="flex items-center justify-between text-xs">
                  <span className="font-mono text-muted-foreground">{l.resource}</span>
                  <span className="tabular-nums">${l.monthly_usd.toLocaleString()}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {/* Effort */}
      {effort && effort.story_points > 0 && (
        <section className="rounded-xl border border-border bg-card/40 p-4">
          <header className="flex items-center gap-2">
            <Clock className="h-4 w-4 text-primary" aria-hidden />
            <h3 className="text-sm font-semibold">Estimated effort</h3>
            <Badge className={cn('ml-auto text-[10px]', confidenceColor[effort.confidence])}>
              {effort.confidence} confidence
            </Badge>
          </header>
          <p className="mt-2 text-sm">
            <span className="text-lg font-bold tabular-nums">{effort.story_points}</span> pts
            {' · '}
            <span className="tabular-nums">
              {effort.duration_days_low}–{effort.duration_days_high}
            </span> dev-days
          </p>
          {effort.assumptions.length > 0 && (
            <ul className="mt-2 list-disc pl-4 text-[11px] text-muted-foreground space-y-0.5">
              {effort.assumptions.map((a, i) => <li key={i}>{a}</li>)}
            </ul>
          )}
        </section>
      )}

      {/* Access */}
      {access.length > 0 && (
        <section className="rounded-xl border border-border bg-card/40 p-4">
          <header className="flex items-center gap-2">
            <KeyRound className="h-4 w-4 text-primary" aria-hidden />
            <h3 className="text-sm font-semibold">Technical access</h3>
          </header>
          <ul className="mt-2 space-y-1">
            {access.map((a, i) => {
              const Icon = a.granted === true ? Check : a.granted === false ? X : HelpCircle;
              const tone =
                a.granted === true ? 'text-emerald-600'
                  : a.granted === false ? 'text-destructive'
                    : 'text-muted-foreground';
              return (
                <li key={i} className="flex items-center gap-2 text-xs">
                  <Icon className={cn('h-3.5 w-3.5 shrink-0', tone)} aria-hidden />
                  <span className="font-mono">{a.provider}:{a.action}</span>
                  {a.granted === false && (
                    <Badge variant="destructive" className="ml-auto text-[10px] py-0 h-4">denied</Badge>
                  )}
                </li>
              );
            })}
          </ul>
          <p className="mt-2 text-[11px] text-muted-foreground">
            Verified by cloud IAM policy-simulation.
          </p>
        </section>
      )}
    </div>
  );
}
