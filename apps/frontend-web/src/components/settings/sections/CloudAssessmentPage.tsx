/**
 * Cloud Assessment viewer (#133/#140/#152).
 *
 * A history of cloud assessments: a newest-first list of runs → click to open
 * the detail (verdict · service topology · remediation plan · full report) with
 * downloads (.md / issues .json / PDF) you can feed to a Claude Code / Antigravity
 * correction task. Backed by /api/cloud/assessments.
 */
import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  ArrowLeft,
  Cloud,
  Download,
  FileDown,
  FileText,
  Loader2,
  Plus,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
} from 'lucide-react';
import { get } from '../../../lib/api-client';
import { formatRelativeTime } from '../../../lib/utils';
import { SettingsSection } from '../SettingsSection';
import { MermaidDiagram } from './MermaidDiagram';
import { Button } from '../../ui/button';
import { CloudCheckDialog } from '../../CloudCheckDialog';

interface Summary {
  id: string;
  provider?: string;
  account?: string;
  verdict?: string;
  failed?: number;
  passed?: number;
  created?: number;
}

interface Detail {
  present: boolean;
  id?: string;
  json?: {
    provider?: string;
    account?: string;
    verdict?: string;
    failed?: number;
    passed?: number;
  };
  reportMarkdown?: string;
  diagramMermaid?: string;
  remediationMarkdown?: string;
  issuesJson?: string;
}

const MD_CLASS =
  'overflow-auto rounded-lg border border-border p-4 text-sm ' +
  '[&_h1]:mb-2 [&_h1]:mt-1 [&_h1]:text-lg [&_h1]:font-bold [&_h1]:text-foreground ' +
  '[&_h2]:mb-2 [&_h2]:mt-4 [&_h2]:text-base [&_h2]:font-semibold [&_h2]:text-foreground ' +
  '[&_h3]:mb-1 [&_h3]:mt-3 [&_h3]:text-sm [&_h3]:font-semibold [&_h3]:text-foreground ' +
  '[&_p]:mb-2 [&_p]:text-muted-foreground ' +
  '[&_ul]:mb-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ul]:text-muted-foreground ' +
  '[&_blockquote]:mb-2 [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-xs [&_blockquote]:text-muted-foreground ' +
  '[&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-xs ' +
  '[&_a]:text-info [&_a]:underline ' +
  '[&_table]:my-3 [&_table]:w-full [&_table]:border-collapse [&_table]:text-xs ' +
  '[&_thead]:bg-muted/50 ' +
  '[&_th]:border [&_th]:border-border [&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_th]:font-semibold ' +
  '[&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1 [&_td]:align-top';

function VerdictBadge({ verdict }: { verdict?: string }) {
  const v = (verdict || '').toLowerCase();
  if (v === 'reject')
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-destructive/15 px-3 py-1 text-sm font-semibold text-destructive">
        <ShieldAlert className="h-4 w-4" /> REJECT
      </span>
    );
  if (v === 'flag')
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-warning/15 px-3 py-1 text-sm font-semibold text-warning">
        <ShieldQuestion className="h-4 w-4" /> FLAG
      </span>
    );
  if (v === 'accept')
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-success/15 px-3 py-1 text-sm font-semibold text-success">
        <ShieldCheck className="h-4 w-4" /> ACCEPT
      </span>
    );
  return null;
}

