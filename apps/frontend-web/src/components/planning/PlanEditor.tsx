/**
 * PlanEditor — in-place edit of title, description, and criteria.
 *
 * Re-running process after edits invalidates gates/approval — the user
 * sees this feedback via the status flow.
 */

import { useState, useCallback } from 'react';
import { Plus, X, RefreshCw } from 'lucide-react';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Textarea } from '../ui/textarea';
import { usePlanStore } from '../../stores/plan-store';
import type { PlanSession, PlanCriterion } from '../../shared/types/plan';

interface Props {
  session: PlanSession;
  onUpdated?: (session: PlanSession) => void;
}

export function PlanEditor({ session, onUpdated }: Props) {
  const store = usePlanStore();
  const { sessionLoading, error } = store;

  const [title, setTitle] = useState(session.plan.title);
  const [description, setDescription] = useState(session.plan.description);
  const [criteria, setCriteria] = useState<PlanCriterion[]>(
    session.plan.criteria.map((c) => ({ ...c })),
  );
  const [newCriterionText, setNewCriterionText] = useState('');
  const [dirty, setDirty] = useState(false);

  const markDirty = useCallback(() => setDirty(true), []);

  const handleAddCriterion = () => {
    if (!newCriterionText.trim()) return;
    const id = `C${Date.now()}`;
    setCriteria((prev) => [...prev, { id, text: newCriterionText.trim() }]);
    setNewCriterionText('');
    markDirty();
  };

  const handleRemoveCriterion = (id: string) => {
    setCriteria((prev) => prev.filter((c) => c.id !== id));
    markDirty();
  };

  const handleUpdateCriterion = (id: string, text: string) => {
    setCriteria((prev) => prev.map((c) => c.id === id ? { ...c, text } : c));
    markDirty();
  };

  const handleReprocess = async () => {
    store.clearError();
    try {
      await store.processCurrentSession({ title, description, criteria });
      const updated = store.currentSession;
      if (updated) {
        setDirty(false);
        onUpdated?.(updated);
      }
    } catch {
      // error surfaced via store.error
    }
  };

  return (
    <div className="flex flex-col gap-5" data-testid="plan-editor">
      <div className="flex flex-col gap-1.5">
        <label htmlFor="edit-title" className="text-sm font-medium text-foreground">
          Title
        </label>
        <Input
          id="edit-title"
          value={title}
          onChange={(e) => { setTitle(e.target.value); markDirty(); }}
          aria-label="Plan title"
          data-testid="edit-title"
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <label htmlFor="edit-description" className="text-sm font-medium text-foreground">
          Description
        </label>
        {/*
          The description carries the whole plan document — business rules, open
          questions, MVP scope — kilobytes of markdown, not a summary line. At
          rows=4 it was a 3-line porthole onto it. Monospace + resize-y so the
          markdown structure is legible and the reader can size it themselves.
        */}
        <Textarea
          id="edit-description"
          value={description}
          onChange={(e) => { setDescription(e.target.value); markDirty(); }}
          rows={24}
          aria-label="Plan description"
          data-testid="edit-description"
          className="min-h-[20rem] resize-y font-mono text-xs leading-relaxed"
        />
      </div>

      {/* Criteria */}
      <div className="flex flex-col gap-2">
        <label className="text-sm font-medium text-foreground">
          Acceptance criteria ({criteria.length})
        </label>
        {criteria.map((c) => (
          <div key={c.id} className="flex items-start gap-2">
            <span className="font-mono text-xs text-muted-foreground mt-2.5 shrink-0 w-14 truncate">
              {c.id}
            </span>
            <Textarea
              value={c.text}
              onChange={(e) => handleUpdateCriterion(c.id, e.target.value)}
              rows={2}
              aria-label={`Criterion ${c.id}`}
              className="flex-1 min-h-0 text-xs"
            />
            <button
              type="button"
              aria-label={`Remove criterion ${c.id}`}
              onClick={() => handleRemoveCriterion(c.id)}
              className="mt-2 rounded p-1 hover:bg-destructive/10 hover:text-destructive transition-colors text-muted-foreground"
            >
              <X className="h-3.5 w-3.5" aria-hidden />
            </button>
          </div>
        ))}

        <div className="flex gap-2">
          <Input
            placeholder="New criterion text…"
            value={newCriterionText}
            onChange={(e) => setNewCriterionText(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleAddCriterion()}
            aria-label="New criterion"
            data-testid="new-criterion-input"
            className="text-xs"
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleAddCriterion}
            disabled={!newCriterionText.trim()}
            aria-label="Add criterion"
          >
            <Plus className="h-3.5 w-3.5" aria-hidden />
          </Button>
        </div>
      </div>

      {error && (
        <div role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}

      {dirty && (
        <div
          role="status"
          className="rounded-md border border-warning/30 bg-warning/5 px-3 py-2 text-xs text-warning"
        >
          Unsaved changes. Re-process saves them and re-runs review — which clears the current review and any approval.
        </div>
      )}

      <div className="flex justify-end gap-3">
        <Button
          variant="outline"
          onClick={() => {
            setTitle(session.plan.title);
            setDescription(session.plan.description);
            setCriteria(session.plan.criteria.map((c) => ({ ...c })));
            setDirty(false);
          }}
          disabled={!dirty || sessionLoading}
        >
          Discard
        </Button>
        <Button
          onClick={handleReprocess}
          disabled={sessionLoading}
          data-testid="reprocess-btn"
          aria-label="Re-process plan"
        >
          {sessionLoading ? (
            <RefreshCw className="mr-2 h-4 w-4 animate-spin" aria-hidden />
          ) : (
            <RefreshCw className="mr-2 h-4 w-4" aria-hidden />
          )}
          Re-process
        </Button>
      </div>
    </div>
  );
}
