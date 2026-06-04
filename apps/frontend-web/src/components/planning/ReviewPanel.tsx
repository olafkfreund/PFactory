/**
 * ReviewPanel — per-lens score bars, aggregate score, gates badge,
 * and findings drill-down with severity colour coding.
 *
 * Blocking findings are highlighted with a destructive ring.
 */

import { useState } from 'react';
import { ChevronDown, ChevronRight, ShieldCheck, ShieldX, AlertTriangle, Info, AlertCircle, Link as LinkIcon } from 'lucide-react';
import { Badge } from '../ui/badge';
import { Progress } from '../ui/progress';
import { cn } from '../../lib/utils';
import type { PlanReview, ReviewLens, ReviewFinding } from '../../shared/types/plan';

// ── Severity ──────────────────────────────────────────────────────────

const SEVERITY_META: Record<
  string,
  { label: string; icon: React.ElementType; className: string }
> = {
  critical: {
    label: 'Critical',
    icon: AlertCircle,
    className: 'text-destructive bg-destructive/10 border-destructive/30',
  },
  high: {
    label: 'High',
    icon: AlertTriangle,
    className: 'text-destructive bg-destructive/5 border-destructive/20',
  },
  medium: {
    label: 'Medium',
    icon: AlertTriangle,
    className: 'text-warning bg-warning/10 border-warning/30',
  },
  low: {
    label: 'Low',
    icon: Info,
    className: 'text-info bg-info/10 border-info/30',
  },
  info: {
    label: 'Info',
    icon: Info,
    className: 'text-muted-foreground bg-muted border-border',
  },
};

function severityMeta(severity: string) {
  return SEVERITY_META[severity] ?? SEVERITY_META.info;
}

// ── FindingRow ────────────────────────────────────────────────────────

function FindingRow({ finding }: { finding: ReviewFinding }) {
  const [open, setOpen] = useState(false);
  const meta = severityMeta(finding.severity);
  const SevIcon = meta.icon;

  return (
    <div
      className={cn(
        'rounded-lg border px-3 py-2',
        meta.className,
        finding.blocking && 'ring-1 ring-destructive/50',
      )}
      data-testid={finding.blocking ? 'blocking-finding' : 'finding'}
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start gap-2 text-left"
        aria-label={`Toggle finding: ${finding.title}`}
      >
        <SevIcon className="h-3.5 w-3.5 shrink-0 mt-0.5" aria-hidden />
        <span className="flex-1 text-xs font-medium leading-snug">{finding.title}</span>
        {finding.blocking && (
          <Badge variant="destructive" className="shrink-0 text-[10px] py-0 h-4">blocking</Badge>
        )}
        {open
          ? <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-60" aria-hidden />
          : <ChevronRight className="h-3.5 w-3.5 shrink-0 opacity-60" aria-hidden />
        }
      </button>
      {open && (
        <div className="mt-2 ml-5 space-y-1.5">
          {finding.detail && (
            <p className="text-xs leading-relaxed opacity-90">{finding.detail}</p>
          )}
          {finding.citations && finding.citations.length > 0 && (
            <div className="space-y-1 rounded-md bg-background/40 px-2 py-1.5">
              {finding.citations.map((c, i) => (
                <div key={i} className="text-[11px] leading-snug">
                  {c.why && <p className="opacity-80"><span className="font-medium">Why:</span> {c.why}</p>}
                  {c.uri ? (
                    <a
                      href={c.uri}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-primary hover:underline"
                    >
                      <LinkIcon className="h-3 w-3" aria-hidden />
                      {c.title || c.source || c.uri}
                    </a>
                  ) : (
                    (c.title || c.source) && (
                      <span className="opacity-60">source: {c.title || c.source}</span>
                    )
                  )}
                </div>
              ))}
            </div>
          )}
          {finding.source && (
            <p className="text-[11px] opacity-60 font-mono">source: {finding.source}</p>
          )}
        </div>
      )}
    </div>
  );
}

// ── LensRow ───────────────────────────────────────────────────────────

