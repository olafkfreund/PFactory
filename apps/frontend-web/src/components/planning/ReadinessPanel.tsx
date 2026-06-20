/**
 * ReadinessPanel — surfaces the readiness gate results for a plan session
 * and lets users waive hard check failures (when the check is waivable).
 *
 * Props:
 *   session   — the current PlanSession (must have review.readiness)
 *   onUpdated — called with the refreshed session after a successful waive
 */

import { useState } from 'react';
import {
  CheckCircle2,
  XCircle,
  MinusCircle,
  SkipForward,
  ShieldAlert,
  ShieldCheck,
  ChevronDown,
  ChevronRight,
  User,
  Clock,
  Loader2,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Textarea } from '../ui/textarea';
import { cn } from '../../lib/utils';
import { waiveChecks } from '../../lib/planning-api';
import type { PlanSession, ReadinessCheck, ReadinessWaiver } from '../../shared/types/plan';

// ── Helpers ───────────────────────────────────────────────────────────────

function isCheckWaived(check: ReadinessCheck, waivers: ReadinessWaiver[]): boolean {
  return waivers.some(
    (w) => w.valid && w.check_ids.includes(check.check_id),
  );
}

// ── Status icon/colour ────────────────────────────────────────────────────

const STATUS_META: Record<
  string,
  { icon: React.ElementType; iconClass: string; badgeVariant: 'success' | 'destructive' | 'warning' | 'muted' }
> = {
  pass: {
    icon: CheckCircle2,
    iconClass: 'text-success',
    badgeVariant: 'success',
  },
  fail: {
    icon: XCircle,
    iconClass: 'text-destructive',
    badgeVariant: 'destructive',
  },
  not_applicable: {
    icon: MinusCircle,
    iconClass: 'text-muted-foreground',
    badgeVariant: 'muted',
  },
  skipped: {
    icon: SkipForward,
    iconClass: 'text-muted-foreground',
    badgeVariant: 'muted',
  },
};

function statusMeta(status: string) {
  return STATUS_META[status] ?? STATUS_META.not_applicable;
}

// ── WaiverForm ────────────────────────────────────────────────────────────

interface WaiverFormProps {
  checkId: string;
  sessionId: string;
  onSuccess: (updated: PlanSession) => void;
  onCancel: () => void;
}

