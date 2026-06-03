import { useState, useMemo, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from '../../hooks/use-toast';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { Separator } from '../ui/separator';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../ui/tabs';
import { ScrollArea } from '../ui/scroll-area';
import { TooltipProvider } from '../ui/tooltip';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Progress } from '../ui/progress';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '../ui/alert-dialog';
import {
  Play,
  Square,
  CheckCircle2,
  RotateCcw,
  Trash2,
  Loader2,
  AlertTriangle,
  Pencil,
  X,
  Zap
} from 'lucide-react';
import { cn } from '../../lib/utils';
import { calculateProgress } from '../../lib/utils';
import { startTask, stopTask, submitReview, recoverStuckTask, deleteTask, persistTaskStatus } from '../../stores/task-store';
import { useProjectStore } from '../../stores/project-store';
import { TASK_STATUS_LABELS } from '../../shared/constants';
import { TaskEditDialog } from '../TaskEditDialog';
import { useTaskDetail } from './hooks/useTaskDetail';
import { TaskMetadata } from './TaskMetadata';
import { TaskWarnings } from './TaskWarnings';
import { TaskSubtasks } from './TaskSubtasks';
import { TaskLogs } from './TaskLogs';
import { TaskFiles } from './TaskFiles';
import { TaskReview } from './TaskReview';
import { PlanReviewSection } from './PlanReviewSection';
import { AgentConsole } from './AgentConsole';
import { CreatePRDialog } from './task-review/CreatePRDialog';
import type { Task } from '../../shared/types';

interface TaskDetailModalProps {
  open: boolean;
  task: Task | null;
  onOpenChange: (open: boolean) => void;
  onSwitchToTerminals?: () => void;
  onOpenInbuiltTerminal?: (id: string, cwd: string) => void;
}

export function TaskDetailModal({ open, task, onOpenChange, onSwitchToTerminals, onOpenInbuiltTerminal }: TaskDetailModalProps) {
  // Don't render anything if no task
  if (!task) {
    return null;
  }

  return (
    <TaskDetailModalContent
      open={open}
      task={task}
      onOpenChange={onOpenChange}
      onSwitchToTerminals={onSwitchToTerminals}
      onOpenInbuiltTerminal={onOpenInbuiltTerminal}
    />
  );
}

// Feature flag for Files tab (enabled by default, can be disabled via localStorage)
const isFilesTabEnabled = () => {
  const flag = localStorage.getItem('use_files_tab');
  return flag === null || flag === 'true'; // Enabled by default
};

