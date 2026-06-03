import { useEffect } from 'react';
import { unstable_batchedUpdates } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { useTaskStore } from '../stores/task-store';
import { useRateLimitStore } from '../stores/rate-limit-store';
import { toast } from './use-toast';
import type { ImplementationPlan, TaskStatus, ReviewReason, ExecutionProgress, ExecutionPhase, RateLimitInfo, SDKRateLimitInfo } from '../shared/types';

/**
 * Batched update queue for IPC events.
 *
 * BATCHING STRATEGY:
 * - Critical updates (status, subtask changes): Immediate flush for responsiveness
 * - High-frequency updates (logs, progress ticks): 16ms batch window to reduce re-renders
 *
 * This provides the best of both worlds:
 * - Status changes appear instantly in the UI
 * - Log and progress updates are coalesced to prevent 100+ updates/sec
 */
interface BatchedUpdate {
  status?: TaskStatus;
  reviewReason?: ReviewReason;
  progress?: ExecutionProgress;
  plan?: ImplementationPlan;
  logs?: string[]; // Batched log lines
  queuedAt?: number; // For debug timing
  hasCriticalUpdate?: boolean; // Flag for immediate flush
}

/**
 * Store action references type for batch flushing.
 */
interface StoreActions {
  updateTaskStatus: (taskId: string, status: TaskStatus, reviewReason?: ReviewReason) => void;
  updateExecutionProgress: (taskId: string, progress: ExecutionProgress) => void;
  updateTaskFromPlan: (taskId: string, plan: ImplementationPlan) => void;
  batchAppendLogs: (taskId: string, logs: string[]) => void;
}

/**
 * Module-level batch state.
 *
 * DESIGN NOTE: These module-level variables are intentionally shared across all hook instances.
 * This is acceptable because:
 * 1. There's only one Zustand store instance (singleton pattern)
 * 2. The app has a single main window that uses this hook
 * 3. Batching IPC updates at module level ensures all events within a frame are coalesced
 *
 * The storeActionsRef pattern ensures we always have the latest action references when
 * flushing, avoiding stale closure issues from component re-renders.
 */
const batchQueue = new Map<string, BatchedUpdate>();
let batchTimeout: NodeJS.Timeout | null = null;
let storeActionsRef: StoreActions | null = null;

function flushBatch(): void {
  if (batchQueue.size === 0 || !storeActionsRef) return;

  const flushStart = performance.now();
  const updateCount = batchQueue.size;
  let totalUpdates = 0;
  let totalLogs = 0;
  let hadCriticalUpdates = false;

  // Capture current actions reference to avoid stale closures during batch processing
  const actions = storeActionsRef;

  // Check for critical updates before processing
  batchQueue.forEach(update => {
    if (update.hasCriticalUpdate) hadCriticalUpdates = true;
  });

  // Batch all React updates together
  unstable_batchedUpdates(() => {
    batchQueue.forEach((updates, taskId) => {
      // Apply updates in order: plan first (has most data), then status, then progress, then logs
      if (updates.plan) {
        actions.updateTaskFromPlan(taskId, updates.plan);
        totalUpdates++;
      }
      if (updates.status) {
        actions.updateTaskStatus(taskId, updates.status, updates.reviewReason);
        totalUpdates++;
      }
      if (updates.progress) {
        actions.updateExecutionProgress(taskId, updates.progress);
        totalUpdates++;
      }
      // Batch append all logs at once (instead of one state update per log line)
      if (updates.logs && updates.logs.length > 0) {
        actions.batchAppendLogs(taskId, updates.logs);
        totalLogs += updates.logs.length;
        totalUpdates++;
      }
    });
  });

  if (window.DEBUG) {
    const flushDuration = performance.now() - flushStart;
    console.log(`[useIpc Batch] Flushed ${totalUpdates} updates (${totalLogs} logs) for ${updateCount} tasks in ${flushDuration.toFixed(2)}ms${hadCriticalUpdates ? ' [CRITICAL]' : ''}`);
  }

  batchQueue.clear();
  batchTimeout = null;
}

/**
 * Queue an update for batched processing.
 *
 * @param taskId - The task ID to update
 * @param update - The update to queue
 * @param options - Options for queue behavior
 * @param options.immediate - If true, flushes immediately (for critical updates like status changes)
 */
