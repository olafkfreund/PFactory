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

import { useEffect, useState, useCallback } from 'react';
import { Plus, Package, LayoutTemplate, Cpu, List, Columns } from 'lucide-react';
import { Button } from '../ui/button';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../ui/tabs';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';
import { SessionList } from './SessionList';
import { SessionDetail } from './SessionDetail';
import { KanbanBoard } from '../KanbanBoard';
import { cn } from '../../lib/utils';
import { PlanUploadForm } from './PlanUploadForm';
import { RegistryPanel } from './RegistryPanel';
import { TemplatesPanel } from './TemplatesPanel';
import { ProvidersPanel } from './ProvidersPanel';
import { usePlanStore } from '../../stores/plan-store';
import { useTaskStore, loadTasks } from '../../stores/task-store';
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
}

export function PlanningView({ fetchFn, onTaskClick, onNewTaskClick }: Props) {
  const store = usePlanStore();

  // Wire fetchFn into store if provided (for testing)
  if (fetchFn && store.fetchFn !== fetchFn) {
    store.setFetchFn(fetchFn);
  }

  // Task-store wiring for Board mode (mirrors the old App.tsx 'kanban' branch).
  const tasks = useTaskStore((state) => state.tasks);
  const projects = useProjectStore((state) => state.projects);
  const selectedProjectId = useProjectStore((state) => state.selectedProjectId);
  const activeProjectId = useProjectStore((state) => state.activeProjectId);
  const selectedProject = projects.find(
    (p) => p.id === (activeProjectId || selectedProjectId),
  );

  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [showNewPlanDialog, setShowNewPlanDialog] = useState(false);
  // Default to the animated Board so the ring rail + stage medallions + animated
  // cards are the first thing you see on Planning (List is one toggle away).
  const [viewMode, setViewMode] = useState<'list' | 'board'>('board');

  // Keep the session list loaded so the board has data even when the list pane
  // isn't mounted (board mode hides the left sidebar).
  useEffect(() => {
    store.loadSessions();
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

  const handleSessionSelect = (sessionId: string) => {
    setSelectedSessionId(sessionId);
  };

  const handleNewPlanSuccess = (session: PlanSession) => {
    setShowNewPlanDialog(false);
    setSelectedSessionId(session.session_id);
  };

  // Stable callback wrappers so KanbanBoard's memo comparisons stay stable.
  const handleTaskClick = useCallback(
    (task: Task) => {
      onTaskClick?.(task);
    },
    [onTaskClick],
  );

  const handleNewTaskClick = useCallback(() => {
    onNewTaskClick?.();
  }, [onNewTaskClick]);

  return (
    <div className="flex flex-col h-full" data-testid="planning-view">
      {/* Top header */}
      <header className="flex items-center justify-between border-b border-border px-6 py-3 shrink-0">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Planning Portal</h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Ingest, process, review and emit product plans.
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
              onClick={() => { setViewMode('board'); setSelectedSessionId(null); }}
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

      {/* Board mode: animated KanbanBoard wired to the task-store.
          Falls back to a "no project selected" placeholder when there is no
          active project so the component never crashes. */}
      {viewMode === 'board' ? (
        <div className="flex-1 overflow-hidden" data-testid="plan-board-view">
          {!selectedProject ? (
            <div className="flex flex-col items-center justify-center h-full py-20 text-muted-foreground gap-3">
              <Columns className="h-8 w-8 opacity-30" aria-hidden />
              <p className="text-sm font-medium">Select a project to see the task board.</p>
            </div>
          ) : (
            <KanbanBoard
              tasks={tasks}
              onTaskClick={handleTaskClick}
              onNewTaskClick={handleNewTaskClick}
              isInitialized={!!selectedProject.autoBuildPath}
            />
          )}
        </div>
      ) : (
      /* Main content: two-pane layout + meta tabs at bottom */
      <div className="flex flex-1 overflow-hidden">
        {/* Left pane: session list */}
        <aside
          className="w-72 shrink-0 border-r border-border flex flex-col overflow-hidden p-4"
          aria-label="Sessions sidebar"
        >
          <SessionList
            selectedId={selectedSessionId}
            onSelect={handleSessionSelect}
          />
        </aside>

        {/* Right pane: detail or empty state */}
        <main className="flex-1 overflow-y-auto p-6">
          {selectedSessionId ? (
            <SessionDetail
              sessionId={selectedSessionId}
              onBack={() => setSelectedSessionId(null)}
            />
          ) : (
            <div className="flex flex-col gap-8">
              {/* Empty state */}
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground gap-3">
                <div className="h-14 w-14 rounded-2xl bg-muted/50 flex items-center justify-center">
                  <Plus className="h-7 w-7 opacity-40" aria-hidden />
                </div>
                <p className="text-sm font-medium">Select a session or create a new plan</p>
                <Button
                  variant="outline"
                  onClick={() => setShowNewPlanDialog(true)}
                  aria-label="Create new plan"
                >
                  <Plus className="mr-2 h-4 w-4" aria-hidden />
                  New plan
                </Button>
              </div>

              {/* Management tabs */}
              <div className="rounded-xl border border-border/60 bg-card/30 p-4">
                <h2 className="text-sm font-semibold text-foreground mb-4">Configuration</h2>
                <Tabs defaultValue="registry">
                  <TabsList>
                    <TabsTrigger value="registry">
                      <Package className="h-3.5 w-3.5 mr-1.5" aria-hidden />
                      Registry
                    </TabsTrigger>
                    <TabsTrigger value="templates">
                      <LayoutTemplate className="h-3.5 w-3.5 mr-1.5" aria-hidden />
                      Templates
                    </TabsTrigger>
                    <TabsTrigger value="providers">
                      <Cpu className="h-3.5 w-3.5 mr-1.5" aria-hidden />
                      Providers
                    </TabsTrigger>
                  </TabsList>
                  <TabsContent value="registry" className="mt-4">
                    <RegistryPanel fetchFn={fetchFn} />
                  </TabsContent>
                  <TabsContent value="templates" className="mt-4">
                    <TemplatesPanel fetchFn={fetchFn} />
                  </TabsContent>
                  <TabsContent value="providers" className="mt-4">
                    <ProvidersPanel fetchFn={fetchFn} />
                  </TabsContent>
                </Tabs>
              </div>
            </div>
          )}
        </main>
      </div>
      )}

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