function LensRow({ lens, threshold }: { lens: ReviewLens; threshold: number }) {
  const [open, setOpen] = useState(false);
  const pct = lens.max > 0 ? Math.round((lens.score / lens.max) * 100) : 0;
  const thresholdPct = threshold;

  return (
    <div className="rounded-lg border border-border/60 bg-card/40">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-4 px-4 py-3 text-left transition-colors hover:bg-muted/40"
        aria-label={`Toggle lens: ${lens.lens}`}
      >
        {open
          ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
          : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        }
        <span className="flex-1 text-sm font-medium capitalize">{lens.lens}</span>
        <div className="flex items-center gap-3 shrink-0">
          {/* Score bar with threshold marker */}
          <div className="relative w-32 hidden sm:block" aria-label={`Score ${lens.score} out of ${lens.max}`}>
            <Progress value={pct} className="h-2" />
            {/* Threshold marker */}
            <div
              className="absolute top-0 w-0.5 h-2 bg-warning/80 rounded-full"
              style={{ left: `${thresholdPct}%` }}
              title={`Threshold: ${thresholdPct}%`}
              aria-hidden
            />
          </div>
          <span className="w-16 text-right font-mono text-xs text-muted-foreground">
            {lens.score}/{lens.max}
          </span>
          {lens.blocking ? (
            <Badge variant="destructive" className="text-[10px] py-0 h-4">blocking</Badge>
          ) : (
            <Badge variant="success" className="text-[10px] py-0 h-4">ok</Badge>
          )}
        </div>
      </button>

      {open && lens.findings.length > 0 && (
        <div className="border-t border-border/60 px-4 py-3 space-y-2">
          {lens.findings.map((f, i) => (
            <FindingRow key={i} finding={f} />
          ))}
        </div>
      )}
      {open && lens.findings.length === 0 && (
        <div className="border-t border-border/60 px-4 py-3">
          <p className="text-xs text-muted-foreground italic">No findings for this lens.</p>
        </div>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────

interface Props {
  review: PlanReview;
}

export function ReviewPanel({ review }: Props) {
  const aggregatePct = review.lenses.reduce((sum, l) => sum + l.max, 0) > 0
    ? Math.round(
        (review.aggregate_score / review.lenses.reduce((sum, l) => sum + l.max, 0)) * 100,
      )
    : 0;

  const blockingCount = review.lenses.reduce(
    (sum, l) => sum + l.findings.filter((f) => f.blocking).length,
    0,
  );

  return (
    <div className="flex flex-col gap-5" data-testid="review-panel">
      {/* Header: aggregate + gates */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-2">
            {review.gates_passed ? (
              <ShieldCheck className="h-5 w-5 text-success" aria-hidden />
            ) : (
              <ShieldX className="h-5 w-5 text-destructive" aria-hidden />
            )}
            <span className="text-sm font-semibold text-foreground">Review Summary</span>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <Badge
              variant={review.gates_passed ? 'success' : 'destructive'}
              data-testid="gates-badge"
            >
              {review.gates_passed ? 'Gates passed' : 'Gates failed'}
            </Badge>
            {blockingCount > 0 && (
              <Badge variant="destructive" data-testid="blocking-count-badge">
                {blockingCount} blocking
              </Badge>
            )}
            {review.code_gates_applied && (
              <Badge variant="muted">Code gates applied</Badge>
            )}
          </div>
        </div>

        {/* Aggregate score gauge */}
        <div className="flex flex-col items-end gap-1 shrink-0">
          <span className="text-2xl font-bold font-mono text-foreground">
            {review.aggregate_score}
          </span>
          <div className="relative w-24">
            <Progress
              value={aggregatePct}
              className={cn(
                'h-2',
                review.gates_passed
                  ? '[&>div]:bg-success'
                  : '[&>div]:bg-destructive',
              )}
            />
            <div
              className="absolute top-0 w-0.5 h-2 bg-warning/80 rounded-full"
              style={{ left: `${review.threshold}%` }}
              title={`Threshold: ${review.threshold}`}
              aria-hidden
            />
          </div>
          <span className="text-[11px] text-muted-foreground">
            threshold {review.threshold}
          </span>
        </div>
      </div>

      {/* Per-lens rows */}
      <div className="flex flex-col gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Lenses ({review.lenses.length})
        </h3>
        {review.lenses.map((lens, i) => (
          <LensRow key={i} lens={lens} threshold={review.threshold} />
        ))}
      </div>
    </div>
  );
}
