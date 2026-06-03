/**
 * SessionDetail — right-pane detail view for a selected PlanSession.
 *
 * Shows pipeline, review, approval, emit, and editor tabs.
 * Loads the session via the store on mount.
 */

import { useEffect } from 'react';
import { Loader2, AlertTriangle, ChevronLeft, RefreshCw, Play } from 'lucide-react';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../ui/tabs';
import { usePlanStore } from '../../stores/plan-store';
import { PipelinePanel } from './PipelinePanel';
import { ReviewPanel } from './ReviewPanel';
import { ApprovalPanel } from './ApprovalPanel';
import { EmitPanel } from './EmitPanel';
import { PlanEditor } from './PlanEditor';
import type { PlanSessionStatus } from '../../shared/types/plan';

const STATUS_BADGE: Record<PlanSessionStatus, { variant: 'success' | 'info' | 'warning' | 'destructive' | 'muted'; label: string }> = {
  ingested: { variant: 'muted', label: 'ingested' },
  processed: { variant: 'info', label: 'processed' },
  approved: { variant: 'success', label: 'approved' },
  rejected: { variant: 'destructive', label: 'rejected' },
  emitted: { variant: 'success', label: 'emitted' },
};

interface Props {
  sessionId: string;
  onBack: () => void;
}

export function SessionDetail({ sessionId, onBack }: Props) {
  const store = usePlanStore();
  const { currentSession, sessionLoading, error } = store;

  useEffect(() => {
    store.selectSession(sessionId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  if (sessionLoading && !currentSession) {
    return (
      <div role="status" className="flex items-center gap-2 p-8 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        <span>Loading session…</span>
      </div>
    );
  }

  if (error && !currentSession) {
    return (
      <div role="alert" className="flex items-center gap-2 p-8 text-destructive">
        <AlertTriangle className="h-4 w-4" aria-hidden />
        <span>{error}</span>
      </div>
    );
  }

  if (!currentSession || currentSession.session_id !== sessionId) {
    return null;
  }

  const session = currentSession;
  const statusMeta = STATUS_BADGE[session.status] ?? { variant: 'muted' as const, label: session.status };
  const canProcess = session.status === 'ingested' || session.status === 'processed';
  const hasReview = session.review !== null;
  const hasEpic = session.epic !== null;
  const isApprovedOrEmitted = session.status === 'approved' || session.status === 'emitted';

  // Default to 'pipeline' if processed; 'editor' if only ingested
  const defaultTab = hasEpic ? 'pipeline' : 'editor';

  return (
    <div className="flex flex-col gap-4 h-full" data-testid="session-detail">
      {/* Header */}
      <div className="flex items-start gap-3">
        <button
          type="button"
          onClick={onBack}
          aria-label="Back to sessions"
          className="mt-0.5 rounded p-1 text-muted-foreground hover:text-primary transition-colors"
        >
          <ChevronLeft className="h-5 w-5" aria-hidden />
        </button>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="text-lg font-semibold text-foreground truncate">
              {session.plan.title}
            </h2>
            <Badge variant={statusMeta.variant}>{statusMeta.label}</Badge>
          </div>
          <p className="font-mono text-xs text-muted-foreground mt-0.5">{session.session_id}</p>
        </div>

        {/* Process button */}
        {canProcess && (
          <Button
            size="sm"
            onClick={() => store.processCurrentSession()}
            disabled={sessionLoading}
            data-testid="process-btn"
            aria-label="Process session"
          >
            {sessionLoading ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <Play className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            )}
            Process
          </Button>
        )}

        {/* Refresh */}
        <Button
          variant="ghost"
          size="icon"
          onClick={() => store.refreshCurrentSession()}
          disabled={sessionLoading}
          aria-label="Refresh session"
        >
          <RefreshCw className={`h-4 w-4 ${sessionLoading ? 'animate-spin' : ''}`} aria-hidden />
        </Button>
      </div>

      {/* Error banner */}
      {error && (
        <div role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Content tabs */}
      <div className="flex-1 overflow-y-auto">
        <Tabs defaultValue={defaultTab}>
          <TabsList>
            <TabsTrigger value="editor">Edit</TabsTrigger>
            {hasEpic && <TabsTrigger value="pipeline">Pipeline</TabsTrigger>}
            {hasReview && <TabsTrigger value="review">Review</TabsTrigger>}
            {hasReview && <TabsTrigger value="approval">Approval</TabsTrigger>}
            {isApprovedOrEmitted && <TabsTrigger value="emit">Emit</TabsTrigger>}
          </TabsList>

          <TabsContent value="editor" className="mt-4">
            <PlanEditor
              session={session}
              onUpdated={() => store.refreshCurrentSession()}
            />
          </TabsContent>

          {hasEpic && (
            <TabsContent value="pipeline" className="mt-4">
              <PipelinePanel session={session} />
            </TabsContent>
          )}

          {hasReview && (
            <TabsContent value="review" className="mt-4">
              <ReviewPanel review={session.review!} />
            </TabsContent>
          )}

          {hasReview && (
            <TabsContent value="approval" className="mt-4">
              <ApprovalPanel
                session={session}
                onUpdated={() => store.refreshCurrentSession()}
              />
            </TabsContent>
          )}

          {isApprovedOrEmitted && (
            <TabsContent value="emit" className="mt-4">
              <EmitPanel
                session={session}
                onUpdated={() => store.refreshCurrentSession()}
              />
            </TabsContent>
          )}
        </Tabs>
      </div>
    </div>
  );
}
