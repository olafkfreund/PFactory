/**
 * EnrichmentPanel — "AI Activity / Context" panel for the planning portal.
 *
 * Tells the story: "the AI probed your infrastructure, read your wiki/docs,
 * and pulled best practices." Rendered as a tab inside SessionDetail when
 * enrichment data is present.
 *
 * Three sections (each shown only when they have content):
 *   1. Infrastructure probe   — per-adapter cards with resources, findings, exposures
 *   2. Best practices         — knowledge refs with kind === "best-practice"
 *   3. Wiki & docs            — remaining knowledge refs (doc/policy/adr/template/catalog)
 *
 * All field access is defensive: every array/object is coerced via ?? [] / ?? {}.
 * Objects are never rendered directly as React children.
 */

import { Server, Globe, BookOpen, ShieldAlert, CheckCircle2, XCircle } from 'lucide-react';
import { Badge } from '../ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { cn } from '../../lib/utils';
import type { PlanSession, InfraSnapshot, KnowledgeRef, InfraPolicy } from '../../shared/types/plan';

// ─── helpers ────────────────────────────────────────────────────────────────

/** Safely coerce a value to string for display — never render raw objects. */
function safeStr(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return '[value]';
  }
}

// ─── InfraCard ───────────────────────────────────────────────────────────────

interface InfraCardProps {
  snap: InfraSnapshot;
}

