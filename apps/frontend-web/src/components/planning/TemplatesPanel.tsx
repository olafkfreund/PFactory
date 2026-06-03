/**
 * TemplatesPanel — lists /api/plan/meta/templates with their policy.
 */

import { useEffect, useState } from 'react';
import { Loader2, AlertTriangle, LayoutTemplate, ChevronDown, ChevronRight } from 'lucide-react';
import { Badge } from '../ui/badge';
import { getTemplates, type FetchOptions } from '../../lib/planning-api';
import type { TemplateEntry } from '../../shared/types/plan';

interface Props {
  fetchFn?: typeof fetch;
}

function TemplateRow({ template }: { template: TemplateEntry }) {
  const [open, setOpen] = useState(false);
  const policyEntries = Object.entries(template.policy);

  return (
    <div className="rounded-lg border border-border/60 bg-card/40">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/40"
        aria-label={`Toggle template: ${template.title}`}
      >
        {open
          ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
          : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        }
        <span className="flex-1 text-sm font-medium text-foreground">{template.title}</span>
        <span className="font-mono text-xs text-muted-foreground">{template.name}</span>
      </button>

      {open && (
        <div className="border-t border-border/60 px-4 py-3 space-y-3">
          {template.tags.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {template.tags.map((tag) => (
                <Badge key={tag} variant="muted" className="text-[10px]">{tag}</Badge>
              ))}
            </div>
          )}
          {policyEntries.length > 0 && (
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
                Policy
              </p>
              <dl className="space-y-1">
                {policyEntries.map(([key, val]) => (
                  <div key={key} className="flex gap-2 text-xs">
                    <dt className="font-mono text-muted-foreground shrink-0">{key}:</dt>
                    <dd className="text-foreground/80 break-all">{JSON.stringify(val)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function TemplatesPanel({ fetchFn }: Props) {
  const [templates, setTemplates] = useState<TemplateEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const opts: FetchOptions = { fetchFn };
    setLoading(true);
    getTemplates(opts)
      .then((r) => {
        if (!cancelled) { setTemplates(r.templates); setLoading(false); }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [fetchFn]);

  if (loading) {
    return (
      <div role="status" className="flex items-center gap-2 p-6 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        <span>Loading templates…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div role="alert" className="flex items-center gap-2 p-6 text-destructive">
        <AlertTriangle className="h-4 w-4" aria-hidden />
        <span>{error}</span>
      </div>
    );
  }

  if (templates.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 p-12 text-muted-foreground">
        <LayoutTemplate className="h-8 w-8" aria-hidden />
        <p>No templates found.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2" data-testid="templates-panel">
      {templates.map((t) => (
        <TemplateRow key={t.name} template={t} />
      ))}
    </div>
  );
}
