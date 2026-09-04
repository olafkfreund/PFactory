/**
 * AnnotationPanel — surfaces the Phase D "honoured document" output: the
 * cited, anchored suggested edits and the improved draft. The original is never
 * rewritten — these are suggestions the engineer accepts, rejects, or adopts.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FileText, Lightbulb, Link as LinkIcon, RefreshCw } from 'lucide-react';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Textarea } from '../ui/textarea';
import { cn } from '../../lib/utils';
import { usePlanStore } from '../../stores/plan-store';
import type { PlanSession, SuggestedEdit } from '../../shared/types/plan';

const sevTone: Record<string, string> = {
  critical: 'bg-destructive/15 text-destructive',
  high: 'bg-destructive/10 text-destructive',
  medium: 'bg-amber-500/15 text-amber-600',
  low: 'bg-muted text-muted-foreground',
  info: 'bg-muted text-muted-foreground',
};

function SuggestionRow({
  s,
  checked,
  draft,
  onToggle,
  onDraftChange,
}: {
  s: SuggestedEdit;
  checked: boolean;
  draft: string;
  onToggle: (checked: boolean) => void;
  onDraftChange: (text: string) => void;
}) {
  const { t } = useTranslation('common');
  // A suggestion with no drafted text cannot be accepted mechanically. Say so
  // and disable it rather than offering a button that would apply nothing.
  const applicable = s.mode !== 'manual';

  return (
    <div className="rounded-lg border border-border bg-card/40 px-3 py-2">
      <div className="flex items-start gap-2">
        {applicable ? (
          <input
            type="checkbox"
            checked={checked}
            onChange={(e) => { onToggle(e.target.checked); }}
            aria-label={t('annotations.acceptAria', { suggestion: s.suggestion })}
            data-testid={`accept-${s.id}`}
            className="mt-1 h-3.5 w-3.5 shrink-0 accent-primary"
          />
        ) : (
          <Lightbulb className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" aria-hidden />
        )}
        <div className="flex-1 space-y-1">
          <p className="text-xs font-medium leading-snug">{s.suggestion}</p>
          <p className="text-[11px] text-muted-foreground">
            {s.anchor_line
              ? t('annotations.line', { line: s.anchor_line })
              : t('annotations.wholeDocument')}
            {s.original_excerpt && <span className="opacity-80"> — “{s.original_excerpt}”</span>}
          </p>
          {s.why && <p className="text-[11px] opacity-90"><span className="font-medium">{t('annotations.why')}</span> {s.why}</p>}
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
          {applicable ? (
            checked && (
              <div className="pt-1">
                <p className="pb-1 text-[11px] font-medium text-muted-foreground">
                  {t('annotations.proposedText')}
                </p>
                <Textarea
                  value={draft}
                  onChange={(e) => { onDraftChange(e.target.value); }}
                  rows={draft.split('\n').length > 6 ? 12 : 4}
                  aria-label={t('annotations.proposedTextAria', { suggestion: s.suggestion })}
                  data-testid={`draft-${s.id}`}
                  className="resize-y font-mono text-[11px] leading-relaxed"
                />
              </div>
            )
          ) : (
            <p className="text-[11px] italic text-muted-foreground">
              {t('annotations.noDraft')}
            </p>
          )}
        </div>
        <Badge className={cn('shrink-0 text-[10px]', sevTone[s.severity])}>{s.severity}</Badge>
      </div>
    </div>
  );
}

export function AnnotationPanel({ session }: { session: PlanSession }) {
  const { t } = useTranslation('common');
  const annotation = session.annotation ?? null;
  const [showDraft, setShowDraft] = useState(false);
  const store = usePlanStore();
  const { sessionLoading, error } = store;
  const [accepted, setAccepted] = useState<Record<string, string>>({});

  const acceptedIds = Object.keys(accepted);

  const handleApply = async () => {
    store.clearError();
    try {
      await store.applyAcceptedSuggestions({
        accepted: acceptedIds.map((id) => ({ id, replacement: accepted[id] })),
        reprocess: true,
      });
      setAccepted({});
    } catch {
      // surfaced via store.error
    }
  };

  if (!annotation || (annotation.suggestions.length === 0 && !annotation.improved_markdown)) {
    return (
      <p className="text-sm text-muted-foreground">
        {t('annotations.empty')}
      </p>
    );
  }

  return (
    <div className="space-y-4" data-testid="annotation-panel">
      <div className="flex items-center gap-2">
        <FileText className="h-4 w-4 text-primary" aria-hidden />
        <h3 className="text-sm font-semibold">
          {t('annotations.heading')} {session.original_filename && (
            <span className="font-normal text-muted-foreground">· {session.original_filename}</span>
          )}
        </h3>
        <span className="ml-auto text-[11px] text-muted-foreground">
          {t('annotations.count', { count: annotation.suggestions.length })}
        </span>
      </div>

      <div className="space-y-2">
        {annotation.suggestions.map((s, i) => (
          <SuggestionRow
            key={s.id || i}
            s={s}
            checked={s.id in accepted}
            draft={accepted[s.id] ?? s.replacement}
            onToggle={(on) => {
              setAccepted((prev) =>
                on
                  ? { ...prev, [s.id]: s.replacement }
                  : Object.fromEntries(Object.entries(prev).filter(([k]) => k !== s.id)),
              );
            }}
            onDraftChange={(text) => { setAccepted((prev) => ({ ...prev, [s.id]: text })); }}
          />
        ))}
      </div>

      {error && (
        <div role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Applying re-runs the pipeline, so the verdict you see next describes the
          text you just accepted — that is the whole point of the loop. */}
      <div className="flex items-center justify-end gap-3">
        <span className="text-[11px] text-muted-foreground">
          {acceptedIds.length === 0
            ? t('annotations.selectPrompt')
            : t('annotations.selected', { count: acceptedIds.length })}
        </span>
        <Button
          onClick={() => void handleApply()}
          disabled={acceptedIds.length === 0 || sessionLoading}
          data-testid="apply-suggestions-btn"
          aria-label={t('annotations.applyAria')}
        >
          <RefreshCw className={cn('mr-2 h-4 w-4', sessionLoading && 'animate-spin')} aria-hidden />
          {acceptedIds.length > 0
            ? t('annotations.applyCount', { count: acceptedIds.length })
            : t('annotations.apply')}
        </Button>
      </div>

      {annotation.improved_markdown && (
        <div>
          <button
            type="button"
            onClick={() => setShowDraft((v) => !v)}
            className="text-xs font-medium text-primary hover:underline"
          >
            {showDraft ? t('annotations.hideDraft') : t('annotations.showDraft')}
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
