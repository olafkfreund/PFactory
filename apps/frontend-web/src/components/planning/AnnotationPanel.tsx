/**
 * AnnotationPanel — surfaces the Phase D "honoured document" output: the
 * cited, anchored suggested edits and the improved draft. The original is never
 * rewritten — these are suggestions the engineer accepts, rejects, or adopts.
 */

import { useState } from 'react';
import { FileText, Lightbulb, Link as LinkIcon } from 'lucide-react';
import { Badge } from '../ui/badge';
import { cn } from '../../lib/utils';
import type { PlanSession, SuggestedEdit } from '../../shared/types/plan';

const sevTone: Record<string, string> = {
  critical: 'bg-destructive/15 text-destructive',
  high: 'bg-destructive/10 text-destructive',
  medium: 'bg-amber-500/15 text-amber-600',
  low: 'bg-muted text-muted-foreground',
  info: 'bg-muted text-muted-foreground',
};

function SuggestionRow({ s }: { s: SuggestedEdit }) {
  return (
    <div className="rounded-lg border border-border bg-card/40 px-3 py-2">
      <div className="flex items-start gap-2">
        <Lightbulb className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" aria-hidden />
        <div className="flex-1 space-y-1">
          <p className="text-xs font-medium leading-snug">{s.suggestion}</p>
          <p className="text-[11px] text-muted-foreground">
            {s.anchor_line ? `line ${s.anchor_line}` : 'whole document'}
            {s.original_excerpt && <span className="opacity-80"> — “{s.original_excerpt}”</span>}
          </p>
          {s.why && <p className="text-[11px] opacity-90"><span className="font-medium">Why:</span> {s.why}</p>}
          {s.citation?.uri && (
            <a
              href={s.citation.uri}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-[11px] text-primary hover:underline"
            >
              <LinkIcon className="h-3 w-3" aria-hidden />
              {s.citation.title || s.citation.source || s.citation.uri}
            </a>
          )}
        </div>
        <Badge className={cn('shrink-0 text-[10px]', sevTone[s.severity])}>{s.severity}</Badge>
      </div>
    </div>
  );
}

export function AnnotationPanel({ session }: { session: PlanSession }) {
  const annotation = session.annotation ?? null;
  const [showDraft, setShowDraft] = useState(false);

  if (!annotation || (annotation.suggestions.length === 0 && !annotation.improved_markdown)) {
    return (
      <p className="text-sm text-muted-foreground">
        No suggested edits — the plan reads cleanly, or it hasn't been processed yet.
      </p>
    );
  }

  return (
    <div className="space-y-4" data-testid="annotation-panel">
      <div className="flex items-center gap-2">
        <FileText className="h-4 w-4 text-primary" aria-hidden />
        <h3 className="text-sm font-semibold">
          Suggested edits {session.original_filename && (
            <span className="font-normal text-muted-foreground">· {session.original_filename}</span>
          )}
        </h3>
        <span className="ml-auto text-[11px] text-muted-foreground">
          {annotation.suggestions.length} suggestion(s) · original preserved
        </span>
      </div>

      <div className="space-y-2">
        {annotation.suggestions.map((s, i) => <SuggestionRow key={i} s={s} />)}
      </div>

      {annotation.improved_markdown && (
        <div>
          <button
            type="button"
            onClick={() => setShowDraft((v) => !v)}
            className="text-xs font-medium text-primary hover:underline"
          >
            {showDraft ? 'Hide' : 'Show'} improved draft
          </button>
          {showDraft && (
            <pre className="mt-2 max-h-[420px] overflow-auto rounded-lg border border-border bg-muted/40 p-3 text-[11px] leading-relaxed whitespace-pre-wrap">
              {annotation.improved_markdown}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
