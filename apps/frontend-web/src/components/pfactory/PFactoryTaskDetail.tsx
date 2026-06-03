/**
 * Per-task detail view — the operator's review surface.
 *
 * Tab-bar over the artefact surfaces that matter for the merge/dismiss
 * decision:
 *   - Status     (a labelled lifecycle panel — NOT a raw JSON dump)
 *   - Lanes      (the LaneStatusGrid, lit by lane_progress)
 *   - Verdicts   (per-test cards, bucketed accept/flag/reject, 5 signals)
 *   - Report     (the rendered triage_report.md, via MarkdownBody)
 *   - Logs / Evidence
 *
 * Tabs whose artefact is absent are disabled. All colour comes from the
 * Gruvbox theme tokens (bg-card, text-foreground, success/warning/destructive/
 * info) so it renders correctly in both light and dark mode.
 */

import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { AlertTriangle, Loader2 } from 'lucide-react';

import { formatRelativeTime } from '../../lib/utils';
import { MarkdownBody } from '../ui/MarkdownBody';
import {
  PFactoryApiError,
  dismissRun,
  evidenceArtifactUrl,
  getTaskDetail,
  getTriageReportMarkdown,
  getVerdicts,
  mergeAcceptedTests,
  type EvidenceUrls,
  type MergeResult,
  type PFactoryTaskDetail as TaskDetail,
  type PFactoryVerdict,
  type PFactoryVerdictsDocument,
} from '../../lib/pfactory-api';
import { LaneStatusGrid } from './LaneStatusGrid';
import { PFactoryLogViewer } from './PFactoryLogViewer';

type Tab = 'status' | 'lanes' | 'verdicts' | 'report' | 'logs' | 'evidence';

interface Props {
  specId: string;
  fetchFn?: typeof fetch;
  wsFactory?: (url: string) => WebSocket;
  /**
   * Background auto-refresh interval in ms so live status/lane changes
   * (e.g. a watchdog `stalled` flip, #95) appear without a manual reload.
   * Set to 0 to disable. Default 5000.
   */
  pollMs?: number;
}

// ── Status severity → theme token ───────────────────────────────────

type Severity = 'success' | 'warning' | 'destructive' | 'info' | 'muted';

export function statusSeverity(status: string | null): Severity {
  if (!status) return 'muted';
  const s = status.toLowerCase();
  if (s.includes('failed') || s.includes('stuck') || s.includes('stalled') || s.includes('error'))
    return 'destructive';
  if (s.endsWith('_empty')) return 'muted';
  if (s.includes('triaged') || s.includes('generated') || s.includes('accept')) return 'success';
  if (s.includes('replan') || s.includes('flag') || s.includes('warn')) return 'warning';
  if (
    s.includes('planning') || s.includes('generating') || s.includes('evaluating') ||
    s.includes('running') || s.includes('pending') || s.includes('in_flight') || s.includes('started')
  ) return 'info';
  return 'muted';
}

const SEV_PILL: Record<Severity, string> = {
  success: 'bg-success/15 text-success border-success/30',
  warning: 'bg-warning/15 text-warning border-warning/30',
  destructive: 'bg-destructive/15 text-destructive border-destructive/30',
  info: 'bg-info/15 text-info border-info/30',
  muted: 'bg-muted text-muted-foreground border-border',
};

function StatusPill({ value }: { value: string | null }) {
  const sev = statusSeverity(value);
  return (
    <span className={`inline-flex items-center rounded-md border px-2 py-0.5 font-mono text-xs ${SEV_PILL[sev]}`}>
      {value ?? 'unknown'}
    </span>
  );
}

// ── Status panel (replaces the raw <pre>) ───────────────────────────

