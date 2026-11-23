/**
 * PlanningView — top-level two-pane planning portal.
 *
 * Left pane: session list + "New plan" button.
 * Right pane: session detail OR new-plan form.
 *
 * Board mode: renders the animated KanbanBoard (task-store) instead of the
 * read-only PlanBoard of planning sessions (issue #82).  If no project is
 * selected the board falls back to the existing empty placeholder so the
 * component never crashes without a projectId.
 *
 * Mounted from App.tsx via activeView === 'planning'.
 */

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, List, Columns } from 'lucide-react';
import { Button } from '../ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';
import { PlanBoard } from './PlanBoard';
import { cn } from '../../lib/utils';
import { PlanUploadForm } from './PlanUploadForm';
import { usePlanStore } from '../../stores/plan-store';
import { loadTasks } from '../../stores/task-store';
import { useProjectStore } from '../../stores/project-store';
import type { PlanSession } from '../../shared/types/plan';
import type { Task } from '../../shared/types';

interface Props {
  /** Test seam: inject fetchFn into the store before render. */
  fetchFn?: typeof fetch;
  /** Called when user clicks a task card in board mode (open detail modal). */
  onTaskClick?: (task: Task) => void;
  /** Called when user clicks "+ Task" in the board's backlog column header. */
  onNewTaskClick?: () => void;
  /**
   * Plan session to open on arrival (deep-link). Used when navigating here from
   * elsewhere — e.g. "View Plan" on a GitHub issue whose plan was just created.
   * When set, the matching session's detail opens in the List pane.
   */
  focusSessionId?: string | null;
  onShowBoard?: () => void;
}

export function PlanningView({ fetchFn, focusSessionId, onShowBoard }: Props) {
  const { t } = useTranslation('tasks');
  const store = usePlanStore();

  // Wire fetchFn into store if provided (for testing)
  if (fetchFn && store.fetchFn !== fetchFn) {
    store.setFetchFn(fetchFn);
  }

  const selectedProjectId = useProjectStore((state) => state.selectedProjectId);
  const activeProjectId = useProjectStore((state) => state.activeProjectId);

  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [showNewPlanDialog, setShowNewPlanDialog] = useState(false);
  const [viewMode, setViewMode] = useState<'list' | 'board'>('board');
  const revealBoard = useCallback(() => {
    onShowBoard?.();
  }, [onShowBoard]);

  // Keep the session list loaded so the board has data even when the list pane
  // isn't mounted (board mode hides the left sidebar).
  useEffect(() => {
    store.loadSessions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-refresh: keep the board/list converged with server state without a
  // manual reload (a plan ingested/processed/approved elsewhere shows up on its
  // own). Quiet refresh — no spinner flicker — and skipped while the tab is
  // hidden so we don't fetch pointlessly in the background. WS reconnect and
  // tab-visible self-heal are wired separately in api-adapter.ts.
  useEffect(() => {
    const POLL_MS = 20000;
    const id = setInterval(() => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
      store.refreshSessions();
    }, POLL_MS);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Load tasks whenever the active project changes so the kanban board is
  // always in sync when the user switches projects and then opens Board mode.
  useEffect(() => {
    const projectId = activeProjectId || selectedProjectId;
    if (projectId) {
      loadTasks(projectId);
    }
  }, [activeProjectId, selectedProjectId]);

  // Deep-link: when arriving with a focusSessionId (e.g. "View Plan" from a
  // GitHub issue), open that session's detail in the active view. Guarded on the
  // id actually being a loaded plan session so a stale/foreign id is ignored,
  // and it waits for the session list to load before focusing.
  useEffect(() => {
    if (focusSessionId && store.sessions.some((s) => s.session_id === focusSessionId)) {
      setSelectedSessionId(focusSessionId);
    }
  }, [focusSessionId, store.sessions]);

  const handleSessionSelect = useCallback((sessionId: string) => {
    setSelectedSessionId((prev) => (prev === sessionId ? null : sessionId));
  }, []);
  const handleCloseDetail = useCallback(() => setSelectedSessionId(null), []);

  const handleNewPlanSuccess = (session: PlanSession) => {
    setShowNewPlanDialog(false);
    setSelectedSessionId(session.session_id);
  };

  return (
    <div className="flex flex-col h-full" data-testid="planning-view">
      {/* Top header */}
      <header className="flex items-center justify-between border-b border-border px-6 py-3 shrink-0">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Planning Portal</h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            {t('pipeline.fleetDescription')}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* List ⇄ Board toggle */}
          <div className="flex rounded-md border border-border p-0.5" role="group" aria-label="View mode">
            <button
              type="button"
              onClick={() => { setViewMode('list'); }}
              aria-pressed={viewMode === 'list'}
              className={cn(
                'flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors',
                viewMode === 'list' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground',
              )}
            >
              <List className="h-3.5 w-3.5" aria-hidden /> List
            </button>
            <button
              type="button"
              onClick={() => { setViewMode('board'); }}
              aria-pressed={viewMode === 'board'}
              className={cn(
                'flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors',
                viewMode === 'board' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground',
              )}
            >
              <Columns className="h-3.5 w-3.5" aria-hidden /> Board
            </button>
          </div>
          <Button
            onClick={() => setShowNewPlanDialog(true)}
            aria-label="Create new plan"
            data-testid="new-plan-btn"
          >
            <Plus className="mr-2 h-4 w-4" aria-hidden />
            New plan
          </Button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto p-4" data-testid="plan-board-view">
        <PlanBoard
          sessions={store.sessions}
          loading={store.loading}
          error={store.error}
          sessionsLoaded={store.sessionsLoaded}
          selectedId={selectedSessionId}
          viewMode={viewMode}
          projectId={activeProjectId || selectedProjectId || undefined}
          fetchFn={fetchFn}
          onRevealBoard={revealBoard}
          onSelect={handleSessionSelect}
          onCloseDetail={handleCloseDetail}
          onNewPlan={() => setShowNewPlanDialog(true)}
        />
      </div>

      {/* New plan dialog */}
      <Dialog open={showNewPlanDialog} onOpenChange={setShowNewPlanDialog}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>New plan</DialogTitle>
          </DialogHeader>
          <div className="mt-2">
            <PlanUploadForm
              onSuccess={handleNewPlanSuccess}
              onCancel={() => setShowNewPlanDialog(false)}
              fetchFn={fetchFn}
            />
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
