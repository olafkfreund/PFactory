import { AlertCircle, GitMerge, Loader2, Trash2, Check, FileText, Eye } from 'lucide-react';
import { useState } from 'react';
import { Button } from '../../ui/button';
import { persistTaskStatus } from '../../../stores/task-store';
import type { Task, TaskLogs } from '../../../shared/types';

/**
 * Helper function to check if coding phase is done and has logs
 *
 * This function determines whether to show the standard "No Workspace Found" message
 * or the friendly "Review Plan Reminder" message. We show the reminder when:
 * 1. Coding phase is NOT completed (status !== 'completed')
 * 2. OR coding phase has no log entries (entries.length === 0)
 *
 * This provides better UX by:
 * - Showing friendly guidance when no coding has happened yet
 * - Explaining what the user should do next (review the plan)
 * - Replacing the harsher "Task Incomplete" alert with helpful instructions
 *
 * @param phaseLogs - Task phase logs containing all phase information
 * @returns true if coding is completed and has log entries, false otherwise
 */
function isCodingDoneAndHasLogs(phaseLogs?: TaskLogs): boolean {
  // No logs provided - coding hasn't happened
  if (!phaseLogs) return false;

  // Check if phases object exists and has coding phase
  if (!phaseLogs.phases || !phaseLogs.phases.coding) return false;

  const codingPhase = phaseLogs.phases.coding;

  // Check if coding phase is marked as completed
  const isCodingCompleted = codingPhase.status === 'completed';

  // Check if coding actually produced log entries (real work was done)
  const hasLogEntries = codingPhase.entries.length > 0;

  // Both conditions must be true to show standard message
  return isCodingCompleted && hasLogEntries;
}

interface LoadingMessageProps {
  message?: string;
}

/**
 * Displays a loading indicator while workspace info is being fetched
 */
export function LoadingMessage({ message = 'Loading workspace info...' }: LoadingMessageProps) {
  return (
    <div className="rounded-xl border border-border bg-secondary/30 p-4">
      <div className="flex items-center gap-2 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span className="text-sm">{message}</span>
      </div>
    </div>
  );
}

interface NoWorkspaceMessageProps {
  task?: Task;
  phaseLogs?: TaskLogs;
  onClose?: () => void;
}

/**
 * Displays message when no workspace is found for the task
 * Shows ReviewPlanReminder when coding is not done or logs are empty
 */
export function NoWorkspaceMessage({ task, phaseLogs, onClose }: NoWorkspaceMessageProps) {
  const [isMarkingDone, setIsMarkingDone] = useState(false);

  const handleMarkDone = async () => {
    if (!task) return;

    setIsMarkingDone(true);
    try {
      await persistTaskStatus(task.id, 'done');
      // Auto-close modal after marking as done
      onClose?.();
    } catch (err) {
      console.error('Error marking task as done:', err);
    } finally {
      setIsMarkingDone(false);
    }
  };

  // Conditional rendering based on coding status
  //
  // Show ReviewPlanReminder (friendly blue informational message) when:
  // - Coding phase is not completed (status !== 'completed')
  // - OR no coding logs exist (entries.length === 0)
  //
  // This replaces the old "Task Incomplete" alert with helpful guidance,
  // directing users to review the implementation plan first before expecting
  // code changes. This improves UX by setting clear expectations and reducing
  // confusion about why there's no code to review yet.
  if (task && !isCodingDoneAndHasLogs(phaseLogs)) {
    return <ReviewPlanReminder task={task} />;
  }

  // Show standard "No Workspace Found" message when:
  // - Coding IS completed AND has logs
  // - But no worktree exists (changes may have been made directly in project)
  //
  // This case handles the scenario where work was done but not in an isolated workspace
  return (
    <div className="rounded-xl border border-border bg-secondary/30 p-4">
      <h3 className="font-medium text-sm text-foreground mb-2 flex items-center gap-2">
        <AlertCircle className="h-4 w-4 text-muted-foreground" />
        No Workspace Found
      </h3>
      <p className="text-sm text-muted-foreground mb-3">
        No isolated workspace was found for this task. The changes may have been made directly in your project.
      </p>

      {/* Allow marking as done */}
      {task && task.status === 'human_review' && (
        <Button
          onClick={handleMarkDone}
          disabled={isMarkingDone}
          size="sm"
          variant="default"
          className="w-full"
        >
          {isMarkingDone ? (
            <>
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              Updating...
            </>
          ) : (
            <>
              <Check className="h-4 w-4 mr-2" />
              Mark as Done
            </>
          )}
        </Button>
      )}
    </div>
  );
}

