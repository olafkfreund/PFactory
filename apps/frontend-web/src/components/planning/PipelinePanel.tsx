/**
 * PipelinePanel — shows the processed plan summary, epic tree, and artifacts.
 *
 * Renders after process() succeeds (session.status === 'processed' | 'approved' | etc.)
 * Shows:
 *  - target_kind + plan_type badges
 *  - EpicPlan children grouped by kind (feature / testing / cicd)
 *  - Synthesized artifacts in Tabs with markdown docs in Dialog
 */

import { useState } from 'react';
import { ChevronDown, ChevronRight, FileText, Cpu, TestTube, Wrench } from 'lucide-react';
import { Badge } from '../ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../ui/tabs';
import { Button } from '../ui/button';
import { MarkdownBody } from '../ui/MarkdownBody';
import { cn } from '../../lib/utils';
import type { PlanSession, EpicChild, PlanArtifact } from '../../shared/types/plan';

// ── Kind icons + labels ───────────────────────────────────────────────

const KIND_META: Record<string, { label: string; icon: React.ElementType; color: string }> = {
  feature: { label: 'Feature', icon: Cpu, color: 'bg-primary/10 text-primary' },
  testing: { label: 'Testing', icon: TestTube, color: 'bg-success/10 text-success' },
  cicd: { label: 'CI/CD', icon: Wrench, color: 'bg-info/10 text-info' },
};

function kindMeta(kind: string) {
  return KIND_META[kind] ?? { label: kind, icon: FileText, color: 'bg-muted text-muted-foreground' };
}

// ── EpicChildRow ──────────────────────────────────────────────────────