async function downloadArtifact(id: string, kind: string, filename: string) {
  let token: string | null = null;
  try {
    token = localStorage.getItem('pfactory-token');
  } catch {
    token = null;
  }
  const res = await fetch(`/api/cloud/assessments/${encodeURIComponent(id)}/download/${kind}`, {
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

const _MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// Humanised "2d ago" relative label + a short absolute date ("May 31"),
// matching the home list. `created` is a unix timestamp in seconds.
function relTime(t?: number): string {
  return t ? formatRelativeTime(new Date(t * 1000)) : '';
}
function shortDate(t?: number): string {
  if (!t) return '';
  const d = new Date(t * 1000);
  return Number.isNaN(d.getTime()) ? '' : `${_MONTHS[d.getMonth()]} ${d.getDate()}`;
}

export function CloudAssessmentPage() {
  const [list, setList] = useState<Summary[] | null>(null);
  const [selected, setSelected] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [showNewCheck, setShowNewCheck] = useState(false);

  const loadList = async () => {
    const res = await get<{ assessments: Summary[] }>('/cloud/assessments');
    setList(res.success && res.data ? res.data.assessments : []);
    setLoading(false);
  };

  useEffect(() => {
    void loadList();
  }, []);

  const openDetail = async (id: string) => {
    setLoadingDetail(true);
    setSelected({ present: true, id });
    const res = await get<Detail>(`/cloud/assessments/${encodeURIComponent(id)}`);
    setSelected(res.success && res.data ? res.data : null);
    setLoadingDetail(false);
  };

  return (
    <SettingsSection
      title="Cloud Reports"
      description="Reports from cloud infrastructure checks — each AWS/Azure/GCP run's verdict, findings, topology, and downloadable remediation plan. Start a check with the New check button (also available from +Task → Cloud Infrastructure)."
    >
      {!selected && (
        <div className="mb-3 flex justify-end">
          <Button size="sm" onClick={() => setShowNewCheck(true)}>
            <Plus className="mr-1.5 h-4 w-4" /> New check
          </Button>
        </div>
      )}
      {loading ? (
        <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading assessments…
        </div>
      ) : selected ? (
        <DetailView detail={selected} onBack={() => setSelected(null)} loading={loadingDetail} />
      ) : (
        <ListView list={list || []} onOpen={openDetail} onNewCheck={() => setShowNewCheck(true)} />
      )}
      <CloudCheckDialog
        open={showNewCheck}
        onOpenChange={setShowNewCheck}
        onLaunched={() => {
          // Refresh shortly after — the assessment runs in the background.
          window.setTimeout(() => void loadList(), 1500);
        }}
      />
    </SettingsSection>
  );
}

function ListView({
  list,
  onOpen,
  onNewCheck,
}: {
  list: Summary[];
  onOpen: (id: string) => void;
  onNewCheck: () => void;
}) {
  if (!list.length) {
    return (
      <div className="rounded-lg border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
        No cloud reports yet. Click{' '}
        <button onClick={onNewCheck} className="font-medium text-foreground underline">
          New check
        </button>{' '}
        to assess an AWS/Azure/GCP account — each run shows here as a card.
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1.5">
      <p className="px-1 text-xs text-muted-foreground">
        {list.length} assessment{list.length === 1 ? '' : 's'} · newest first
      </p>
      {list.map((a) => {
        const verdictColor =
          (a.verdict || '').toLowerCase() === 'reject'
            ? 'bg-destructive'
            : (a.verdict || '').toLowerCase() === 'flag'
              ? 'bg-warning'
              : 'bg-success';
        return (
          <button
            type="button"
            key={a.id}
            onClick={() => onOpen(a.id)}
            className="group relative flex items-center gap-4 overflow-hidden rounded-lg border border-border/60 bg-card/40 px-4 py-3 text-left transition-all duration-150 hover:border-border hover:bg-muted/40 hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span
              className={`absolute inset-y-0 left-0 w-[3px] ${verdictColor} opacity-0 transition-opacity duration-150 group-hover:opacity-100`}
              aria-hidden
            />
            <Cloud className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="truncate text-sm font-semibold text-foreground transition-colors group-hover:text-primary">
                {a.provider?.toUpperCase()}
                <span className="ml-1.5 font-mono text-xs font-normal text-muted-foreground">
                  {a.account}
                </span>
              </span>
              <span className="mt-0.5 flex items-center gap-2 font-mono text-[11px] text-muted-foreground">
                <span className="text-destructive">{a.failed ?? 0} fail</span>
                <span className="text-success">{a.passed ?? 0} pass</span>
              </span>
            </span>
            <span className="shrink-0 text-right leading-tight">
              <span className="block text-xs font-medium tabular-nums text-foreground/80">
                {relTime(a.created) || '—'}
              </span>
              <span className="block text-[11px] tabular-nums text-muted-foreground">
                {shortDate(a.created)}
              </span>
            </span>
            <VerdictBadge verdict={a.verdict} />
          </button>
        );
      })}
    </div>
  );
}

function DetailView({ detail, onBack, loading }: { detail: Detail; onBack: () => void; loading: boolean }) {
  const id = detail.id || '';
  const acct = detail.json?.account || 'acct';
  return (
    <div className="space-y-5">
      <button
        onClick={onBack}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" /> All assessments
      </button>

      <div className="flex flex-wrap items-center gap-3">
        <VerdictBadge verdict={detail.json?.verdict} />
        <span className="text-sm text-muted-foreground">
          {detail.json?.provider?.toUpperCase()} · account {acct} · 🔴 {detail.json?.failed ?? 0} fail · ✅{' '}
          {detail.json?.passed ?? 0} pass
        </span>
      </div>

      {/* Downloads — feed these to a Claude Code / Antigravity correction task */}
      <div className="flex flex-wrap gap-2">
        <DownloadBtn
          icon={<FileText className="h-3.5 w-3.5" />}
          label="Remediation .md"
          onClick={() => downloadArtifact(id, 'remediation.md', `cloud-remediation-${acct}.md`)}
        />
        <DownloadBtn
          icon={<FileDown className="h-3.5 w-3.5" />}
          label="Remediation .pdf"
          onClick={() => downloadArtifact(id, 'remediation.pdf', `cloud-remediation-${acct}.pdf`)}
        />
        <DownloadBtn
          icon={<Download className="h-3.5 w-3.5" />}
          label="Issues .json"
          onClick={() => downloadArtifact(id, 'issues.json', `cloud-issues-${acct}.json`)}
        />
        <DownloadBtn
          icon={<FileText className="h-3.5 w-3.5" />}
          label="Report .md"
          onClick={() => downloadArtifact(id, 'report.md', `cloud-report-${acct}.md`)}
        />
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading…
        </div>
      ) : (
        <>
          {detail.diagramMermaid && (
            <div>
              <h4 className="mb-2 text-sm font-semibold text-foreground">Service topology</h4>
              <MermaidDiagram source={detail.diagramMermaid} />
            </div>
          )}
          {detail.remediationMarkdown && (
            <div>
              <h4 className="mb-2 text-sm font-semibold text-foreground">Remediation plan — how to fix</h4>
              <div className={MD_CLASS}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{detail.remediationMarkdown}</ReactMarkdown>
              </div>
            </div>
          )}
          {detail.reportMarkdown && (
            <div>
              <h4 className="mb-2 text-sm font-semibold text-foreground">Full report</h4>
              <div className={MD_CLASS}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{detail.reportMarkdown}</ReactMarkdown>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function DownloadBtn({
  icon,
  label,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium text-foreground hover:bg-muted/50"
    >
      {icon}
      {label}
    </button>
  );
}