interface StagedInProjectMessageProps {
  task: Task;
  projectPath?: string;
  hasWorktree?: boolean;
  onClose?: () => void;
}

/**
 * Displays message when changes have already been staged in the main project
 */
export function StagedInProjectMessage({ task, projectPath, hasWorktree = false, onClose }: StagedInProjectMessageProps) {
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDeleteWorktreeAndMarkDone = async () => {
    setIsDeleting(true);
    setError(null);

    try {
      // Call the discard/delete worktree command
      const result = await window.API.discardWorktree(task.id);

      if (!result.success) {
        setError(result.error || 'Failed to delete worktree');
        return;
      }

      // Mark task as done
      await persistTaskStatus(task.id, 'done');

      // Auto-close modal after marking as done
      onClose?.();
    } catch (err) {
      console.error('Error deleting worktree:', err);
      setError(err instanceof Error ? err.message : 'Failed to delete worktree');
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div className="rounded-xl border border-success/30 bg-success/10 p-4">
      <h3 className="font-medium text-sm text-foreground mb-2 flex items-center gap-2">
        <GitMerge className="h-4 w-4 text-success" />
        Changes Staged in Project
      </h3>
      <p className="text-sm text-muted-foreground mb-3">
        This task's changes have been staged in your main project{task.stagedAt ? ` on ${new Date(task.stagedAt).toLocaleDateString()}` : ''}.
      </p>
      <div className="bg-background/50 rounded-lg p-3 mb-3">
        <p className="text-xs text-muted-foreground mb-2">Next steps:</p>
        <ol className="text-xs text-muted-foreground space-y-1 list-decimal list-inside">
          <li>Review staged changes with <code className="bg-background px-1 rounded">git status</code> and <code className="bg-background px-1 rounded">git diff --staged</code></li>
          <li>Commit when ready: <code className="bg-background px-1 rounded">git commit -m "your message"</code></li>
          <li>Push to remote when satisfied</li>
        </ol>
      </div>

      {/* Action buttons */}
      {hasWorktree && (
        <div className="flex flex-col gap-2">
          <div className="flex gap-2">
            <Button
              onClick={handleDeleteWorktreeAndMarkDone}
              disabled={isDeleting}
              size="sm"
              variant="default"
              className="flex-1"
            >
              {isDeleting ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Cleaning up...
                </>
              ) : (
                <>
                  <Check className="h-4 w-4 mr-2" />
                  Delete Worktree & Mark Done
                </>
              )}
            </Button>
          </div>
          {error && (
            <p className="text-xs text-destructive">{error}</p>
          )}
          <p className="text-xs text-muted-foreground">
            This will delete the isolated workspace and mark the task as complete.
          </p>
        </div>
      )}
    </div>
  );
}

interface ReviewPlanReminderProps {
  task: Task;
}

/**
 * Displays a friendly reminder to review the implementation plan when coding hasn't started
 */
export function ReviewPlanReminder({ task }: ReviewPlanReminderProps) {
  return (
    <div className="rounded-xl border border-blue-500/30 bg-blue-500/10 p-4">
      <h3 className="font-medium text-sm text-foreground mb-2 flex items-center gap-2">
        <FileText className="h-4 w-4 text-blue-400" />
        Review Implementation Plan
      </h3>
      <p className="text-sm text-muted-foreground mb-3">
        This task has an implementation plan ready for review. Once you approve the plan,
        coding will begin and you'll be able to review and merge the changes here.
      </p>
      <div className="bg-background/50 rounded-lg p-3 mb-3">
        <p className="text-xs text-muted-foreground mb-2">What's next:</p>
        <ol className="text-xs text-muted-foreground space-y-1 list-decimal list-inside">
          <li>Review the implementation plan in the "Plan" tab above</li>
          <li>Check that the approach aligns with your expectations</li>
          <li>Approve the plan to start the implementation</li>
          <li>Come back here to review and merge the completed work</li>
        </ol>
      </div>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Eye className="h-3.5 w-3.5" />
        <span>No code changes yet - waiting for plan approval</span>
      </div>
    </div>
  );
}
