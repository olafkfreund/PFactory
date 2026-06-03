/**
 * PlanningView — top-level two-pane planning portal.
 *
 * Left pane: session list + "New plan" button.
 * Right pane: session detail OR new-plan form.
 *
 * Mounted from App.tsx via activeView === 'planning'.
 */

import { useState } from 'react';
import { Plus, Package, LayoutTemplate, Cpu } from 'lucide-react';
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
import { PlanUploadForm } from './PlanUploadForm';
import { RegistryPanel } from './RegistryPanel';
import { TemplatesPanel } from './TemplatesPanel';
import { ProvidersPanel } from './ProvidersPanel';
import { usePlanStore } from '../../stores/plan-store';
import type { PlanSession } from '../../shared/types/plan';

interface Props {
  /** Test seam: inject fetchFn into the store before render. */
  fetchFn?: typeof fetch;
}

export function PlanningView({ fetchFn }: Props) {
  const store = usePlanStore();

  // Wire fetchFn into store if provided (for testing)
  if (fetchFn && store.fetchFn !== fetchFn) {
    store.setFetchFn(fetchFn);
  }

  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [showNewPlanDialog, setShowNewPlanDialog] = useState(false);

  const handleSessionSelect = (sessionId: string) => {
    setSelectedSessionId(sessionId);
  };

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
            Ingest, process, review and emit product plans.
          </p>
        </div>
        <Button
          onClick={() => setShowNewPlanDialog(true)}
          aria-label="Create new plan"
          data-testid="new-plan-btn"
        >
          <Plus className="mr-2 h-4 w-4" aria-hidden />
          New plan
        </Button>
      </header>

      {/* Main content: two-pane layout + meta tabs at bottom */}
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