function InfraCard({ snap }: InfraCardProps) {
  const adapter = safeStr(snap.adapter) || 'unknown';
  const target = safeStr(snap.target) || '—';
  const resources = (snap.resources ?? {}) as Record<string, unknown>;
  const findings = Array.isArray(snap.findings) ? snap.findings : [];
  const policies = Array.isArray(snap.policies) ? snap.policies : [];
  const publicPolicies = policies.filter(
    (p): p is InfraPolicy => typeof p === 'object' && p !== null && (p as InfraPolicy).public === true,
  );

  return (
    <Card className="overflow-hidden" data-testid="infra-card">
      {/* Header row */}
      <CardHeader className="pb-3 px-4 pt-4">
        <div className="flex items-center gap-2">
          <Server className="h-4 w-4 text-muted-foreground shrink-0" aria-hidden />
          <CardTitle className="text-sm font-semibold flex-1 truncate capitalize">
            {adapter}
            {target !== '—' && (
              <span className="ml-1.5 text-xs font-mono text-muted-foreground">
                · {target}
              </span>
            )}
          </CardTitle>
          {/* Availability dot */}
          {snap.available ? (
            <span
              className="flex items-center gap-1 text-xs text-success"
              aria-label="Adapter available"
              data-testid="infra-available"
            >
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
              available
            </span>
          ) : (
            <span
              className="flex items-center gap-1 text-xs text-muted-foreground"
              aria-label="Adapter unavailable"
              data-testid="infra-unavailable"
            >
              <XCircle className="h-3.5 w-3.5" aria-hidden />
              unavailable
            </span>
          )}
        </div>
      </CardHeader>

      <CardContent className="px-4 pb-4 pt-0 space-y-4">
        {/* Error banner */}
        {snap.error && (
          <p className="text-xs text-muted-foreground italic" data-testid="infra-error">
            {safeStr(snap.error)}
          </p>
        )}

        {/* Resources grid */}
        {Object.keys(resources).length > 0 && (
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground mb-2">
              Resources
            </p>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3" data-testid="infra-resources">
              {Object.entries(resources).map(([key, val]) => (
                <div key={key} className="flex flex-col gap-0.5">
                  <dt className="text-[10px] font-mono text-muted-foreground/80 truncate">{key}</dt>
                  <dd className="text-xs font-semibold text-foreground truncate">{safeStr(val)}</dd>
                </div>
              ))}
            </dl>
          </div>
        )}

        {/* Findings list */}
        {findings.length > 0 && (
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">
              Findings
            </p>
            <ul className="space-y-1" data-testid="infra-findings">
              {findings.map((f, i) => (
                <li key={i} className="flex gap-2 text-xs text-foreground/80">
                  <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary/60 mt-1" aria-hidden />
                  {safeStr(f)}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Public SG exposures */}
        {publicPolicies.length > 0 && (
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-destructive mb-1.5 flex items-center gap-1">
              <ShieldAlert className="h-3 w-3" aria-hidden />
              Exposed security groups ({publicPolicies.length})
            </p>
            <ul className="space-y-1.5" data-testid="infra-exposures">
              {publicPolicies.map((pol, i) => {
                const cidrs = Array.isArray(pol.open_cidrs) ? pol.open_cidrs.join(', ') : safeStr(pol.open_cidrs);
                const ports = Array.isArray(pol.open_ports) ? pol.open_ports.join(', ') : safeStr(pol.open_ports);
                return (
                  <li
                    key={i}
                    className="rounded-md bg-destructive/5 border border-destructive/20 px-3 py-2 text-xs text-destructive"
                    data-testid="infra-exposure-row"
                  >
                    <span className="font-semibold">
                      {safeStr(pol.group_name)}
                      {pol.group_id ? (
                        <span className="ml-1 font-mono text-[10px] opacity-70">({safeStr(pol.group_id)})</span>
                      ) : null}
                    </span>
                    {' — '}
                    open to <span className="font-mono">{cidrs || '—'}</span>
                    {ports ? (
                      <span> on <span className="font-mono">{ports}</span></span>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        {/* Empty state */}
        {!snap.error &&
          Object.keys(resources).length === 0 &&
          findings.length === 0 &&
          publicPolicies.length === 0 && (
            <p className="text-xs text-muted-foreground italic">No details gathered for this adapter.</p>
          )}
      </CardContent>
    </Card>
  );
}

// ─── KnowledgeCard ───────────────────────────────────────────────────────────

const KIND_BADGE: Record<string, { variant: 'info' | 'success' | 'warning' | 'muted' | 'purple'; label: string }> = {
  'best-practice': { variant: 'success', label: 'best-practice' },
  policy: { variant: 'warning', label: 'policy' },
  doc: { variant: 'info', label: 'doc' },
  adr: { variant: 'purple', label: 'adr' },
  template: { variant: 'muted', label: 'template' },
  catalog: { variant: 'muted', label: 'catalog' },
};

function kindBadge(kind: string) {
  return KIND_BADGE[kind] ?? { variant: 'muted' as const, label: kind };
}

interface KnowledgeCardProps {
  ref_: KnowledgeRef;
}

function KnowledgeCard({ ref_ }: KnowledgeCardProps) {
  const title = safeStr(ref_.title) || 'Untitled';
  const snippet = safeStr(ref_.snippet);
  const connector = safeStr(ref_.connector);
  const uri = safeStr(ref_.uri);
  const score = typeof ref_.score === 'number' ? ref_.score : null;
  const kb = kindBadge(ref_.kind ?? '');

  return (
    <Card className="overflow-hidden" data-testid="knowledge-card">
      <CardContent className="px-4 py-3 space-y-2">
        {/* Title row */}
        <div className="flex items-start gap-2">
          <div className="flex-1 min-w-0">
            {uri ? (
              <a
                href={uri}
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm font-medium text-foreground hover:text-primary hover:underline truncate block"
                data-testid="knowledge-link"
                title={title}
              >
                {title}
              </a>
            ) : (
              <p className="text-sm font-medium text-foreground truncate" data-testid="knowledge-title">
                {title}
              </p>
            )}
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            {connector && (
              <Badge variant="outline" className="text-[10px] font-mono py-0 h-4">
                {connector}
              </Badge>
            )}
            <Badge variant={kb.variant} className="text-[10px] py-0 h-4">
              {kb.label}
            </Badge>
            {score !== null && (
              <span
                className={cn(
                  'rounded-md px-1.5 py-0.5 text-[10px] font-mono shrink-0',
                  score >= 0.8
                    ? 'bg-success/10 text-success'
                    : score >= 0.5
                    ? 'bg-info/10 text-info'
                    : 'bg-muted text-muted-foreground',
                )}
                aria-label={`Relevance score ${score.toFixed(2)}`}
                data-testid="knowledge-score"
              >
                {score.toFixed(2)}
              </span>
            )}
          </div>
        </div>

        {/* Snippet */}
        {snippet && (
          <p className="text-xs text-foreground/70 leading-relaxed line-clamp-3" data-testid="knowledge-snippet">
            {snippet}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ─── EnrichmentPanel ─────────────────────────────────────────────────────────

interface Props {
  session: PlanSession;
}

export function EnrichmentPanel({ session }: Props) {
  const enrichment = session.plan?.enrichment;
  const infraList = Array.isArray(enrichment?.infra) ? enrichment.infra : [];
  const knowledgeList = Array.isArray(enrichment?.knowledge) ? enrichment.knowledge : [];

  const bestPractices = knowledgeList.filter(
    (k): k is KnowledgeRef =>
      typeof k === 'object' && k !== null && (k as KnowledgeRef).kind === 'best-practice',
  );
  const wikiDocs = knowledgeList.filter(
    (k): k is KnowledgeRef =>
      typeof k === 'object' && k !== null && (k as KnowledgeRef).kind !== 'best-practice',
  );

  const hasInfra = infraList.length > 0;
  const hasBestPractices = bestPractices.length > 0;
  const hasWikiDocs = wikiDocs.length > 0;

  if (!hasInfra && !hasBestPractices && !hasWikiDocs) {
    return (
      <div
        className="flex flex-col items-center justify-center py-12 text-center gap-2"
        data-testid="enrichment-empty"
      >
        <p className="text-sm text-muted-foreground">No AI context was gathered for this plan.</p>
        <p className="text-xs text-muted-foreground/60">
          Context is collected when the plan is processed.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8" data-testid="enrichment-panel">

      {/* ── 1. Infrastructure probe ─────────────────────────────────────── */}
      {hasInfra && (
        <section aria-labelledby="enrichment-infra-heading">
          <div className="flex items-center gap-2 mb-3">
            <Server className="h-4 w-4 text-muted-foreground" aria-hidden />
            <h3
              id="enrichment-infra-heading"
              className="text-sm font-semibold text-foreground"
            >
              Infrastructure probe
            </h3>
            <Badge variant="muted" className="text-[10px]">{infraList.length}</Badge>
          </div>
          <div className="flex flex-col gap-3">
            {infraList.map((snap, i) => {
              if (typeof snap !== 'object' || snap === null) return null;
              return <InfraCard key={i} snap={snap as InfraSnapshot} />;
            })}
          </div>
        </section>
      )}

      {/* ── 2. Best practices ───────────────────────────────────────────── */}
      {hasBestPractices && (
        <section aria-labelledby="enrichment-bp-heading">
          <div className="flex items-center gap-2 mb-3">
            <Globe className="h-4 w-4 text-muted-foreground" aria-hidden />
            <h3
              id="enrichment-bp-heading"
              className="text-sm font-semibold text-foreground"
            >
              Best practices
            </h3>
            <Badge variant="success" className="text-[10px]">{bestPractices.length}</Badge>
          </div>
          <div className="flex flex-col gap-2">
            {bestPractices.map((ref_, i) => (
              <KnowledgeCard key={i} ref_={ref_} />
            ))}
          </div>
        </section>
      )}

      {/* ── 3. Wiki & docs ──────────────────────────────────────────────── */}
      {hasWikiDocs && (
        <section aria-labelledby="enrichment-docs-heading">
          <div className="flex items-center gap-2 mb-3">
            <BookOpen className="h-4 w-4 text-muted-foreground" aria-hidden />
            <h3
              id="enrichment-docs-heading"
              className="text-sm font-semibold text-foreground"
            >
              Wiki &amp; docs
            </h3>
            <Badge variant="info" className="text-[10px]">{wikiDocs.length}</Badge>
          </div>
          <div className="flex flex-col gap-2">
            {wikiDocs.map((ref_, i) => (
              <KnowledgeCard key={i} ref_={ref_} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