function queueUpdate(taskId: string, update: BatchedUpdate, options?: { immediate?: boolean }): void {
  const existing = batchQueue.get(taskId) || {};

  // For logs, accumulate rather than replace
  let mergedLogs = existing.logs;
  if (update.logs) {
    mergedLogs = [...(existing.logs || []), ...update.logs];
  }

  // Track if any critical update is in the queue
  const hasCriticalUpdate = existing.hasCriticalUpdate || options?.immediate || false;

  batchQueue.set(taskId, {
    ...existing,
    ...update,
    logs: mergedLogs,
    queuedAt: existing.queuedAt || performance.now(),
    hasCriticalUpdate
  });

  // Critical updates: flush immediately via microtask for maximum responsiveness
  // This ensures status changes appear in the UI within the same frame
  if (options?.immediate) {
    if (batchTimeout) {
      clearTimeout(batchTimeout);
      batchTimeout = null;
    }
    // Use queueMicrotask for immediate but non-blocking flush
    queueMicrotask(flushBatch);
    return;
  }

  // Non-critical updates: schedule flush after 16ms (one frame at 60fps)
  if (!batchTimeout) {
    batchTimeout = setTimeout(flushBatch, 16);
  }
}

/**
 * Hook to set up IPC event listeners for task updates
 */