function WaiverForm({ checkId, sessionId, onSuccess, onCancel }: WaiverFormProps) {
  const { t } = useTranslation('common');
  const [reason, setReason] = useState('');
  const [waivedBy, setWaivedBy] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    if (!reason.trim()) {
      setError(t('readiness.waive.reasonRequired'));
      return;
    }
    const effectiveWaivedBy = waivedBy.trim() || 'portal-user';
    setError(null);
    setSubmitting(true);
    try {
      const updated = await waiveChecks(sessionId, {
        check_ids: [checkId],
        reason: reason.trim(),
        waived_by: effectiveWaivedBy,
      });
      onSuccess(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  };

  return (
    <div className="mt-2 ml-6 rounded-lg border border-warning/30 bg-warning/5 p-3 space-y-2">
      <p className="text-xs font-semibold text-warning">{t('readiness.waive.formTitle')}</p>

      <div className="space-y-1">
        <label className="text-xs font-medium text-foreground" htmlFor={`waived-by-${checkId}`}>
          {t('readiness.waive.waivedByLabel')}
          <span className="ml-1 text-muted-foreground font-normal">({t('readiness.waive.waivedByOptional')})</span>
        </label>
        <Input
          id={`waived-by-${checkId}`}
          placeholder={t('readiness.waive.waivedByPlaceholder')}
          value={waivedBy}
          onChange={(e) => setWaivedBy(e.target.value)}
          className="h-8 text-xs"
          aria-label={t('readiness.waive.waivedByLabel')}
        />
      </div>

      <div className="space-y-1">
        <label className="text-xs font-medium text-foreground" htmlFor={`waive-reason-${checkId}`}>
          {t('readiness.waive.reasonLabel')}
          <span className="ml-1 text-destructive">*</span>
        </label>
        <Textarea
          id={`waive-reason-${checkId}`}
          placeholder={t('readiness.waive.reasonPlaceholder')}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={2}
          className="text-xs resize-none"
          aria-label={t('readiness.waive.reasonLabel')}
          data-testid={`waive-reason-input-${checkId}`}
        />
      </div>

      {error && (
        <p role="alert" className="text-xs text-destructive">{error}</p>
      )}

      <div className="flex gap-2">
        <Button
          size="sm"
          variant="warning"
          onClick={handleSubmit}
          disabled={submitting || !reason.trim()}
          aria-label={t('readiness.waive.confirmLabel')}
          data-testid={`waive-confirm-${checkId}`}
        >
          {submitting
            ? <Loader2 className="mr-1.5 h-3 w-3 animate-spin" aria-hidden />
            : null
          }
          {submitting ? t('readiness.waive.submitting') : t('readiness.waive.confirmButton')}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={onCancel}
          disabled={submitting}
          aria-label={t('buttons.cancel')}
        >
          {t('buttons.cancel')}
        </Button>
      </div>
    </div>
  );
}

// ── CheckRow ──────────────────────────────────────────────────────────────

interface CheckRowProps {
  check: ReadinessCheck;
  waivers: ReadinessWaiver[];
  sessionId: string;
  onWaived: (updated: PlanSession) => void;
}

function CheckRow({ check, waivers, sessionId, onWaived }: CheckRowProps) {
  const { t } = useTranslation('common');
  const [open, setOpen] = useState(false);
  const [showWaiveForm, setShowWaiveForm] = useState(false);

  const meta = statusMeta(check.status);
  const StatusIcon = meta.icon;
  const alreadyWaived = isCheckWaived(check, waivers);

  // A check is waivable from the UI if: hard fail, waivable flag, not already waived.
  const canWaive = check.hard && check.status === 'fail' && check.waivable && !alreadyWaived;

  const activeWaiver = waivers.find(
    (w) => w.valid && w.check_ids.includes(check.check_id),
  );

  // Override status icon colour when waived
  const iconClass = alreadyWaived
    ? 'text-warning'
    : check.hard && check.status === 'fail'
      ? 'text-destructive'
      : meta.iconClass;

  return (
    <div
      className={cn(
        'rounded-lg border px-3 py-2.5',
        check.status === 'pass'
          ? 'border-success/20 bg-success/5'
          : check.status === 'fail' && check.hard && !alreadyWaived
            ? 'border-destructive/30 bg-destructive/5 ring-1 ring-destructive/20'
            : check.status === 'fail' && alreadyWaived
              ? 'border-warning/20 bg-warning/5'
              : 'border-border/60 bg-card/40',
      )}
      data-testid={`readiness-check-${check.check_id}`}
    >
      {/* Row header */}
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start gap-2 text-left"
        aria-label={`${t('readiness.check.toggle')} ${check.title}`}
      >
        <StatusIcon
          className={cn('h-3.5 w-3.5 shrink-0 mt-0.5', iconClass)}
          aria-hidden
        />
        <span className="flex-1 text-xs font-medium leading-snug">{check.title}</span>

        <div className="flex items-center gap-1.5 shrink-0">
          {check.hard && (
            <Badge
              variant={alreadyWaived ? 'warning' : 'destructive'}
              className="text-[10px] py-0 h-4"
            >
              {alreadyWaived ? t('readiness.badge.waived') : t('readiness.badge.hard')}
            </Badge>
          )}
          {check.status !== 'pass' && check.status !== 'not_applicable' && !check.hard && (
            <Badge variant="warning" className="text-[10px] py-0 h-4">
              {t('readiness.badge.advisory')}
            </Badge>
          )}
          <Badge variant={meta.badgeVariant} className="text-[10px] py-0 h-4">
            {t(`readiness.status.${check.status}`, check.status)}
          </Badge>
          {open
            ? <ChevronDown className="h-3.5 w-3.5 opacity-50" aria-hidden />
            : <ChevronRight className="h-3.5 w-3.5 opacity-50" aria-hidden />
          }
        </div>
      </button>

      {/* Expanded content */}
      {open && (
        <div className="mt-2 ml-5 space-y-2">
          {check.detail && (
            <p className="text-xs leading-relaxed text-muted-foreground">{check.detail}</p>
          )}
          {check.remediation && (
            <div className="rounded-md bg-background/60 border border-border/40 px-2.5 py-1.5">
              <p className="text-[11px] font-medium text-foreground mb-0.5">{t('readiness.check.remediation')}</p>
              <p className="text-[11px] text-muted-foreground leading-relaxed">{check.remediation}</p>
            </div>
          )}
          {check.evidence && Object.keys(check.evidence).length > 0 && (
            <pre
              className="text-[11px] font-mono text-muted-foreground/70 leading-relaxed whitespace-pre-wrap break-all rounded-md bg-background/60 border border-border/40 px-2.5 py-1.5"
              data-testid={`readiness-evidence-${check.check_id}`}
            >
              {JSON.stringify(check.evidence, null, 2)}
            </pre>
          )}
          {check.severity && (
            <p className="text-[11px] text-muted-foreground">
              {t('readiness.check.severity')}: <span className="font-medium capitalize">{check.severity}</span>
            </p>
          )}

          {/* Existing valid waiver */}
          {activeWaiver && (
            <div className="rounded-md border border-warning/20 bg-warning/5 px-2.5 py-1.5 space-y-0.5">
              <p className="text-[11px] font-semibold text-warning">{t('readiness.waiver.title')}</p>
              <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <User className="h-3 w-3" aria-hidden />
                <span>{activeWaiver.waived_by}</span>
                <Clock className="h-3 w-3 ml-1" aria-hidden />
                <span>{new Date(activeWaiver.waived_at).toLocaleString()}</span>
              </div>
              <p className="text-[11px] text-muted-foreground leading-snug">{activeWaiver.reason}</p>
            </div>
          )}

          {/* Waive button / form */}
          {canWaive && !showWaiveForm && (
            <Button
              size="sm"
              variant="outline"
              className="text-xs h-7 border-warning/40 text-warning hover:bg-warning/10"
              onClick={() => setShowWaiveForm(true)}
              aria-label={`${t('readiness.waive.buttonLabel')} ${check.title}`}
              data-testid={`waive-btn-${check.check_id}`}
            >
              <ShieldAlert className="mr-1.5 h-3 w-3" aria-hidden />
              {t('readiness.waive.buttonLabel')}
            </Button>
          )}

          {canWaive && showWaiveForm && (
            <WaiverForm
              checkId={check.check_id}
              sessionId={sessionId}
              onSuccess={(updated) => {
                setShowWaiveForm(false);
                onWaived(updated);
              }}
              onCancel={() => setShowWaiveForm(false)}
            />
          )}
        </div>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────

interface Props {
  session: PlanSession;
  onUpdated: (updated: PlanSession) => void;
}

export function ReadinessPanel({ session, onUpdated }: Props) {
  const { t } = useTranslation('common');
  const readiness = session.review?.readiness ?? null;

  // Not yet evaluated
  if (readiness === null) {
    return (
      <p className="text-sm text-muted-foreground italic" data-testid="readiness-not-evaluated">
        {t('readiness.notEvaluated')}
      </p>
    );
  }

  const checks = readiness.results;
  const waivers = readiness.waivers;

  const hardFailCount = checks.filter(
    (c) => c.hard && c.status === 'fail' && !isCheckWaived(c, waivers),
  ).length;

  const passCount = checks.filter((c) => c.status === 'pass').length;
  const totalHard = checks.filter((c) => c.hard).length;
  const waivedCount = checks.filter((c) => isCheckWaived(c, waivers)).length;

  const allClear = hardFailCount === 0;

  return (
    <div className="flex flex-col gap-5" data-testid="readiness-panel">
      {/* Summary header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-2">
          {allClear ? (
            <ShieldCheck className="h-5 w-5 text-success mt-0.5 shrink-0" aria-hidden />
          ) : (
            <ShieldAlert className="h-5 w-5 text-destructive mt-0.5 shrink-0" aria-hidden />
          )}
          <div>
            <p className="text-sm font-semibold text-foreground">
              {allClear ? t('readiness.summary.allClear') : t('readiness.summary.hardFailures')}
            </p>
            <div className="mt-1 flex flex-wrap gap-1.5">
              <Badge variant={allClear ? 'success' : 'destructive'} data-testid="readiness-gate-badge">
                {allClear ? t('readiness.summary.gatePassed') : t('readiness.summary.gateFailed')}
              </Badge>
              {hardFailCount > 0 && (
                <Badge variant="destructive">
                  {t('readiness.summary.hardFailCount', { count: hardFailCount })}
                </Badge>
              )}
              {waivedCount > 0 && (
                <Badge variant="warning">
                  {t('readiness.summary.waivedCount', { count: waivedCount })}
                </Badge>
              )}
              <Badge variant="muted">
                {t('readiness.summary.passCount', { pass: passCount, total: checks.length })}
              </Badge>
              {totalHard > 0 && (
                <Badge variant="muted">
                  {t('readiness.summary.hardCount', { count: totalHard })}
                </Badge>
              )}
            </div>
          </div>
        </div>

        <div className="text-right shrink-0">
          <p className="text-[11px] font-mono text-muted-foreground">
            {new Date(readiness.generated_at).toLocaleString()}
          </p>
        </div>
      </div>

      {/* Check list */}
      <div className="flex flex-col gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t('readiness.checks.heading', { count: checks.length })}
        </h3>
        {checks.map((check) => (
          <CheckRow
            key={check.check_id}
            check={check}
            waivers={waivers}
            sessionId={session.session_id}
            onWaived={onUpdated}
          />
        ))}
        {checks.length === 0 && (
          <p className="text-xs text-muted-foreground italic">{t('readiness.checks.empty')}</p>
        )}
      </div>

      {/* Waivers summary (existing waivers not already shown inline) */}
      {waivers.length > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t('readiness.waivers.heading', { count: waivers.length })}
          </h3>
          {waivers.map((w, i) => (
            <div
              key={i}
              className={cn(
                'rounded-lg border px-3 py-2 text-xs space-y-0.5',
                w.valid
                  ? 'border-warning/20 bg-warning/5'
                  : 'border-border/60 bg-muted/30',
              )}
              data-testid={`waiver-${i}`}
            >
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-medium text-foreground">
                  {t('readiness.waivers.checks', { ids: w.check_ids.join(', ') })}
                </span>
                <Badge
                  variant={w.valid ? 'warning' : 'muted'}
                  className="text-[10px] py-0 h-4"
                >
                  {w.valid ? t('readiness.badge.waived') : t('readiness.waivers.invalidated')}
                </Badge>
              </div>
              <div className="flex items-center gap-2 text-muted-foreground">
                <User className="h-3 w-3" aria-hidden />
                <span>{w.waived_by}</span>
                <Clock className="h-3 w-3 ml-1" aria-hidden />
                <span>{new Date(w.waived_at).toLocaleString()}</span>
              </div>
              <p className="text-muted-foreground leading-snug">{w.reason}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