function EpicChildRow({ child }: { child: EpicChild }) {
  const [open, setOpen] = useState(false);
  const { label, icon: Icon, color } = kindMeta(child.kind);

  return (
    <div className="rounded-lg border border-border/60 bg-card/40">
      <button
        type="button"
        aria-expanded={open}
        aria-label={`Toggle details for ${child.title}`}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/40"
      >
        {open
          ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
          : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        }
        <span className={cn('flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium', color)}>
          <Icon className="h-3 w-3" aria-hidden />
          {label}
        </span>
        <span className="flex-1 truncate text-sm font-medium text-foreground">{child.title}</span>
        <span className="font-mono text-xs text-muted-foreground">{child.key}</span>
        {child.complexity && (
          <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
            {String(child.complexity)}
          </span>
        )}
      </button>

      {open && (
        <div className="border-t border-border/60 px-4 py-3 text-sm text-foreground/80 space-y-3">
          {child.body && (
            <MarkdownBody source={child.body} />
          )}
          {child.labels.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {child.labels.map((l) => (
                <Badge key={l} variant="muted" className="text-xs">{l}</Badge>
              ))}
            </div>
          )}
          {child.acceptance_criteria.length > 0 && (
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">
                Acceptance criteria
              </p>
              <ul className="space-y-1">
                {child.acceptance_criteria.map((ac, i) => (
                  <li key={i} className="flex gap-2 text-xs text-foreground/80">
                    <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary/60 mt-1" aria-hidden />
                    {ac}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {child.depends_on.length > 0 && (
            <p className="text-xs text-muted-foreground">
              Depends on: {child.depends_on.join(', ')}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Artifact dialog ───────────────────────────────────────────────────

function ArtifactCard({ artifact }: { artifact: PlanArtifact }) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={`View artifact: ${artifact.title}`}
        className="flex items-start gap-3 rounded-lg border border-border/60 bg-card/40 px-4 py-3 text-left transition-colors hover:border-border hover:bg-muted/40 w-full"
      >
        <FileText className="h-4 w-4 shrink-0 text-muted-foreground mt-0.5" aria-hidden />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-foreground truncate">{artifact.title}</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            <span className="font-mono">{artifact.filename}</span>
            {artifact.child && (
              <span className="ml-2 text-muted-foreground/70">· {artifact.child}</span>
            )}
          </p>
        </div>
        <Badge variant={artifact.kind === 'cicd' ? 'info' : 'success'} className="shrink-0">
          {artifact.kind}
        </Badge>
      </button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-3xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{artifact.title}</DialogTitle>
          </DialogHeader>
          <div className="mt-2">
            <MarkdownBody source={artifact.document} />
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

// ── Main component ────────────────────────────────────────────────────

interface Props {
  session: PlanSession;
}

export function PipelinePanel({ session }: Props) {
  const { plan, epic } = session;

  // Group epic children by kind
  const childrenByKind = (epic?.children ?? []).reduce<Record<string, EpicChild[]>>(
    (acc, c) => {
      const k = c.kind || 'feature';
      if (!acc[k]) acc[k] = [];
      acc[k].push(c);
      return acc;
    },
    {},
  );

  const kindOrder = ['feature', 'testing', 'cicd'];
  const sortedKinds = [
    ...kindOrder.filter((k) => k in childrenByKind),
    ...Object.keys(childrenByKind).filter((k) => !kindOrder.includes(k)),
  ];

  const testingArtifacts = session.artifacts.filter((a) => a.kind === 'testing');
  const cicdArtifacts = session.artifacts.filter((a) => a.kind === 'cicd');

  return (
    <div className="flex flex-col gap-6" data-testid="pipeline-panel">
      {/* Plan summary */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">{plan.title}</CardTitle>
        </CardHeader>
        <CardContent className="pt-0 space-y-3">
          <div className="flex flex-wrap gap-2">
            <Badge variant={plan.target_kind === 'software' ? 'info' : plan.target_kind === 'non-software' ? 'warning' : 'muted'}>
              {plan.target_kind}
            </Badge>
            <Badge variant="secondary">{plan.plan_type}</Badge>
            {plan.source_format && (
              <Badge variant="outline" className="font-mono text-xs">{plan.source_format}</Badge>
            )}
          </div>
          {plan.description && (
            <p className="text-sm text-foreground/80 leading-relaxed">{plan.description}</p>
          )}
          {plan.criteria.length > 0 && (
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
                Criteria ({plan.criteria.length})
              </p>
              <ul className="space-y-1">
                {plan.criteria.slice(0, 5).map((c) => (
                  <li key={c.id} className="flex gap-2 text-xs text-foreground/80">
                    <span className="font-mono text-muted-foreground">{c.id}</span>
                    <span>{c.text}</span>
                  </li>
                ))}
                {plan.criteria.length > 5 && (
                  <li className="text-xs text-muted-foreground">
                    + {plan.criteria.length - 5} more…
                  </li>
                )}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Epic children */}
      {epic && (
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-foreground">{epic.epic_title}</h3>
            <Badge variant="secondary">{epic.children.length} items</Badge>
          </div>
          {epic.summary && (
            <p className="text-sm text-muted-foreground">{epic.summary}</p>
          )}

          <Tabs defaultValue={sortedKinds[0] ?? 'feature'}>
            <TabsList>
              {sortedKinds.map((kind) => {
                const meta = kindMeta(kind);
                const Icon = meta.icon;
                return (
                  <TabsTrigger key={kind} value={kind}>
                    <Icon className="h-3.5 w-3.5 mr-1.5" aria-hidden />
                    {meta.label}
                    <span className="ml-1.5 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-mono">
                      {childrenByKind[kind].length}
                    </span>
                  </TabsTrigger>
                );
              })}
            </TabsList>
            {sortedKinds.map((kind) => (
              <TabsContent key={kind} value={kind} className="mt-3">
                <div className="flex flex-col gap-2">
                  {childrenByKind[kind].map((child) => (
                    <EpicChildRow key={child.key} child={child} />
                  ))}
                </div>
              </TabsContent>
            ))}
          </Tabs>
        </div>
      )}

      {/* Artifacts */}
      {session.artifacts.length > 0 && (
        <div className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold text-foreground">
            Synthesized artifacts ({session.artifacts.length})
          </h3>
          <Tabs defaultValue={testingArtifacts.length > 0 ? 'testing' : 'cicd'}>
            <TabsList>
              {testingArtifacts.length > 0 && (
                <TabsTrigger value="testing">
                  <TestTube className="h-3.5 w-3.5 mr-1.5" aria-hidden />
                  Testing ({testingArtifacts.length})
                </TabsTrigger>
              )}
              {cicdArtifacts.length > 0 && (
                <TabsTrigger value="cicd">
                  <Wrench className="h-3.5 w-3.5 mr-1.5" aria-hidden />
                  CI/CD ({cicdArtifacts.length})
                </TabsTrigger>
              )}
            </TabsList>
            <TabsContent value="testing" className="mt-3">
              <div className="flex flex-col gap-2">
                {testingArtifacts.map((a, i) => (
                  <ArtifactCard key={i} artifact={a} />
                ))}
              </div>
            </TabsContent>
            <TabsContent value="cicd" className="mt-3">
              <div className="flex flex-col gap-2">
                {cicdArtifacts.map((a, i) => (
                  <ArtifactCard key={i} artifact={a} />
                ))}
              </div>
            </TabsContent>
          </Tabs>
        </div>
      )}
    </div>
  );
}
