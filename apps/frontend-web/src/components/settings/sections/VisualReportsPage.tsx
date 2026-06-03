/**
 * Visual Reports page (#170 / P4) — browse visual inspection runs.
 *
 * A history list (newest-first cards) → detail (verdict · report · correction
 * plan · downloads), mirroring the Cloud Reports page. Backed by
 * GET /api/visual-inspections.
 */
import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  ArrowLeft,
  Camera,
  Download,
  FileText,
  Loader2,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
} from 'lucide-react';
import { get } from '../../../lib/api-client';
import { formatRelativeTime } from '../../../lib/utils';
import { SettingsSection } from '../SettingsSection';

interface Summary {
  id: string;
  target?: { name?: string; platform?: string; base_url?: string };
  verdict?: string;
  counts?: { steps?: number; passed?: number; failed?: number };
  created?: number;
}
interface Detail {
  present?: boolean;
  id?: string;
  meta?: { verdict?: string; counts?: Summary['counts']; target?: Summary['target'] };
  reportMarkdown?: string;
  correctionPlanMarkdown?: string;
}

const MD_CLASS =
  'prose prose-sm prose-invert max-w-none [&_table]:block [&_table]:overflow-x-auto ' +
  '[&_th]:border [&_th]:border-border [&_th]:px-2 [&_th]:py-1 [&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1 ' +
  '[&_img]:max-h-64 [&_img]:rounded [&_img]:border [&_img]:border-border';

function VerdictBadge({ verdict }: { verdict?: string }) {
  const v = (verdict || '').toLowerCase();
  if (v === 'fail')
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-destructive/15 px-3 py-1 text-sm font-semibold text-destructive">
        <ShieldAlert className="h-4 w-4" /> FAIL
      </span>
    );
  if (v === 'attention')
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-warning/15 px-3 py-1 text-sm font-semibold text-warning">
        <ShieldQuestion className="h-4 w-4" /> ATTENTION
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-success/15 px-3 py-1 text-sm font-semibold text-success">
      <ShieldCheck className="h-4 w-4" /> PASS
    </span>
  );
}

async function downloadArtifact(id: string, kind: string, filename: string) {
  let token: string | null = null;
  try {
    token = localStorage.getItem('pfactory-token');
  } catch {
    token = null;
  }
  const res = await fetch(`/api/visual-inspections/${encodeURIComponent(id)}/download/${kind}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) return;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function VisualReportsPage() {
  const [list, setList] = useState<Summary[] | null>(null);
  const [selected, setSelected] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const res = await get<{ runs: Summary[] }>('/visual-inspections');
      if (!cancelled) {
        setList(res.success && res.data ? res.data.runs : []);
        setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const openDetail = async (id: string) => {
    setLoadingDetail(true);
    setSelected({ present: true, id });
    const res = await get<Detail>(`/visual-inspections/${encodeURIComponent(id)}`);
    setSelected(res.success && res.data ? res.data : null);
    setLoadingDetail(false);
  };

  return (
    <SettingsSection
      title="Visual Reports"
      description="Recorded browser inspection runs — each run's verdict, per-step screenshots, the human report, and a downloadable correction plan. Runs are produced by a Visual Inspection task and (optionally) committed to the project repo under automated-test/."
    >
      {loading ? (
        <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading runs…
        </div>
      ) : selected ? (
        <DetailView detail={selected} onBack={() => setSelected(null)} loading={loadingDetail} />
      ) : (
        <ListView list={list || []} onOpen={openDetail} />
      )}
    </SettingsSection>
  );
}

function ListView({ list, onOpen }: { list: Summary[]; onOpen: (id: string) => void }) {
  if (!list.length) {
    return (
      <div className="rounded-lg border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
        No visual inspection runs yet. Start one from <strong>+Task → Visual Inspection</strong> — each
        run shows here as a card.
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1.5">
      <p className="px-1 text-xs text-muted-foreground">
        {list.length} run{list.length === 1 ? '' : 's'} · newest first
      </p>
      {list.map((r) => {
        const v = (r.verdict || '').toLowerCase();
        const accent = v === 'fail' ? 'bg-destructive' : v === 'attention' ? 'bg-warning' : 'bg-success';
        const c = r.counts || {};
        return (
          <button
            type="button"
            key={r.id}
            onClick={() => onOpen(r.id)}
            className="group relative flex items-center gap-4 overflow-hidden rounded-lg border border-border/60 bg-card/40 px-4 py-3 text-left transition-all duration-150 hover:border-border hover:bg-muted/40 hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span className={`absolute inset-y-0 left-0 w-[3px] ${accent} opacity-0 transition-opacity group-hover:opacity-100`} aria-hidden />
            <Camera className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="truncate text-sm font-semibold text-foreground transition-colors group-hover:text-primary">
                {r.target?.name || r.id}
                {r.target?.platform && (
                  <span className="ml-1.5 font-mono text-xs font-normal text-muted-foreground">{r.target.platform}</span>
                )}
              </span>
              <span className="mt-0.5 flex items-center gap-2 font-mono text-[11px] text-muted-foreground">
                <span className="text-success">{c.passed ?? 0} pass</span>
                <span className="text-destructive">{c.failed ?? 0} fail</span>
              </span>
            </span>
            <span className="shrink-0 text-right text-xs font-medium tabular-nums text-foreground/80">
              {formatRelativeTime(r.created ? new Date(r.created * 1000) : null) || '—'}
            </span>
            <VerdictBadge verdict={r.verdict} />
          </button>
        );
      })}
    </div>
  );
}

function DetailView({ detail, onBack, loading }: { detail: Detail; onBack: () => void; loading: boolean }) {
  const id = detail.id || '';
  return (
    <div className="space-y-4">
      <button onClick={onBack} className="inline-flex items-center gap-1 text-sm text-primary hover:underline">
        <ArrowLeft className="h-4 w-4" /> All runs
      </button>
      {loading ? (
        <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading…
        </div>
      ) : !detail.present ? (
        <p className="text-sm text-muted-foreground">Run not found.</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <VerdictBadge verdict={detail.meta?.verdict} />
            <div className="flex flex-wrap gap-2">
              <DownloadBtn id={id} kind="report.md" label="Report (.md)" />
              <DownloadBtn id={id} kind="correction-plan.md" label="Correction plan (.md)" />
              <DownloadBtn id={id} kind="report.pdf" label="Report (.pdf)" />
              <DownloadBtn id={id} kind="issues.json" label="Issues (.json)" />
            </div>
          </div>
          {detail.reportMarkdown && (
            <section>
              <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-foreground">
                <FileText className="h-4 w-4" /> Inspection report
              </h3>
              <div className={MD_CLASS}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{detail.reportMarkdown}</ReactMarkdown>
              </div>
            </section>
          )}
          {detail.correctionPlanMarkdown && (
            <section>
              <h3 className="mb-2 text-sm font-semibold text-foreground">Correction plan</h3>
              <div className={MD_CLASS}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{detail.correctionPlanMarkdown}</ReactMarkdown>
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}

function DownloadBtn({ id, kind, label }: { id: string; kind: string; label: string }) {
  return (
    <button
      onClick={() => downloadArtifact(id, kind, `visual-${id}-${kind}`)}
      className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card/40 px-2.5 py-1 text-xs text-foreground transition-colors hover:border-primary/40 hover:bg-muted/50"
    >
      <Download className="h-3.5 w-3.5" /> {label}
    </button>
  );
}