export function useIpcListeners(): void {
  const { t } = useTranslation('tasks');
  const updateTaskFromPlan = useTaskStore((state) => state.updateTaskFromPlan);
  const updateTaskStatus = useTaskStore((state) => state.updateTaskStatus);
  const updateExecutionProgress = useTaskStore((state) => state.updateExecutionProgress);
  const appendLog = useTaskStore((state) => state.appendLog);
  const batchAppendLogs = useTaskStore((state) => state.batchAppendLogs);
  const setError = useTaskStore((state) => state.setError);

  // Update module-level store actions reference for batch flushing
  // This ensures flushBatch() always has access to current action implementations
  storeActionsRef = { updateTaskStatus, updateExecutionProgress, updateTaskFromPlan, batchAppendLogs };

  useEffect(() => {
    // Set up listeners with batched updates
    const cleanupProgress = window.API.onTaskProgress(
      (taskId: string, plan: ImplementationPlan) => {
        if (window.DEBUG) {
          console.log('[useIpc] task:progress received:', taskId, 'phases:', plan?.phases?.length ?? 0);
        }
        queueUpdate(taskId, { plan });
      }
    );

    const cleanupError = window.API.onTaskError(
      (taskId: string, error: string) => {
        if (window.DEBUG) {
          console.log('[useIpc] task:error received:', taskId, 'error:', error.substring(0, 100));
        }
        // Errors are not batched - show immediately
        setError(`Task ${taskId}: ${error}`);
        appendLog(taskId, `[ERROR] ${error}`);
      }
    );

    const cleanupLog = window.API.onTaskLog(
      (taskId: string, log: string) => {
        // Logs are now batched to reduce state updates (was causing 100+ updates/sec)
        // Debug log only first 30 chars to avoid console flood
        if (window.DEBUG) {
          console.debug('[useIpc] task:log received:', taskId, 'log:', log.substring(0, 30).replace(/\n/g, '\\n'));
        }
        queueUpdate(taskId, { logs: [log] });
      }
    );

    const cleanupStatus = window.API.onTaskStatusChange(
      (taskId: string, status: TaskStatus, reviewReason?: string) => {
        if (window.DEBUG) {
          console.log('[useIpc] Status event received:', taskId, 'status:', status, 'reviewReason:', reviewReason);
        }
        // Queue the status update with immediate flush for maximum responsiveness
        // Status changes are critical UX updates that should appear instantly
        queueUpdate(
          taskId,
          { status, reviewReason: reviewReason as ReviewReason | undefined },
          { immediate: true }
        );

        // For terminal statuses, also queue a progress update to set phase to 'complete'
        // This ensures phase badges update in real-time without page refresh
        // Uses immediate flush since it accompanies a critical status change
        if (status === 'done') {
          queueUpdate(
            taskId,
            {
              progress: {
                phase: 'complete' as ExecutionPhase,
                phaseProgress: 100,
                overallProgress: 100
              }
            },
            { immediate: true }
          );
        }
      }
    );

    const cleanupExecutionProgress = window.API.onTaskExecutionProgress(
      (taskId: string, progress: ExecutionProgress) => {
        if (window.DEBUG) {
          console.log('[useIpc] task:executionProgress received:', taskId, 'phase:', progress?.phase, 'progress:', progress?.phaseProgress);
        }
        // Phase changes are critical (planning → coding → complete transitions)
        // Progress ticks within a phase can be batched
        const isPhaseChange = progress?.phase !== undefined;
        queueUpdate(taskId, { progress }, { immediate: isPhaseChange });
      }
    );

    // Task update listener (real-time updates from backend)
    const cleanupTaskUpdate = window.API.onTaskUpdate?.(
      (data: { taskId: string; executionProgress?: ExecutionProgress; phase?: string; subtasksCompleted?: number; subtasksTotal?: number; subtasks?: { id: string; status: string }[] }) => {
        // Debug logging for task updates
        if (window.DEBUG) {
          console.log('[useIpc] Task update received:', {
            taskId: data.taskId,
            phase: data.phase,
            executionProgress: data.executionProgress,
            subtasksCompleted: data.subtasksCompleted,
            subtasksTotal: data.subtasksTotal,
            subtasksCount: data.subtasks?.length,
          });
        }
        // Check if this update contains important changes that should flush immediately
        const hasSubtaskChanges = data.subtasks && data.subtasks.length > 0;
        const hasPhaseChange = data.executionProgress?.phase !== undefined || data.phase !== undefined;
        const shouldFlushImmediately = hasSubtaskChanges || hasPhaseChange;

        if (data.executionProgress) {
          queueUpdate(data.taskId, { progress: data.executionProgress }, { immediate: shouldFlushImmediately });
        }
        // Update subtask statuses if provided (real-time subtask progress)
        // This bypasses the batch queue for direct store update (already immediate)
        if (hasSubtaskChanges) {
          useTaskStore.getState().updateSubtaskStatuses(data.taskId, data.subtasks!);
        }
      }
    ) || (() => {});

    // Individual subtask update listener (granular real-time updates)
    // This event is emitted when a single subtask's status changes, providing more immediate feedback
    const cleanupSubtaskUpdate = window.API.onTaskSubtaskUpdate?.(
      (taskId: string, subtaskId: string, status: string, previousStatus?: string) => {
        if (window.DEBUG) {
          console.log('[useIpc] Subtask update received:', {
            taskId,
            subtaskId,
            status,
            previousStatus,
          });
        }
        // Use the efficient single subtask update method
        useTaskStore.getState().updateSingleSubtaskStatus(taskId, subtaskId, status);
      }
    ) || (() => {});

    // Terminal rate limit listener
    const showRateLimitModal = useRateLimitStore.getState().showRateLimitModal;
    const cleanupRateLimit = window.API.onTerminalRateLimit(
      (info: RateLimitInfo) => {
        // Convert detectedAt string to Date if needed
        showRateLimitModal({
          ...info,
          detectedAt: typeof info.detectedAt === 'string'
            ? new Date(info.detectedAt)
            : info.detectedAt
        });
      }
    );

    // SDK rate limit listener (for changelog, tasks)
    const showSDKRateLimitModal = useRateLimitStore.getState().showSDKRateLimitModal;
    const cleanupSDKRateLimit = window.API.onSDKRateLimit(
      (info: SDKRateLimitInfo) => {
        // Convert detectedAt string to Date if needed
        showSDKRateLimitModal({
          ...info,
          detectedAt: typeof info.detectedAt === 'string'
            ? new Date(info.detectedAt)
            : info.detectedAt
        });
      }
    );

    // Task profile switching (reactive recovery)
    const cleanupProfileSwitch = window.API.onTaskProfileSwitch?.((taskId, info) => {
      const profileName = info.newProfileName || info.newProfileId || '';
      const reasonKey =
        info.reason === 'rate_limit' ? 'rateLimit'
          : info.reason === 'early_failure' ? 'earlyFailure'
            : 'unknown';

      toast({
        title: t('notifications.profileSwitch.title'),
        description: t(`notifications.profileSwitch.description.${reasonKey}`, { profileName, taskId }),
      });
    }) || (() => {});

    // Cleanup on unmount
    return () => {
      // Flush any pending batched updates before cleanup
      if (batchTimeout) {
        clearTimeout(batchTimeout);
        flushBatch();
        batchTimeout = null;
      }
      cleanupProgress();
      cleanupError();
      cleanupLog();
      cleanupStatus();
      cleanupExecutionProgress();
      cleanupTaskUpdate();
      cleanupSubtaskUpdate();
      cleanupRateLimit();
      cleanupSDKRateLimit();
      cleanupProfileSwitch();
    };
  }, [updateTaskFromPlan, updateTaskStatus, updateExecutionProgress, appendLog, batchAppendLogs, setError, t]);
}

/**
 * Hook to manage app settings
 */
export function useAppSettings() {
  const getSettings = async () => {
    const result = await window.API.getSettings();
    if (result.success && result.data) {
      return result.data;
    }
    return null;
  };

  const saveSettings = async (settings: Parameters<typeof window.API.saveSettings>[0]) => {
    const result = await window.API.saveSettings(settings);
    return result.success;
  };

  return { getSettings, saveSettings };
}

/**
 * Hook to get the app version
 */
export function useAppVersion() {
  const getVersion = async () => {
    return window.API.getAppVersion();
  };

  return { getVersion };
}
