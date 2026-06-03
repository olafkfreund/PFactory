import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { useProjectStore } from '../../../stores/project-store';
import { checkTaskRunning, isIncompleteHumanReview, getTaskProgress } from '../../../stores/task-store';
import type { Task, TaskLogs, TaskLogPhase, TaskPhaseLog, TaskLogStreamChunk, TaskLogEntry, WorktreeStatus, WorktreeDiff, MergeConflict, MergeStats, GitConflictInfo } from '../../../shared/types';

export interface UseTaskDetailOptions {
  task: Task;
}

export function useTaskDetail({ task }: UseTaskDetailOptions) {
  // Debug: Log when task prop changes (reactivity verification)
  // This helps verify that store updates propagate to TaskDetailModal correctly
  useEffect(() => {
    if (window.DEBUG) {
      console.log('[useTaskDetail] Task prop changed:', {
        id: task.id,
        specId: task.specId,
        status: task.status,
        subtasksCount: task.subtasks?.length,
        completedSubtasks: task.subtasks?.filter(s => s.status === 'completed').length,
        phase: task.executionProgress?.phase,
        reviewReason: task.reviewReason,
      });
    }
  }, [task, task.status, task.subtasks, task.executionProgress?.phase, task.reviewReason]);

  const [feedback, setFeedback] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [activeTab, setActiveTab] = useState('overview');
  const [isUserScrolledUp, setIsUserScrolledUp] = useState(false);
  const [isStuck, setIsStuck] = useState(false);
  const [isRecovering, setIsRecovering] = useState(false);
  const [hasCheckedRunning, setHasCheckedRunning] = useState(false);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [isEditDialogOpen, setIsEditDialogOpen] = useState(false);
  const [worktreeStatus, setWorktreeStatus] = useState<WorktreeStatus | null>(null);
  const [worktreeDiff, setWorktreeDiff] = useState<WorktreeDiff | null>(null);
  const [isLoadingWorktree, setIsLoadingWorktree] = useState(false);
  const [isMerging, setIsMerging] = useState(false);
  const [isDiscarding, setIsDiscarding] = useState(false);
  const [showDiscardDialog, setShowDiscardDialog] = useState(false);
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [showDiffDialog, setShowDiffDialog] = useState(false);
  const [stageOnly, setStageOnly] = useState(false); // Default to full merge for proper cleanup (fixes #243)
  const [stagedSuccess, setStagedSuccess] = useState<string | null>(null);
  const [stagedProjectPath, setStagedProjectPath] = useState<string | undefined>(undefined);
  const [suggestedCommitMessage, setSuggestedCommitMessage] = useState<string | undefined>(undefined);
  const [phaseLogs, setPhaseLogs] = useState<TaskLogs | null>(null);
  const [isLoadingLogs, setIsLoadingLogs] = useState(false);
  const [expandedPhases, setExpandedPhases] = useState<Set<TaskLogPhase>>(new Set());
  const logsEndRef = useRef<HTMLDivElement>(null);
  const logsContainerRef = useRef<HTMLDivElement>(null);

  // Merge preview state
  const [mergePreview, setMergePreview] = useState<{
    files: string[];
    conflicts: MergeConflict[];
    summary: MergeStats;
    gitConflicts?: GitConflictInfo;
    uncommittedChanges?: { hasChanges: boolean; files: string[]; count: number; conflictingFiles?: string[]; hasConflicts?: boolean } | null;
  } | null>(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState(false);
  const [showConflictDialog, setShowConflictDialog] = useState(false);
  const [isResolvingConflicts, setIsResolvingConflicts] = useState(false);
  const [isResolvingUncommitted, setIsResolvingUncommitted] = useState(false);
  const [isAbortingMerge, setIsAbortingMerge] = useState(false);
  const [mergeStep, setMergeStep] = useState<'idle' | 'resolving_uncommitted' | 'resolving_git_conflicts' | 'merging'>('idle');

  const selectedProject = useProjectStore((state) => state.getSelectedProject());
  const isRunning = task.status === 'in_progress';
  // isActiveTask includes ai_review for stuck detection (CHANGELOG documents this feature)
  const isActiveTask = task.status === 'in_progress' || task.status === 'ai_review';

  // Memoize needsReview to ensure proper React dependency tracking when status changes
  // This is critical for TaskReview section appearing when task enters human_review state
  const needsReview = useMemo(() => task.status === 'human_review', [task.status]);

  const executionPhase = task.executionProgress?.phase;
  const hasActiveExecution = executionPhase && executionPhase !== 'idle' && executionPhase !== 'complete' && executionPhase !== 'failed';
  const isIncomplete = isIncompleteHumanReview(task);
  const taskProgress = getTaskProgress(task);

  // Check if task is stuck (status says in_progress/ai_review but no actual process)
  // Add a grace period to avoid false positives during process spawn
  useEffect(() => {
    let timeoutId: NodeJS.Timeout | undefined;

    if (isActiveTask && !hasCheckedRunning) {
      // Wait 2 seconds before checking - gives process time to spawn and register
      timeoutId = setTimeout(() => {
        checkTaskRunning(task.id).then((actuallyRunning) => {
          setIsStuck(!actuallyRunning);
          setHasCheckedRunning(true);
        });
      }, 2000);
    } else if (!isActiveTask) {
      setIsStuck(false);
      setHasCheckedRunning(false);
    }

    return () => {
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, [task.id, isActiveTask, hasCheckedRunning]);

  // Handle scroll events in logs to detect if user scrolled up
  const handleLogsScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const target = e.target as HTMLDivElement;
    const isNearBottom = target.scrollHeight - target.scrollTop - target.clientHeight < 100;
    setIsUserScrolledUp(!isNearBottom);
  };

  // Auto-scroll logs to bottom only if user hasn't scrolled up
  // Triggers on both legacy task.logs and new phaseLogs updates
  useEffect(() => {
    if (activeTab === 'logs' && logsEndRef.current && !isUserScrolledUp) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [task.logs, phaseLogs, activeTab, isUserScrolledUp]);

  // Reset scroll state and reload logs when switching to logs tab
  useEffect(() => {
    if (activeTab === 'logs') {
      setIsUserScrolledUp(false);

      // Reload logs to get the latest data
      const reloadLogs = async () => {
        if (!selectedProject) return;

        setIsLoadingLogs(true);
        try {
          const result = await window.API.getTaskLogs(selectedProject.id, task.specId);
          if (result.success && result.data) {
            setPhaseLogs(result.data);
            // Auto-expand active phase
            if (result.data.phases) {
              const activePhase = (['planning', 'coding', 'validation'] as TaskLogPhase[]).find(
                phase => result.data?.phases?.[phase]?.status === 'active'
              );
              if (activePhase) {
                setExpandedPhases(new Set([activePhase]));
              }
            }
          }
        } catch (err) {
          console.error('Failed to reload task logs:', err);
        } finally {
          setIsLoadingLogs(false);
        }
      };

      reloadLogs();
    }
  }, [activeTab, selectedProject, task.specId]);

  // Load worktree status - extracted as callback for manual refresh after conflict resolution
  const loadWorktreeStatus = useCallback(async () => {
    setIsLoadingWorktree(true);
    setWorkspaceError(null);
    try {
      const [statusResult, diffResult] = await Promise.all([
        window.API.getWorktreeStatus(task.id),
        window.API.getWorktreeDiff(task.id)
      ]);
      if (statusResult.success && statusResult.data) {
        setWorktreeStatus(statusResult.data);
      }
      if (diffResult.success && diffResult.data) {
        setWorktreeDiff(diffResult.data);
      }
    } catch (err) {
      console.error('Failed to load worktree info:', err);
    } finally {
      setIsLoadingWorktree(false);
    }
  }, [task.id]);

  // Load worktree status when task is in human_review (including plan_review — worktrees exist for all phases)
  useEffect(() => {
    if (needsReview) {
      loadWorktreeStatus();
    } else {
      setWorktreeStatus(null);
      setWorktreeDiff(null);
    }
  }, [task.id, task.reviewReason, needsReview, loadWorktreeStatus]);

  // Load and watch phase logs
  useEffect(() => {
    if (!selectedProject) return;

    const loadLogs = async () => {
      setIsLoadingLogs(true);
      console.log('[useTaskDetail] Loading logs for:', { projectId: selectedProject.id, specId: task.specId, taskId: task.id });
      try {
        const result = await window.API.getTaskLogs(selectedProject.id, task.specId);
        console.log('[useTaskDetail] getTaskLogs result:', result);
        if (result.success && result.data) {
          console.log('[useTaskDetail] Setting phaseLogs:', result.data);
          setPhaseLogs(result.data);
          // Auto-expand active phase (check phases exists first)
          if (result.data.phases) {
            const activePhase = (['planning', 'coding', 'validation'] as TaskLogPhase[]).find(
              phase => result.data?.phases?.[phase]?.status === 'active'
            );
            if (activePhase) {
              setExpandedPhases(new Set([activePhase]));
            }
          }
        } else {
          console.error('[useTaskDetail] No data in result or success=false:', result);
        }
      } catch (err) {
        console.error('Failed to load task logs:', err);
      } finally {
        setIsLoadingLogs(false);
      }
    };

    loadLogs();

    // Start watching for log changes
    window.API.watchTaskLogs(selectedProject.id, task.specId);

    // Listen for log changes
    const unsubscribe = window.API.onTaskLogsChanged((specId, logs) => {
      if (specId === task.specId) {
        setPhaseLogs(logs);
        // Auto-expand newly active phase (check phases exists first)
        if (logs?.phases) {
          const activePhase = (['planning', 'coding', 'validation'] as TaskLogPhase[]).find(
            phase => logs.phases?.[phase]?.status === 'active'
          );
          if (activePhase) {
            setExpandedPhases(prev => {
              const next = new Set(prev);
              next.add(activePhase);
              return next;
            });
          }
        }
      }
    });

    return () => {
      unsubscribe();
      window.API.unwatchTaskLogs(selectedProject.id, task.specId);
    };
  }, [selectedProject, task.specId]);

  // Subscribe to WebSocket events for real-time log streaming and subtask updates
  // This supplements file watching with instant WebSocket events from the backend
  useEffect(() => {
    // Subscribe to task-logs:stream events for real-time log updates
    const unsubscribeLogStream = window.API.onTaskLogsStream((specId, chunk: TaskLogStreamChunk) => {
      if (specId !== task.specId) return;

      if (window.DEBUG) {
        console.log('[useTaskDetail] WebSocket log stream received:', specId, chunk.type);
      }

      // Convert chunk to TaskLogEntry and append to the appropriate phase
      const phase = chunk.phase || 'coding';
      const entry: TaskLogEntry = {
        timestamp: chunk.timestamp || new Date().toISOString(),
        type: chunk.type || 'text',
        content: chunk.content || '',
        phase: phase,
        tool_name: chunk.tool?.name,
        tool_input: chunk.tool?.input,
        subtask_id: chunk.subtask_id,
      };

      // Update phaseLogs state with the new entry
      // This provides real-time updates via WebSocket, supplementing file-based loading
      setPhaseLogs(prev => {
        // Helper to create a properly typed empty phase log
        const createEmptyPhaseLog = (phaseName: TaskLogPhase): TaskPhaseLog => ({
          phase: phaseName,
          status: 'pending',
          started_at: null,
          completed_at: null,
          entries: [],
        });

        // Initialize phaseLogs if null (before initial load completes)
        const baseLogs: TaskLogs = prev || {
          spec_id: task.specId,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          phases: {
            planning: createEmptyPhaseLog('planning'),
            coding: createEmptyPhaseLog('coding'),
            validation: createEmptyPhaseLog('validation'),
          },
        };

        // Ensure phases object exists with all required phases
        const phases = baseLogs.phases || {
          planning: createEmptyPhaseLog('planning'),
          coding: createEmptyPhaseLog('coding'),
          validation: createEmptyPhaseLog('validation'),
        };

        const updatedPhases = { ...phases };

        // Initialize the phase if it doesn't exist yet, otherwise use existing
        const existingPhaseLog = updatedPhases[phase];
        const phaseLog: TaskPhaseLog = existingPhaseLog || createEmptyPhaseLog(phase);

        // Update phase status based on event type
        if (chunk.type === 'phase_start') {
          // Explicit phase start - mark as active
          updatedPhases[phase] = {
            ...phaseLog,
            status: 'active',
            started_at: chunk.timestamp || new Date().toISOString(),
            entries: [...phaseLog.entries, entry],
          };
        } else if (chunk.type === 'phase_end') {
          // Explicit phase end - mark as completed
          updatedPhases[phase] = {
            ...phaseLog,
            status: 'completed',
            completed_at: chunk.timestamp || new Date().toISOString(),
            entries: [...phaseLog.entries, entry],
          };
        } else if (chunk.type === 'error') {
          // Error entry - mark phase as failed if not already completed
          updatedPhases[phase] = {
            ...phaseLog,
            status: phaseLog.status === 'completed' ? 'completed' : 'failed',
            entries: [...phaseLog.entries, entry],
          };
        } else {
          // Regular log entry - append to phase
          // If phase was pending and we're receiving entries, mark it as active
          const newStatus = phaseLog.status === 'pending' ? 'active' : phaseLog.status;
          updatedPhases[phase] = {
            ...phaseLog,
            status: newStatus,
            started_at: phaseLog.status === 'pending' ? (chunk.timestamp || new Date().toISOString()) : phaseLog.started_at,
            entries: [...phaseLog.entries, entry],
          };
        }

        return {
          ...baseLogs,
          phases: updatedPhases,
          updated_at: new Date().toISOString(),
        };
      });

      // Auto-expand the phase that's receiving new logs
      // This ensures users can see new log entries as they arrive
      if (chunk.type === 'phase_start' || chunk.type === 'phase_end') {
        // Always expand on phase start/end events
        setExpandedPhases(prev => {
          const next = new Set(prev);
          next.add(phase);
          return next;
        });
      } else if (chunk.type !== 'text') {
        // Also expand for significant events (tool_start, tool_end, error)
        // but not for every text entry to avoid excessive UI updates
        setExpandedPhases(prev => {
          if (!prev.has(phase)) {
            const next = new Set(prev);
            next.add(phase);
            return next;
          }
          return prev;
        });
      }
    });

    // Subscribe to task:subtask-update events for real-time subtask status changes
    // This allows subtask checkboxes to update immediately without waiting for full sync
    const unsubscribeSubtaskUpdate = window.API.onTaskUpdate?.((data) => {
      // Handle subtask updates that come through task:update
      if (data.taskId !== task.id && data.taskId !== task.specId) return;

      if (window.DEBUG && data.subtasks) {
        console.log('[useTaskDetail] WebSocket subtask update received:', data.taskId, data.subtasks?.length, 'subtasks');
      }

      // The useIpc hook handles updating the store with updateSubtaskStatuses
      // This handler is here to ensure we can react to any additional task-specific updates
    }) ?? (() => {});

    return () => {
      unsubscribeLogStream();
      unsubscribeSubtaskUpdate();
    };
  }, [task.specId, task.id]);

  // Toggle phase expansion
  const togglePhase = useCallback((phase: TaskLogPhase) => {
    setExpandedPhases(prev => {
      const next = new Set(prev);
      if (next.has(phase)) {
        next.delete(phase);
      } else {
        next.add(phase);
      }
      return next;
    });
  }, []);

  // Track if we've already loaded preview for this task to prevent infinite loops
  const hasLoadedPreviewRef = useRef<string | null>(null);

  // Clear merge preview state when switching to a different task
  useEffect(() => {
    if (hasLoadedPreviewRef.current !== task.id) {
      setMergePreview(null);
      hasLoadedPreviewRef.current = null;
    }
  }, [task.id]);

  // Load merge preview (conflict detection)
  const loadMergePreview = useCallback(async () => {
    setIsLoadingPreview(true);
    try {
      // Use task.id (format: "project_id:spec_id") so backend can resolve the project
      const result = await window.API.mergeWorktreePreview(task.id);
      if (result.success && result.data?.preview) {
        setMergePreview(result.data.preview);
      }
    } catch (err) {
      console.error('[useTaskDetail] Failed to load merge preview:', err);
    } finally {
      hasLoadedPreviewRef.current = task.id;
      setIsLoadingPreview(false);
    }
  }, [task.id, task.specId]);

  // Resolve conflicts with AI
  // Detects if this is a git merge in progress (files with conflict markers)
  // vs a three-way merge preview, and calls the appropriate endpoint
  const resolveConflictsWithAI = useCallback(async () => {
    setIsResolvingConflicts(true);
    try {
      // Check if there's a git merge in progress with conflict markers
      const hasGitMergeInProgress = mergePreview?.gitConflicts?.mergeInProgress;

      let result;
      if (hasGitMergeInProgress) {
        // Use the new git merge conflict resolution endpoint
        // This handles files that already have <<<<<<< conflict markers
        console.log('[useTaskDetail] Using git merge conflict resolution (merge in progress)');
        result = await window.API.resolveGitMergeConflicts(task.id);
      } else {
        // Use the standard three-way merge conflict resolution
        console.log('[useTaskDetail] Using standard worktree conflict resolution');
        result = await window.API.resolveWorktreeConflicts(task.id, { useAI: true });
      }

      if (result.success && result.data) {
        console.log('[useTaskDetail] Conflict resolution result:', result.data);
        // Reset cache and reload both merge preview and worktree status
        // This ensures UI reflects the post-merge state (no conflicts, no uncommitted changes)
        hasLoadedPreviewRef.current = null;
        await Promise.all([
          loadMergePreview(),
          loadWorktreeStatus()
        ]);
      } else {
        console.error('[useTaskDetail] Conflict resolution failed:', result.error);
        setWorkspaceError(result.error || 'Failed to resolve conflicts');
      }
    } catch (err) {
      console.error('[useTaskDetail] Failed to resolve conflicts:', err);
      setWorkspaceError(err instanceof Error ? err.message : 'Failed to resolve conflicts');
    } finally {
      setIsResolvingConflicts(false);
    }
  }, [task.id, loadMergePreview, loadWorktreeStatus, mergePreview?.gitConflicts?.mergeInProgress]);

  // Abort a stuck merge
  const abortMerge = useCallback(async () => {
    setIsAbortingMerge(true);
    try {
      const result = await window.API.abortWorktreeMerge(task.id);
      if (result.success && result.data) {
        console.log('[useTaskDetail] Merge abort result:', result.data);
        // Reset cache and reload both merge preview and worktree status
        hasLoadedPreviewRef.current = null;
        await Promise.all([
          loadMergePreview(),
          loadWorktreeStatus()
        ]);
      } else {
        console.error('[useTaskDetail] Failed to abort merge:', result.error);
        setWorkspaceError(result.error || 'Failed to abort merge');
      }
    } catch (err) {
      console.error('[useTaskDetail] Failed to abort merge:', err);
      setWorkspaceError(err instanceof Error ? err.message : 'Failed to abort merge');
    } finally {
      setIsAbortingMerge(false);
    }
  }, [task.id, loadMergePreview, loadWorktreeStatus]);

  // Resolve uncommitted conflicts with AI
  const resolveUncommittedConflicts = useCallback(async () => {
    setIsResolvingUncommitted(true);
    try {
      const result = await window.API.resolveUncommittedConflicts(task.id);
      if (result.success && result.data) {
        console.log('[useTaskDetail] Uncommitted conflict resolution result:', result.data);
        // Reset cache and reload both merge preview and worktree status
        hasLoadedPreviewRef.current = null;
        await Promise.all([
          loadMergePreview(),
          loadWorktreeStatus()
        ]);
      } else {
        console.error('[useTaskDetail] Failed to resolve uncommitted conflicts:', result.error);
        setWorkspaceError(result.error || 'Failed to resolve uncommitted conflicts');
      }
    } catch (err) {
      console.error('[useTaskDetail] Failed to resolve uncommitted conflicts:', err);
      setWorkspaceError(err instanceof Error ? err.message : 'Failed to resolve uncommitted conflicts');
    } finally {
      setIsResolvingUncommitted(false);
    }
  }, [task.id, loadMergePreview, loadWorktreeStatus]);

  // Unified merge orchestrator — resolves uncommitted, git conflicts, then merges in sequence
  const unifiedMerge = useCallback(async (stageOnlyParam: boolean) => {
    setWorkspaceError(null);
    let mergeSucceeded = false;

    try {
      // Step 1: Resolve uncommitted conflicts if they exist
      const hasUncommittedConflicts = mergePreview?.uncommittedChanges?.hasConflicts;
      if (hasUncommittedConflicts) {
        setMergeStep('resolving_uncommitted');
        const result = await window.API.resolveUncommittedConflicts(task.id);
        if (!result.success || !result.data) {
          setWorkspaceError(result.error || 'Failed to resolve uncommitted conflicts');
          return { success: false };
        }
        // Refresh merge preview to get updated state
        hasLoadedPreviewRef.current = null;
        await loadMergePreview();
      }

      // Step 2: Resolve git merge conflicts if they exist
      // Only resolve when there are actual file conflicts or AI-resolvable conflicts.
      // needsRebase alone (branch behind but no conflicting files) is handled by the
      // merge endpoint itself — no need to call resolve first.
      const freshPreviewResult = await window.API.mergeWorktreePreview(task.id);
      const freshPreview = freshPreviewResult.success ? freshPreviewResult.data?.preview : null;
      const hasGitConflictsNow = freshPreview?.gitConflicts?.hasConflicts;
      const hasAIConflictsNow = freshPreview && freshPreview.conflicts && freshPreview.conflicts.length > 0;
      const hasPathMappedNow = (freshPreview?.summary?.pathMappedAIMergeCount || 0) > 0;

      if (hasGitConflictsNow || hasAIConflictsNow || hasPathMappedNow) {
        setMergeStep('resolving_git_conflicts');
        let resolveResult;
        if (freshPreview?.gitConflicts?.mergeInProgress) {
          resolveResult = await window.API.resolveGitMergeConflicts(task.id);
        } else {
          resolveResult = await window.API.resolveWorktreeConflicts(task.id, { useAI: true });
        }
        if (!resolveResult.success || !resolveResult.data) {
          setWorkspaceError(resolveResult.error || 'Failed to resolve git conflicts');
          return { success: false };
        }
      }

      // Step 3: Perform the actual merge
      setMergeStep('merging');
      const mergeResult = await window.API.mergeWorktree(task.id, { noCommit: stageOnlyParam });
      if (mergeResult.success && mergeResult.data?.success) {
        mergeSucceeded = true;
        return { success: true, data: mergeResult.data, stageOnly: stageOnlyParam };
      } else {
        const errorMsg = mergeResult.data?.message || mergeResult.error || 'Failed to merge changes';
        if (errorMsg.includes('local changes') && errorMsg.includes('would be overwritten')) {
          setWorkspaceError(
            'Your main project has uncommitted changes that conflict with this build. ' +
            'Please commit or stash your local changes before merging.'
          );
        } else {
          setWorkspaceError(errorMsg);
        }
        return { success: false };
      }
    } catch (err) {
      setWorkspaceError(err instanceof Error ? err.message : 'Unknown error during merge');
      return { success: false };
    } finally {
      setMergeStep('idle');
      // Only refresh state on failure — on success the worktree is deleted
      // and the caller (handleMerge) handles post-merge actions
      if (!mergeSucceeded) {
        hasLoadedPreviewRef.current = null;
        await Promise.all([
          loadMergePreview(),
          loadWorktreeStatus()
        ]).catch(() => { /* worktree may already be gone */ });
      }
    }
  }, [task.id, mergePreview?.uncommittedChanges?.hasConflicts, loadMergePreview, loadWorktreeStatus]);

  // Auto-load merge preview when worktree is ready (eliminates need to click "Check Conflicts")
  // NOTE: This must be placed AFTER loadMergePreview definition since it depends on that callback
  useEffect(() => {
    // Only auto-load if:
    // 1. Task needs review (skip merge preview for plan_review - no code changes to merge)
    // 2. Worktree exists
    // 3. We haven't already loaded the preview for this task
    // 4. We're not currently loading
    const isPlanReview = task.reviewReason === 'plan_review';
    const alreadyLoaded = hasLoadedPreviewRef.current === task.id;
    if (needsReview && !isPlanReview && worktreeStatus?.exists && !alreadyLoaded && !isLoadingPreview) {
      loadMergePreview();
    }
  }, [needsReview, task.reviewReason, worktreeStatus?.exists, isLoadingPreview, task.id, loadMergePreview]);

  return {
    // State
    feedback,
    isSubmitting,
    activeTab,
    isUserScrolledUp,
    isStuck,
    isRecovering,
    hasCheckedRunning,
    showDeleteDialog,
    isDeleting,
    deleteError,
    isEditDialogOpen,
    worktreeStatus,
    worktreeDiff,
    isLoadingWorktree,
    isMerging,
    isDiscarding,
    showDiscardDialog,
    workspaceError,
    showDiffDialog,
    stageOnly,
    stagedSuccess,
    stagedProjectPath,
    suggestedCommitMessage,
    phaseLogs,
    isLoadingLogs,
    expandedPhases,
    logsEndRef,
    logsContainerRef,
    selectedProject,
    isRunning,
    needsReview,
    executionPhase,
    hasActiveExecution,
    isIncomplete,
    taskProgress,
    mergePreview,
    isLoadingPreview,
    showConflictDialog,
    isAbortingMerge,
    mergeStep,

    // Setters
    setFeedback,
    setIsSubmitting,
    setActiveTab,
    setIsUserScrolledUp,
    setIsStuck,
    setIsRecovering,
    setHasCheckedRunning,
    setShowDeleteDialog,
    setIsDeleting,
    setDeleteError,
    setIsEditDialogOpen,
    setWorktreeStatus,
    setWorktreeDiff,
    setIsLoadingWorktree,
    setIsMerging,
    setIsDiscarding,
    setShowDiscardDialog,
    setWorkspaceError,
    setShowDiffDialog,
    setStageOnly,
    setStagedSuccess,
    setStagedProjectPath,
    setSuggestedCommitMessage,
    setPhaseLogs,
    setIsLoadingLogs,
    setExpandedPhases,
    setMergePreview,
    setIsLoadingPreview,
    setShowConflictDialog,
    setIsAbortingMerge,

    // Handlers
    handleLogsScroll,
    togglePhase,
    loadMergePreview,
    loadWorktreeStatus,
    abortMerge,
    unifiedMerge,
  };
}