function StatusPanel({ statusJson }: { statusJson: Record<string, unknown> }) {
  const status = (statusJson.status as string | undefined) ?? null;
  const phase = (statusJson.phase as string | undefined) ?? null;
  const lane = (statusJson.lane_progress as Record<string, string | null> | undefined) ?? {};
  const subtaskCount = statusJson.subtask_count as number | undefined;
  const testsGenerated = statusJson.tests_generated as number | undefined;
  const created = statusJson.created_at as string | undefined;
  const updated = statusJson.updated_at as string | undefined;
  const warnings = (statusJson.planner_warnings as string[] | undefined) ?? [];
  const evaluatorError = statusJson.evaluator_error as string | undefined;

  return (
    <div data-testid="status-panel" className="space-y-4">
      {/* Lifecycle strip */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card p-3">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">Status</span>
        <StatusPill value={status} />
        {phase && (
          <>
            <span className="text-muted-foreground">·</span>
            <span className="font-mono text-xs text-muted-foreground">{phase}</span>
          </>
        )}
      </div>

      {/* Hard error callout */}
      {evaluatorError && (
        <div role="alert" className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <div>
            <p className="font-medium">Evaluator error</p>
            <p className="font-mono text-xs opacity-90">{evaluatorError}</p>
          </div>
        </div>
      )}

      {/* Lane progress chips */}
      {Object.keys(lane).length > 0 && (
        <div>
          <p className="mb-1.5 text-xs uppercase tracking-wide text-muted-foreground">Lanes</p>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(lane).map(([name, st]) => (
              <span
                key={name}
                className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs ${SEV_PILL[statusSeverity(st)]}`}
              >
                <span className="font-medium capitalize">{name}</span>
                <span className="font-mono opacity-80">{st ?? '—'}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Counts */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Subtasks" value={subtaskCount} />
        <Stat label="Tests generated" value={testsGenerated} />
        <Stat label="Created" value={fmt(created)} mono />
        <Stat label="Updated" value={fmt(updated)} mono />
      </div>

      {warnings.length > 0 && (
        <div className="rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
          <p className="font-medium">Planner warnings</p>
          <ul className="ml-4 list-disc">
            {warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, mono }: { label: string; value: unknown; mono?: boolean }) {
  return (
    <div className="rounded-lg border border-border bg-card p-2.5">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className={`text-sm text-foreground ${mono ? 'font-mono text-xs' : 'font-semibold'}`}>
        {value === undefined || value === null || value === '' ? '—' : String(value)}
      </p>
    </div>
  );
}

function fmt(iso?: string): string {
  if (!iso) return '';
  // Humanised relative time ("2d ago"), consistent with the home + cloud lists.
  return formatRelativeTime(iso) || iso;
}

// ── Tab button ──────────────────────────────────────────────────────

interface TabButtonProps {
  tab: Tab;
  active: Tab;
  disabled?: boolean;
  onClick: (tab: Tab) => void;
  label: string;
  badge?: number;
}

function TabButton({ tab, active, disabled, onClick, label, badge }: TabButtonProps) {
  const isActive = active === tab;
  return (
    <button
      type="button"
      role="tab"
      aria-selected={isActive}
      aria-controls={`tab-panel-${tab}`}
      disabled={disabled}
      onClick={() => onClick(tab)}
      data-testid={`tab-${tab}`}
      data-active={isActive ? 'true' : 'false'}
      className={[
        'inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium border-b-2 transition-colors',
        isActive
          ? 'border-primary text-foreground'
          : 'border-transparent text-muted-foreground hover:text-foreground',
        disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer',
      ].join(' ')}
    >
      {label}
      {typeof badge === 'number' && badge > 0 && (
        <span className="rounded-full bg-muted px-1.5 text-[10px] text-muted-foreground">{badge}</span>
      )}
    </button>
  );
}

// ── Verdict signal encodings ────────────────────────────────────────

const VERDICT_STYLE: Record<string, string> = {
  accept: 'bg-success/15 text-success border-success/30',
  reject: 'bg-destructive/15 text-destructive border-destructive/30',
  flag: 'bg-warning/15 text-warning border-warning/30',
};

function VerdictBadge({ verdict }: { verdict: string }) {
  return (
    <span
      data-testid={`verdict-badge-${verdict}`}
      className={`inline-block rounded-full border px-2 py-0.5 text-xs font-medium ${VERDICT_STYLE[verdict] ?? SEV_PILL.muted}`}
    >
      {verdict}
    </span>
  );
}

function MutationChip({ value }: { value?: string }) {
  if (!value) return <Dash />;
  const killed = value.toLowerCase().includes('kill');
  const survived = value.toLowerCase().includes('surviv');
  const cls = killed ? SEV_PILL.success : survived ? SEV_PILL.destructive : SEV_PILL.muted;
  const label = killed ? 'KILLED' : survived ? 'SURVIVED' : value;
  return <span className={`inline-block rounded border px-1.5 py-0.5 font-mono text-[10px] ${cls}`}>{label}</span>;
}

function StabilityChip({ value }: { value?: string }) {
  if (!value) return <Dash />;
  const v = value.toLowerCase();
  const cls = v.includes('consistent_pass') || v === 'stable'
    ? SEV_PILL.success
    : v.includes('fail') ? SEV_PILL.destructive : SEV_PILL.warning;
  return <span className={`inline-block rounded border px-1.5 py-0.5 font-mono text-[10px] ${cls}`}>{value}</span>;
}

function SemanticPill({ value }: { value?: 'high' | 'medium' | 'low' }) {
  if (!value) return <Dash />;
  const cls = value === 'high' ? SEV_PILL.success : value === 'low' ? SEV_PILL.destructive : SEV_PILL.warning;
  return <span className={`inline-block rounded-full border px-1.5 py-0.5 text-[10px] ${cls}`}>{value}</span>;
}

function LintFlag({ value }: { value?: string }) {
  if (!value || value === 'no_findings') return <span className="text-muted-foreground text-xs">clean</span>;
  return <span className={`inline-block rounded border px-1.5 py-0.5 font-mono text-[10px] ${SEV_PILL.warning}`}>{value}</span>;
}

function CoverageBar({ pct }: { pct?: number }) {
  if (pct === undefined || pct === null) return <Dash />;
  const clamped = Math.max(0, Math.min(100, pct));
  return (
    <div className="flex items-center gap-1.5">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
        <div className="h-full rounded-full bg-success" style={{ width: `${clamped}%` }} />
      </div>
      <span className="font-mono text-[10px] text-muted-foreground">{pct}%</span>
    </div>
  );
}

function Dash() {
  return <span className="text-muted-foreground">—</span>;
}

function VerdictCard({ v }: { v: PFactoryVerdict }) {
  const s = v.signals_summary ?? {};
  return (
    <div
      data-testid={`verdict-row-${v.test_id}`}
      className="rounded-lg border border-border bg-card p-3 space-y-2"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-xs text-foreground break-all">{v.test_id}</span>
        <VerdictBadge verdict={v.verdict} />
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs">
        <Signal label="cov"><CoverageBar pct={s.coverage_delta_pct} /></Signal>
        <Signal label="stability"><StabilityChip value={s.stability} /></Signal>
        <Signal label="mutation"><MutationChip value={s.mutation} /></Signal>
        <Signal label="lint"><LintFlag value={s.lint_promotion} /></Signal>
        <Signal label="semantic"><SemanticPill value={v.semantic_relevance} /></Signal>
      </div>
      {v.reasons?.length > 0 && (
        <ul className="ml-4 list-disc text-xs text-muted-foreground">
          {v.reasons.slice(0, 3).map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}
    </div>
  );
}

function Signal({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</span>
      {children}
    </span>
  );
}

// ── Merge / dismiss action bar (the human review gate) ──────────────

function MergeActionBar({ specId, verdicts, fetchFn }: {
  specId: string;
  verdicts: PFactoryVerdict[];
  fetchFn?: typeof fetch;
}) {
  const acceptCount = verdicts.filter((v) => v.verdict === 'accept').length;
  const flagCount = verdicts.filter((v) => v.verdict === 'flag').length;
  const [dryRun, setDryRun] = useState(true);
  const [branch, setBranch] = useState('');
  const [includeFlagged, setIncludeFlagged] = useState(false);
  const [busy, setBusy] = useState<'merge' | 'dismiss' | null>(null);
  const [result, setResult] = useState<MergeResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);

  const onMerge = async () => {
    setBusy('merge'); setError(null); setResult(null);
    try {
      const r = await mergeAcceptedTests(specId, {
        dry_run: dryRun,
        target_branch: branch.trim() || undefined,
        include_flagged: includeFlagged,
      }, { fetchFn });
      setResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(null); }
  };
  const onDismiss = async () => {
    setBusy('dismiss'); setError(null);
    try { await dismissRun(specId, { fetchFn }); setDismissed(true); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  };

  return (
    <div data-testid="merge-action-bar" className="rounded-lg border border-border bg-card p-3 space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          data-testid="merge-btn"
          disabled={acceptCount === 0 || busy !== null || dismissed}
          onClick={onMerge}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-40 hover:bg-primary/90"
        >
          {busy === 'merge' && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
          {dryRun ? 'Preview merge' : 'Merge'} {acceptCount} accepted{includeFlagged && flagCount ? ` + ${flagCount} flagged` : ''}
        </button>
        <button
          type="button"
          data-testid="dismiss-btn"
          disabled={busy !== null || dismissed}
          onClick={onDismiss}
          className="rounded-md border border-border px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
        >
          Dismiss run
        </button>
        <label className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
          <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} data-testid="dry-run-toggle" />
          Dry run
        </label>
        {flagCount > 0 && (
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <input type="checkbox" checked={includeFlagged} onChange={(e) => setIncludeFlagged(e.target.checked)} />
            include flagged
          </label>
        )}
      </div>
      <input
        type="text"
        value={branch}
        onChange={(e) => setBranch(e.target.value)}
        placeholder="target branch (defaults to the handover branch)"
        className="w-full rounded-md border border-border bg-background px-2 py-1 font-mono text-xs text-foreground"
        data-testid="target-branch"
      />
      {acceptCount === 0 && (
        <p className="text-xs text-muted-foreground">No accepted tests — nothing to merge. Dismiss the run or re-evaluate.</p>
      )}
      {dismissed && <p className="text-sm text-warning">Run dismissed.</p>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {result && (
        <div className="space-y-1.5 rounded-md border border-border bg-muted/40 p-2 text-xs">
          <p className={result.ok ? 'text-success' : 'text-destructive'}>
            {result.dry_run ? 'Dry run' : 'Committed'} · {result.ok ? 'ok' : `failed: ${result.error}`}
            {result.commit_sha && <> · <span className="font-mono">{result.commit_sha.slice(0, 10)}</span></>}
          </p>
          <p className="text-muted-foreground">{result.files.length} file(s) → <span className="font-mono">{result.branch}</span></p>
          <ul className="ml-4 list-disc font-mono text-muted-foreground">
            {result.files.slice(0, 8).map((f) => <li key={f}>{f}</li>)}
          </ul>
          {result.dry_run && result.argv.length > 0 && (
            <details>
              <summary className="cursor-pointer text-muted-foreground">git commands (preview)</summary>
              <pre className="mt-1 overflow-auto whitespace-pre-wrap font-mono text-[10px] text-muted-foreground">
                {result.argv.map((a) => a.join(' ')).join('\n')}
              </pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

function VerdictBuckets({ verdicts }: { verdicts: PFactoryVerdict[] }) {
  if (verdicts.length === 0) {
    return <p className="p-4 text-sm text-muted-foreground">No verdicts yet.</p>;
  }
  // Lead with flag (the human-attention queue), then accept, then reject.
  const order: Array<PFactoryVerdict['verdict']> = ['flag', 'accept', 'reject'];
  const groups = order
    .map((k) => ({ k, items: verdicts.filter((v) => v.verdict === k) }))
    .filter((g) => g.items.length > 0);
  return (
    <div data-testid="verdict-table" className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {order.map((k) => {
          const n = verdicts.filter((v) => v.verdict === k).length;
          return (
            <span key={k} className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs ${VERDICT_STYLE[k]}`}>
              <span className="font-semibold">{n}</span>
              <span className="capitalize">{k}</span>
            </span>
          );
        })}
      </div>
      {groups.map((g) => (
        <section key={g.k}>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground capitalize">
            {g.k} · {g.items.length}
          </h4>
          <div className="space-y-2">
            {g.items.map((v) => <VerdictCard key={v.test_id} v={v} />)}
          </div>
        </section>
      ))}
    </div>
  );
}