// Separate component to use hooks only when task exists
function TaskDetailModalContent({ open, task, onOpenChange, onSwitchToTerminals, onOpenInbuiltTerminal }: { open: boolean; task: Task; onOpenChange: (open: boolean) => void; onSwitchToTerminals?: () => void; onOpenInbuiltTerminal?: (id: string, cwd: string) => void }) {
  const { t } = useTranslation(['tasks']);
  const state = useTaskDetail({ task });
  const showFilesTab = isFilesTabEnabled();

  // Epic #44 R2 — show the Live Console tab only when the operator
  // has enabled rmux (PFACTORY_RMUX_ENABLED=true on the server).
  // We probe /api/capabilities once per modal mount; it's a cheap
  // GET that never 404s.  Default to false so the tab stays hidden
  // if the probe fails.
  const [rmuxEnabled, setRmuxEnabled] = useState(false);
  useEffect(() => {
    let cancelled = false;
    fetch('/api/capabilities', { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : { rmux: false }))
      .then((c) => {
        if (!cancelled) setRmuxEnabled(Boolean(c?.rmux));
      })
      .catch(() => {
        if (!cancelled) setRmuxEnabled(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Memoize subtask-based calculations for reactivity
  const { subtaskProgress, completedSubtasks, totalSubtasks } = useMemo(() => {
    const completed = task.subtasks.filter(s => s.status === 'completed').length;
    const total = task.subtasks.length;
    const progress = calculateProgress(task.subtasks);
    return { subtaskProgress: progress, completedSubtasks: completed, totalSubtasks: total };
  }, [task.subtasks]);

  // Memoize progress percentage - uses live overallProgress from backend if available,
  // otherwise falls back to subtask-based calculation
  // This ensures the progress bar updates whenever executionProgress changes
  const progressPercent = useMemo(() => {
    const backendProgress = task.executionProgress?.overallProgress;
    // Use backend progress if available (even if 0), otherwise use subtask calculation
    const percent = backendProgress !== undefined ? backendProgress : subtaskProgress;
    return Math.round(percent);
  }, [task.executionProgress?.overallProgress, subtaskProgress]);

  // Memoize status badge variant and label - ensures React tracks status changes properly
  // This guarantees the badge re-renders when task.status changes via WebSocket events
  const { statusBadgeVariant, statusLabel } = useMemo(() => {
    const variant = task.status === 'done' ? 'success'
      : task.status === 'human_review' ? 'purple'
      : task.status === 'in_progress' ? 'info'
      : 'secondary';
    const label = TASK_STATUS_LABELS[task.status];
    return { statusBadgeVariant: variant as 'success' | 'purple' | 'info' | 'secondary', statusLabel: label };
  }, [task.status]);

  // Memoize review reason badge variant and label - ensures proper reactivity for review states
  const { reviewBadgeVariant, reviewLabel } = useMemo(() => {
    if (!task.reviewReason) return { reviewBadgeVariant: null, reviewLabel: null };
    const variant = task.reviewReason === 'completed' ? 'success'
      : task.reviewReason === 'errors' ? 'destructive'
      : 'warning';
    const label = task.reviewReason === 'completed' ? 'Completed'
      : task.reviewReason === 'errors' ? 'Has Errors'
      : task.reviewReason === 'plan_review' ? 'Approve Plan'
      : 'QA Issues';
    return { reviewBadgeVariant: variant as 'success' | 'destructive' | 'warning', reviewLabel: label };
  }, [task.reviewReason]);

  // Memoize showTaskReview to ensure TaskReview section appears when:
  // 1. Task is in human_review status (needsReview)
  // 2. reviewReason is not 'plan_review' (plan review uses PlanReviewSection instead)
  // Note: reviewReason can be null when phase tracking fails but worktree has changes —
  // we still show the review section so the merge button can evaluate filesChanged
  const showTaskReview = useMemo(() => {
    const shouldShow = state.needsReview && task.reviewReason !== 'plan_review';
    return shouldShow;
  }, [state.needsReview, task.reviewReason]);

  // DEBUG: Log progress updates for troubleshooting live updates
  useEffect(() => {
    if (window.DEBUG) {
      console.log('[TaskDetailModal] Progress update:', {
        taskId: task.specId,
        backendProgress: task.executionProgress?.overallProgress,
        subtaskProgress,
        displayedPercent: progressPercent,
        phase: task.executionProgress?.phase,
        phaseProgress: task.executionProgress?.phaseProgress
      });
    }
  }, [task.specId, task.executionProgress?.overallProgress, task.executionProgress?.phase, task.executionProgress?.phaseProgress, subtaskProgress, progressPercent]);

  // DEBUG: Log status badge updates for troubleshooting live updates
  useEffect(() => {
    if (window.DEBUG) {
      console.log('[TaskDetailModal] Status badge update:', {
        taskId: task.specId,
        status: task.status,
        statusBadgeVariant,
        statusLabel,
        reviewReason: task.reviewReason,
        reviewBadgeVariant,
        reviewLabel,
      });
    }
  }, [task.specId, task.status, statusBadgeVariant, statusLabel, task.reviewReason, reviewBadgeVariant, reviewLabel]);

  // DEBUG: Log TaskReview section visibility changes - critical for subtask 5.4
  // This traces when TaskReview section appears/disappears based on needsReview and reviewReason
  useEffect(() => {
    if (window.DEBUG) {
      console.log('[TaskDetailModal] TaskReview section visibility:', {
        taskId: task.specId,
        needsReview: state.needsReview,
        reviewReason: task.reviewReason,
        showTaskReview,
        showPlanReview: task.status === 'human_review' && task.reviewReason === 'plan_review',
      });
    }
  }, [task.specId, state.needsReview, task.reviewReason, showTaskReview, task.status]);

  // Event Handlers
  const handleStartStop = () => {
    if (state.isRunning && !state.isStuck) {
      stopTask(task.id);
    } else {
      startTask(task.id);
      onOpenChange(false);
    }
  };

  const handleRecover = async () => {
    state.setIsRecovering(true);
    const result = await recoverStuckTask(task.id, { autoRestart: true });
    if (result.success) {
      state.setIsStuck(false);
      state.setHasCheckedRunning(false);

      // Show appropriate toast based on auto-restart status
      if (result.autoRestarted) {
        toast({ title: t('labels.recovered'), description: 'Task recovered and restarted successfully' });
      } else if (result.autoRestartError) {
        toast({
          variant: 'destructive',
          title: t('labels.recovered'),
          description: `Task recovered but restart failed: ${result.autoRestartError}`
        });
      } else {
        toast({ title: t('labels.recovered'), description: 'Task recovered and reset to backlog' });
      }
    } else {
      toast({ variant: 'destructive', title: t('labels.recoveryFailed'), description: result.message || 'Failed to recover task' });
    }
    state.setIsRecovering(false);
  };

  const handleReject = async () => {
    if (!state.feedback.trim()) {
      return;
    }
    state.setIsSubmitting(true);
    await submitReview(task.id, false, state.feedback);
    state.setIsSubmitting(false);
    state.setFeedback('');
  };

  const handleDelete = async () => {
    state.setIsDeleting(true);
    state.setDeleteError(null);
    const result = await deleteTask(task.id);
    if (result.success) {
      state.setShowDeleteDialog(false);
      onOpenChange(false);
    } else {
      state.setDeleteError(result.error || 'Failed to delete task');
    }
    state.setIsDeleting(false);
  };

  const handleMerge = async () => {
    state.setIsMerging(true);
    state.setWorkspaceError(null);
    try {
      const result = await state.unifiedMerge(state.stageOnly);
      if (result.success && result.data) {
        if (result.stageOnly && result.data.staged) {
          state.setWorkspaceError(null);
          state.setStagedSuccess(result.data.message || 'Changes staged in main project');
          state.setStagedProjectPath(result.data.projectPath);
          state.setSuggestedCommitMessage(result.data.suggestedCommitMessage);
        } else {
          // Mark task as done after successful merge (force skips subtask validation)
          const statusUpdated = await persistTaskStatus(task.id, 'done', { force: true });
          if (!statusUpdated) {
            console.warn('Merge succeeded but failed to persist done status for task:', task.id);
          }
          onOpenChange(false);
        }
      }
      // Errors are handled inside unifiedMerge via setWorkspaceError
    } finally {
      state.setIsMerging(false);
    }
  };

  const [isCreatingPR, setIsCreatingPR] = useState(false);
  const [showCreatePRDialog, setShowCreatePRDialog] = useState(false);
  const selectedProject = useProjectStore((s) => s.getSelectedProject());

  const handleCreatePR = () => {
    setShowCreatePRDialog(true);
  };

  const handleDiscard = async () => {
    state.setIsDiscarding(true);
    state.setWorkspaceError(null);
    const result = await window.API.discardWorktree(task.id);
    if (result.success && result.data?.success) {
      state.setShowDiscardDialog(false);
      onOpenChange(false);
    } else {
      state.setWorkspaceError(result.data?.message || result.error || 'Failed to discard changes');
    }
    state.setIsDiscarding(false);
  };

  const handleClose = () => {
    onOpenChange(false);
  };

  // Render primary action button based on state
  const renderPrimaryAction = () => {
    if (state.isStuck) {
      return (
        <Button
          variant="warning"
          onClick={handleRecover}
          disabled={state.isRecovering}
        >
          {state.isRecovering ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Recovering...
            </>
          ) : (
            <>
              <RotateCcw className="mr-2 h-4 w-4" />
              Recover Task
            </>
          )}
        </Button>
      );
    }

    if (state.isIncomplete) {
      return (
        <Button variant="default" onClick={handleStartStop}>
          <Play className="mr-2 h-4 w-4" />
          Resume Task
        </Button>
      );
    }

    if (task.status === 'backlog' || task.status === 'in_progress') {
      return (
        <Button
          variant={state.isRunning ? 'destructive' : 'default'}
          onClick={handleStartStop}
        >
          {state.isRunning ? (
            <>
              <Square className="mr-2 h-4 w-4" />
              Stop Task
            </>
          ) : (
            <>
              <Play className="mr-2 h-4 w-4" />
              Start Task
            </>
          )}
        </Button>
      );
    }

    if (task.status === 'done') {
      return (
        <div className="completion-state text-sm flex items-center gap-2 text-success">
          <CheckCircle2 className="h-5 w-5" />
          <span className="font-medium">Task completed</span>
        </div>
      );
    }

    return null;
  };


  return (
    <TooltipProvider delayDuration={300}>
      <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
        <DialogPrimitive.Portal>
          {/* Semi-transparent overlay - can see background content */}
          <DialogPrimitive.Overlay
            className={cn(
              'fixed inset-0 z-50 bg-black/60',
              'data-[state=open]:animate-in data-[state=closed]:animate-out',
              'data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0'
            )}
          />

          {/* Full-height centered modal content */}
          <DialogPrimitive.Content
            className={cn(
              'fixed left-[50%] top-4 z-50',
              'translate-x-[-50%]',
              'w-[95vw] max-w-5xl h-[calc(100vh-32px)]',
              'bg-card border border-border rounded-xl',
              'shadow-2xl overflow-hidden flex flex-col',
              'data-[state=open]:animate-in data-[state=closed]:animate-out',
              'data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0',
              'data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95',
              'duration-200'
            )}
          >
            {/* Header */}
            <div className="p-5 pb-4 border-b border-border shrink-0">
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0 overflow-hidden">
                  <DialogPrimitive.Title className="text-xl font-semibold leading-tight text-foreground truncate">
                    {task.title}
                  </DialogPrimitive.Title>
                  <DialogPrimitive.Description asChild>
                    <div className="mt-2.5 flex items-center gap-2 flex-wrap">
                      <Badge variant="outline" className="text-xs font-mono">
                        {task.specId}
                      </Badge>
                      {state.isStuck ? (
                        <Badge variant="warning" className="text-xs flex items-center gap-1 animate-pulse">
                          <AlertTriangle className="h-3 w-3" />
                          Stuck
                        </Badge>
                      ) : state.isIncomplete ? (
                        <>
                          <Badge variant="warning" className="text-xs flex items-center gap-1">
                            <AlertTriangle className="h-3 w-3" />
                            Incomplete
                          </Badge>
                        </>
                      ) : (
                        <>
                          <Badge
                            variant={statusBadgeVariant}
                            className={cn('text-xs', (task.status === 'in_progress' && !state.isStuck) && 'status-running')}
                          >
                            {statusLabel}
                          </Badge>
                          {task.status === 'human_review' && reviewBadgeVariant && reviewLabel && (
                            <Badge
                              variant={reviewBadgeVariant}
                              className="text-xs"
                            >
                              {reviewLabel}
                            </Badge>
                          )}
                        </>
                      )}
                      {/* Quick mode badge */}
                      {task.metadata?.mode === 'quick' && (
                        <Badge
                          variant="outline"
                          className="text-xs flex items-center gap-1 bg-amber-500/10 text-amber-400 border-amber-500/30"
                        >
                          <Zap className="h-3 w-3" />
                          QUICK
                        </Badge>
                      )}
                      {/* Compact progress indicator */}
                      {totalSubtasks > 0 && (
                        <span className="text-xs text-muted-foreground ml-1">
                          {completedSubtasks}/{totalSubtasks} subtasks
                        </span>
                      )}
                    </div>
                  </DialogPrimitive.Description>
                </div>
                <div className="flex items-center gap-1 shrink-0 electron-no-drag">
                  <Button
                    variant="ghost"
                    size="icon"
                    className="hover:bg-primary/10 hover:text-primary transition-colors"
                    onClick={() => state.setIsEditDialogOpen(true)}
                    disabled={state.isRunning && !state.isStuck}
                  >
                    <Pencil className="h-4 w-4" />
                  </Button>
                  <DialogPrimitive.Close asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="hover:bg-muted transition-colors"
                    >
                      <X className="h-5 w-5" />
                      <span className="sr-only">Close</span>
                    </Button>
                  </DialogPrimitive.Close>
                </div>
              </div>

              {/* Progress bar - show when running (even with 0 subtasks) or has any progress */}
              {(state.isRunning || progressPercent > 0 || completedSubtasks > 0) && (
                <div className="mt-3 flex items-center gap-3">
                  <Progress
                    value={progressPercent}
                    className="h-1.5 flex-1"
                    animated={state.isRunning && !state.isStuck}
                  />
                  <span className="text-xs text-muted-foreground tabular-nums w-10 text-right">{progressPercent}%</span>
                </div>
              )}

              {/* Warnings - compact inline */}
              {(state.isStuck || state.isIncomplete) && (
                <div className="mt-3">
                  <TaskWarnings
                    isStuck={state.isStuck}
                    isIncomplete={state.isIncomplete}
                    isRecovering={state.isRecovering}
                    taskProgress={state.taskProgress}
                    onRecover={handleRecover}
                    onResume={handleStartStop}
                  />
                </div>
              )}
            </div>

            {/* Body - Single Column with Tabs */}
            <div className="flex-1 min-h-0 overflow-hidden">
              <Tabs value={state.activeTab} onValueChange={state.setActiveTab} className="flex flex-col h-full">
                <TabsList className="w-full justify-start rounded-none border-b border-border bg-transparent px-5 h-auto shrink-0">
                  <TabsTrigger
                    value="overview"
                    className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none px-4 py-2.5 text-sm"
                  >
                    Overview
                  </TabsTrigger>
                  <TabsTrigger
                    value="subtasks"
                    className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none px-4 py-2.5 text-sm"
                  >
                    Subtasks ({task.subtasks.length})
                  </TabsTrigger>
                  <TabsTrigger
                    value="logs"
                    className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none px-4 py-2.5 text-sm"
                  >
                    Logs
                  </TabsTrigger>
                  {showFilesTab && (
                    <TabsTrigger
                      value="files"
                      className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none px-4 py-2.5 text-sm"
                    >
                      {t('tasks:files.tab')}
                    </TabsTrigger>
                  )}
                  {rmuxEnabled && (
                    <TabsTrigger
                      value="agent-console"
                      className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none px-4 py-2.5 text-sm"
                      data-testid="agent-console-tab-trigger"
                    >
                      {t('tasks:agentConsole.tab')}
                    </TabsTrigger>
                  )}
                </TabsList>

                {/* Overview Tab */}
                <TabsContent value="overview" className="flex-1 min-h-0 overflow-hidden mt-0">
                  <ScrollArea className="h-full">
                    <div className="p-5 space-y-5">
                      {/* Metadata */}
                      <TaskMetadata task={task} />

                      {/* Plan Review Section - shown when waiting for plan approval */}
                      {task.status === 'human_review' && task.reviewReason === 'plan_review' && (
                        <>
                          <Separator />
                          <PlanReviewSection
                            task={task}
                            onResume={handleStartStop}
                          />
                        </>
                      )}

                      {/* Human Review Section - shown for post-build review (merge/discard)
                          Uses memoized showTaskReview for proper reactivity when reviewReason is set */}
                      {showTaskReview && (
                        <>
                          <Separator />
                          <TaskReview
                            task={task}
                            feedback={state.feedback}
                            isSubmitting={state.isSubmitting}
                            worktreeStatus={state.worktreeStatus}
                            worktreeDiff={state.worktreeDiff}
                            isLoadingWorktree={state.isLoadingWorktree}
                            isMerging={state.isMerging}
                            isDiscarding={state.isDiscarding}
                            showDiscardDialog={state.showDiscardDialog}
                            showDiffDialog={state.showDiffDialog}
                            workspaceError={state.workspaceError}
                            stageOnly={state.stageOnly}
                            stagedSuccess={state.stagedSuccess}
                            stagedProjectPath={state.stagedProjectPath}
                            suggestedCommitMessage={state.suggestedCommitMessage}
                            mergePreview={state.mergePreview}
                            isLoadingPreview={state.isLoadingPreview}
                            showConflictDialog={state.showConflictDialog}
                            isAbortingMerge={state.isAbortingMerge}
                            mergeStep={state.mergeStep}
                            phaseLogs={state.phaseLogs ?? undefined}
                            onFeedbackChange={state.setFeedback}
                            onReject={handleReject}
                            onMerge={handleMerge}
                            onCreatePR={handleCreatePR}
                            isCreatingPR={isCreatingPR}
                            onDiscard={handleDiscard}
                            onShowDiscardDialog={state.setShowDiscardDialog}
                            onShowDiffDialog={state.setShowDiffDialog}
                            onStageOnlyChange={state.setStageOnly}
                            onShowConflictDialog={state.setShowConflictDialog}
                            onLoadMergePreview={state.loadMergePreview}
                            onAbortMerge={state.abortMerge}
                            onClose={handleClose}
                            onSwitchToTerminals={onSwitchToTerminals}
                            onOpenInbuiltTerminal={onOpenInbuiltTerminal}
                          />
                        </>
                      )}
                    </div>
                  </ScrollArea>
                </TabsContent>

                {/* Subtasks Tab */}
                <TabsContent value="subtasks" className="flex-1 min-h-0 overflow-hidden mt-0">
                  <TaskSubtasks task={task} />
                </TabsContent>

                {/* Logs Tab */}
                <TabsContent value="logs" className="flex-1 min-h-0 overflow-hidden mt-0">
                  <TaskLogs
                    task={task}
                    phaseLogs={state.phaseLogs}
                    isLoadingLogs={state.isLoadingLogs}
                    expandedPhases={state.expandedPhases}
                    isStuck={state.isStuck}
                    logsEndRef={state.logsEndRef}
                    logsContainerRef={state.logsContainerRef}
                    onLogsScroll={state.handleLogsScroll}
                    onTogglePhase={state.togglePhase}
                  />
                </TabsContent>

                {/* Files Tab */}
                {showFilesTab && (
                  <TabsContent value="files" className="flex-1 min-h-0 overflow-hidden mt-0">
                    <TaskFiles
                      task={task}
                      worktreeSpecsPath={
                        state.worktreeStatus?.exists && state.worktreeStatus.worktreePath && task.specId
                          ? `${state.worktreeStatus.worktreePath}/.pfactory/specs/${task.specId}`
                          : undefined
                      }
                    />
                  </TabsContent>
                )}

                {/* Epic #44 R2 — Live Agent Console Tab (rmux opt-in) */}
                {rmuxEnabled && (
                  <TabsContent value="agent-console" className="flex-1 min-h-0 overflow-hidden mt-0">
                    <AgentConsole taskId={task.id} />
                  </TabsContent>
                )}
              </Tabs>
            </div>

            {/* Footer - Actions */}
            <div className="flex items-center gap-3 px-5 py-3 border-t border-border shrink-0">
              <Button
                variant="ghost"
                size="sm"
                className="text-muted-foreground hover:text-destructive hover:bg-destructive/10"
                onClick={() => state.setShowDeleteDialog(true)}
                disabled={state.isRunning && !state.isStuck}
              >
                <Trash2 className="mr-2 h-4 w-4" />
                Delete Task
              </Button>
              <div className="flex-1" />
              {renderPrimaryAction()}
              <Button variant="outline" onClick={handleClose}>
                Close
              </Button>
            </div>
          </DialogPrimitive.Content>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>

      {/* Edit Task Dialog */}
      <TaskEditDialog
        task={task}
        open={state.isEditDialogOpen}
        onOpenChange={state.setIsEditDialogOpen}
      />

      {/* Create PR Dialog */}
      {selectedProject && (
        <CreatePRDialog
          open={showCreatePRDialog}
          task={task}
          projectPath={selectedProject.path}
          onOpenChange={setShowCreatePRDialog}
          onSuccess={() => {
            setIsCreatingPR(false);
          }}
          onError={(error) => {
            state.setWorkspaceError(error);
            setIsCreatingPR(false);
          }}
        />
      )}

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={state.showDeleteDialog} onOpenChange={state.setShowDeleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-destructive" />
              Delete Task
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="text-sm text-muted-foreground space-y-3">
                <p>
                  Are you sure you want to delete <strong className="text-foreground">"{task.title}"</strong>?
                </p>
                <p className="text-destructive">
                  This action cannot be undone. All task files, including the spec, implementation plan, and any generated code will be permanently deleted from the project.
                </p>
                {state.deleteError && (
                  <p className="text-destructive bg-destructive/10 px-3 py-2 rounded-lg text-sm">
                    {state.deleteError}
                  </p>
                )}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={state.isDeleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                handleDelete();
              }}
              disabled={state.isDeleting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {state.isDeleting ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Deleting...
                </>
              ) : (
                <>
                  <Trash2 className="mr-2 h-4 w-4" />
                  Delete Permanently
                </>
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </TooltipProvider>
  );
}