// ── Evidence panel ──────────────────────────────────────────────────

function EvidenceTestRow({ specId, testId, urls }: { specId: string; testId: string; urls: EvidenceUrls }) {
  const screenshots: string[] = Array.isArray(urls.screenshots)
    ? urls.screenshots : typeof urls.screenshots === 'string' ? [urls.screenshots] : [];
  const videoUrl = typeof urls.video === 'string' ? urls.video : null;
  const traceUrl = typeof urls.trace === 'string' ? urls.trace : null;
  const networkUrl = typeof urls.network === 'string' ? urls.network : null;
  const buildUrl = (p: string) => (p.startsWith('/api/') ? p : evidenceArtifactUrl(specId, testId, p));

  return (
    <div data-testid={`evidence-row-${testId}`} className="rounded-lg border border-border bg-card p-3 space-y-2">
      <h4 className="font-mono text-xs font-semibold text-foreground">{testId}</h4>
      {screenshots.length > 0 && (
        <div data-testid="evidence-screenshots" className="flex flex-wrap gap-2">
          {screenshots.map((url) => {
            const full = buildUrl(url);
            const name = url.split('/').pop() ?? 'screenshot';
            return (
              <a key={full} href={full} target="_blank" rel="noopener noreferrer">
                <img src={full} alt={`Screenshot ${name}`} title={name}
                  className="h-24 w-auto rounded border border-border object-cover"
                  data-testid={`evidence-screenshot-img-${name}`} />
              </a>
            );
          })}
        </div>
      )}
      {videoUrl && (
        <div data-testid="evidence-video">
          {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
          <video src={buildUrl(videoUrl)} controls className="max-h-48 w-full rounded" data-testid="evidence-video-player" />
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {traceUrl && (
          <a href={buildUrl(traceUrl)} download data-testid="evidence-trace-download"
            className="rounded bg-muted px-2 py-1 text-xs text-foreground hover:bg-muted/70">Download trace.zip</a>
        )}
        {networkUrl && (
          <a href={buildUrl(networkUrl)} download data-testid="evidence-har-download"
            className="rounded bg-muted px-2 py-1 text-xs text-foreground hover:bg-muted/70">Download network.har</a>
        )}
      </div>
    </div>
  );
}

function EvidenceTab({ specId, evidenceByTest }: { specId: string; evidenceByTest: Record<string, EvidenceUrls> }) {
  const entries = Object.entries(evidenceByTest);
  if (entries.length === 0) {
    return (
      <p data-testid="evidence-empty" className="p-4 text-sm text-muted-foreground">
        No evidence captured yet — evidence is collected after tests run.
      </p>
    );
  }
  return (
    <div data-testid="evidence-panel" className="space-y-3 p-1">
      {entries.map(([testId, urls]) => (
        <EvidenceTestRow key={testId} specId={specId} testId={testId} urls={urls} />
      ))}
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────

export function PFactoryTaskDetail({ specId, fetchFn, wsFactory, pollMs = 5000 }: Props) {
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(true);
  const [activeTab, setActiveTab] = useState<Tab>('status');

  const [verdicts, setVerdicts] = useState<PFactoryVerdictsDocument | null>(null);
  const [verdictsError, setVerdictsError] = useState<string | null>(null);
  const [loadingVerdicts, setLoadingVerdicts] = useState(false);
  const [verdictsRequested, setVerdictsRequested] = useState(false);

  const [reportMd, setReportMd] = useState<string | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);
  const [loadingReport, setLoadingReport] = useState(false);
  const [reportRequested, setReportRequested] = useState(false);

  const [evidenceByTest, setEvidenceByTest] = useState<Record<string, EvidenceUrls>>({});

  useEffect(() => {
    let cancelled = false;
    setLoadingDetail(true);
    setDetailError(null);
    getTaskDetail(specId, { fetchFn })
      .then((d) => { if (!cancelled) { setDetail(d); setLoadingDetail(false); } })
      .catch((err: unknown) => {
        if (cancelled) return;
        setDetailError(err instanceof Error ? err.message : String(err));
        setLoadingDetail(false);
      });
    return () => { cancelled = true; };
  }, [specId, fetchFn]);

  // Background auto-refresh: re-fetch the detail on an interval so live
  // status/lane changes (e.g. a watchdog `stalled` flip, #95) surface without
  // a manual reload. Updates only on success — a transient poll error keeps
  // the last-good detail rather than flipping to the error state.
  useEffect(() => {
    if (!pollMs || pollMs <= 0) return;
    const id = setInterval(() => {
      getTaskDetail(specId, { fetchFn })
        .then((d) => setDetail(d))
        .catch(() => {
          /* keep last-good detail on a transient poll error */
        });
    }, pollMs);
    return () => clearInterval(id);
  }, [pollMs, specId, fetchFn]);

  const _extractEvidenceFromVerdicts = useCallback((doc: PFactoryVerdictsDocument) => {
    const byTest: Record<string, EvidenceUrls> = {};
    for (const v of doc.verdicts) {
      if (v.evidence_urls && Object.keys(v.evidence_urls).length > 0) byTest[v.test_id] = v.evidence_urls;
    }
    setEvidenceByTest(byTest);
  }, []);

  const onSelectTab = useCallback((tab: Tab) => {
    setActiveTab(tab);
    if (tab === 'verdicts' && !verdictsRequested) {
      setVerdictsRequested(true);
      setLoadingVerdicts(true);
      setVerdictsError(null);
      getVerdicts(specId, { fetchFn })
        .then((d) => { setVerdicts(d); setLoadingVerdicts(false); _extractEvidenceFromVerdicts(d); })
        .catch((err: unknown) => {
          setVerdictsError(err instanceof PFactoryApiError && err.status === 404
            ? 'No verdicts yet — task hasn\'t reached the Evaluator.'
            : err instanceof Error ? err.message : String(err));
          setLoadingVerdicts(false);
        });
    }
    if (tab === 'report' && !reportRequested) {
      setReportRequested(true);
      setLoadingReport(true);
      setReportError(null);
      getTriageReportMarkdown(specId, { fetchFn })
        .then((md) => { setReportMd(md); setLoadingReport(false); })
        .catch((err: unknown) => {
          setReportError(err instanceof PFactoryApiError && err.status === 404
            ? 'No report yet — task hasn\'t reached the Triager.'
            : err instanceof Error ? err.message : String(err));
          setLoadingReport(false);
        });
    }
    if (tab === 'evidence' && verdicts && Object.keys(evidenceByTest).length === 0) {
      _extractEvidenceFromVerdicts(verdicts);
    }
  }, [specId, fetchFn, verdictsRequested, reportRequested, verdicts, evidenceByTest, _extractEvidenceFromVerdicts]);

  if (loadingDetail) {
    return (
      <div role="status" className="flex items-center gap-2 p-6 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        <span>Loading task…</span>
      </div>
    );
  }
  if (detailError) {
    return (
      <div role="alert" className="flex items-center gap-2 p-6 text-destructive">
        <AlertTriangle className="h-4 w-4" aria-hidden />
        <span>{detailError}</span>
      </div>
    );
  }
  if (!detail) {
    return <div role="alert" className="p-6 text-destructive">No detail returned.</div>;
  }

  const status = (detail.status_json.status as string | undefined) ?? null;
  const verdictsAvailable = detail.artefacts.verdicts?.exists ?? false;
  const reportAvailable = detail.artefacts.triage_report_md?.exists ?? false;
  const evidenceAvailable = verdictsAvailable;

  const laneProgress = detail.status_json.lane_progress as Record<string, string | null> | undefined;
  const laneStatuses = laneProgress ?? { unit: status };
  const verdictCount = verdicts?.verdicts.length;

  return (
    <div data-testid="pfactory-task-detail" className="flex flex-col gap-4">
      <header className="flex items-baseline gap-2 border-b border-border pb-3">
        <h2 className="text-lg font-semibold text-foreground">{detail.spec_id}</h2>
        <span className="text-xs text-muted-foreground">project {detail.project_id}</span>
        <span className="ml-auto"><StatusPill value={status} /></span>
      </header>

      <div role="tablist" className="flex flex-wrap border-b border-border">
        <TabButton tab="status" active={activeTab} onClick={onSelectTab} label="Status" />
        <TabButton tab="lanes" active={activeTab} onClick={onSelectTab} label="Lanes" />
        <TabButton tab="verdicts" active={activeTab} onClick={onSelectTab} label="Verdicts" disabled={!verdictsAvailable} badge={verdictCount} />
        <TabButton tab="report" active={activeTab} onClick={onSelectTab} label="Report" disabled={!reportAvailable} />
        <TabButton tab="logs" active={activeTab} onClick={onSelectTab} label="Logs" />
        <TabButton tab="evidence" active={activeTab} onClick={onSelectTab} label="Evidence" disabled={!evidenceAvailable} />
      </div>

      <div role="tabpanel" id={`tab-panel-${activeTab}`} data-testid={`panel-${activeTab}`}>
        {activeTab === 'status' && (
          <StatusPanel statusJson={detail.status_json as Record<string, unknown>} />
        )}
        {activeTab === 'lanes' && <LaneStatusGrid laneStatuses={laneStatuses} />}
        {activeTab === 'verdicts' && (
          <>
            {loadingVerdicts && <div role="status" className="p-4 text-sm text-muted-foreground">Loading verdicts…</div>}
            {verdictsError && <div role="alert" className="p-4 text-sm text-destructive">{verdictsError}</div>}
            {verdicts && (
              <div className="space-y-4">
                <MergeActionBar specId={specId} verdicts={verdicts.verdicts} fetchFn={fetchFn} />
                <VerdictBuckets verdicts={verdicts.verdicts} />
              </div>
            )}
          </>
        )}
        {activeTab === 'report' && (
          <>
            {loadingReport && <div role="status" className="p-4 text-sm text-muted-foreground">Loading report…</div>}
            {reportError && <div role="alert" className="p-4 text-sm text-destructive">{reportError}</div>}
            {reportMd && (
              <div data-testid="report-md-content" className="rounded-lg border border-border bg-card p-4">
                <MarkdownBody source={reportMd} />
              </div>
            )}
          </>
        )}
        {activeTab === 'logs' && <PFactoryLogViewer specId={specId} wsFactory={wsFactory} />}
        {activeTab === 'evidence' && <EvidenceTab specId={specId} evidenceByTest={evidenceByTest} />}
      </div>
    </div>
  );
}
