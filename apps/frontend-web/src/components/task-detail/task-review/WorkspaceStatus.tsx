import {
  GitBranch,
  FileCode,
  Plus,
  Minus,
  Eye,
  GitMerge,
  GitPullRequestCreateArrow,
  FolderX,
  Loader2,
  RotateCcw,
  AlertTriangle,
  CheckCircle,
  GitCommit,
  Code,
  Terminal,
  XCircle
} from 'lucide-react';
import { Button } from '../../ui/button';
import { Checkbox } from '../../ui/checkbox';
import { cn, isWebMode } from '../../../lib/utils';
import type { WorktreeStatus, MergeConflict, MergeStats, GitConflictInfo, SupportedIDE, SupportedTerminal, Task, TaskLogs } from '../../../shared/types';
import { useSettingsStore } from '../../../stores/settings-store';

interface WorkspaceStatusProps {
  task: Task;
  worktreeStatus: WorktreeStatus;
  workspaceError: string | null;
  stageOnly: boolean;
  mergePreview: { files: string[]; conflicts: MergeConflict[]; summary: MergeStats; gitConflicts?: GitConflictInfo; uncommittedChanges?: { hasChanges: boolean; files: string[]; count: number; conflictingFiles?: string[]; hasConflicts?: boolean } | null } | null;
  isLoadingPreview: boolean;
  isMerging: boolean;
  isDiscarding: boolean;
  isAbortingMerge?: boolean;
  mergeStep: 'idle' | 'resolving_uncommitted' | 'resolving_git_conflicts' | 'merging';
  phaseLogs?: TaskLogs;
  onShowDiffDialog: (show: boolean) => void;
  onShowDiscardDialog: (show: boolean) => void;
  onShowConflictDialog: (show: boolean) => void;
  onLoadMergePreview: () => void;
  onStageOnlyChange: (value: boolean) => void;
  onMerge: () => void;
  onCreatePR?: () => void;
  isCreatingPR?: boolean;
  onAbortMerge?: () => void;
  onClose?: () => void;
  onSwitchToTerminals?: () => void;
  onOpenInbuiltTerminal?: (id: string, cwd: string) => void;
}

/**
 * Displays the workspace status including change summary, merge preview, and action buttons
 */
// IDE display names for button labels (short names for buttons)
const IDE_LABELS: Partial<Record<SupportedIDE, string>> = {
  vscode: 'VS Code',
  cursor: 'Cursor',
  windsurf: 'Windsurf',
  zed: 'Zed',
  sublime: 'Sublime',
  webstorm: 'WebStorm',
  intellij: 'IntelliJ',
  pycharm: 'PyCharm',
  xcode: 'Xcode',
  vim: 'Vim',
  neovim: 'Neovim',
  emacs: 'Emacs',
  custom: 'IDE'
};

// Terminal display names for button labels (short names for buttons)
const TERMINAL_LABELS: Partial<Record<SupportedTerminal, string>> = {
  system: 'Terminal',
  terminal: 'Terminal',
  iterm2: 'iTerm',
  warp: 'Warp',
  ghostty: 'Ghostty',
  alacritty: 'Alacritty',
  kitty: 'Kitty',
  wezterm: 'WezTerm',
  hyper: 'Hyper',
  windowsterminal: 'Terminal',
  gnometerminal: 'Terminal',
  konsole: 'Konsole',
  custom: 'Terminal'
};

/**
 * Helper function to check if coding phase is done and has logs
 *
 * This function determines whether the "Build Ready for Review" section and
 * merge button should be displayed in the UI. We only show these elements when:
 * 1. The coding phase has been completed (status === 'completed')
 * 2. The coding phase has generated log entries (entries.length > 0)
 *
 * This prevents showing merge options when:
 * - Coding hasn't started yet (status === 'pending')
 * - Coding is currently in progress (status === 'active')
 * - Coding failed (status === 'failed')
 * - No actual coding work was done (empty logs)
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

  // Both conditions must be true to show merge UI
  return isCodingCompleted && hasLogEntries;
}

export function WorkspaceStatus({
  task,
  worktreeStatus,
  workspaceError,
  stageOnly,
  mergePreview,
  isLoadingPreview,
  isMerging,
  isDiscarding,
  isAbortingMerge = false,
  mergeStep,
  phaseLogs,
  onShowDiffDialog,
  onShowDiscardDialog,
  onShowConflictDialog,
  onLoadMergePreview,
  onStageOnlyChange,
  onMerge,
  onCreatePR,
  isCreatingPR = false,
  onAbortMerge,
  onClose,
  onSwitchToTerminals,
  onOpenInbuiltTerminal
}: WorkspaceStatusProps) {
  const { settings } = useSettingsStore();
  const preferredIDE = settings.preferredIDE || 'vscode';
  const preferredTerminal = settings.preferredTerminal || 'system';

  // Determine if merge button should be shown based on task state and coding status
  //
  // The merge button is shown when:
  // 1. reviewReason === 'completed' - Task explicitly marked as completed
  // 2. OR worktree has changes (filesChanged > 0) and task is in human_review with no plan_review reason
  //    This handles edge cases where phase tracking wasn't properly updated
  // 3. OR isCodingDoneAndHasLogs(phaseLogs) - Coding phase completed with log entries
  //
  // The merge button is hidden when:
  // 1. task.reviewReason === 'plan_review' - Task is in plan review phase, no code written yet
  //
  // This ensures users can merge when there's completed work, even if phase tracking failed
  const hasWorkToMerge =
    task.reviewReason === 'completed' ||
    (worktreeStatus.filesChanged && worktreeStatus.filesChanged > 0 && task.reviewReason !== 'plan_review') ||
    isCodingDoneAndHasLogs(phaseLogs);
  const shouldShowMergeButton = task.reviewReason !== 'plan_review' && hasWorkToMerge;

  const handleOpenInIDE = async () => {
    if (!worktreeStatus.worktreePath) return;
    try {
      await window.API.worktreeOpenInIDE(
        worktreeStatus.worktreePath,
        preferredIDE,
        settings.customIDEPath
      );
    } catch (err) {
      console.error('Failed to open in IDE:', err);
    }
  };

  const handleOpenInTerminal = async () => {
    if (!worktreeStatus.worktreePath) return;

    // In web mode, use the inbuilt terminal instead of external terminal
    if (isWebMode()) {
      if (onOpenInbuiltTerminal) {
        // Generate a unique terminal ID based on the worktree branch
        const terminalId = `worktree-${worktreeStatus.branch || 'default'}-${Date.now()}`;
        onOpenInbuiltTerminal(terminalId, worktreeStatus.worktreePath);
      } else if (onSwitchToTerminals) {
        // Fallback: switch to terminals tab if no inbuilt terminal callback
        onSwitchToTerminals();
      }
      return;
    }

    try {
      await window.API.worktreeOpenInTerminal(
        worktreeStatus.worktreePath,
        preferredTerminal,
        settings.customTerminalPath
      );
    } catch (err) {
      console.error('Failed to open in terminal:', err);
    }
  };

  const hasGitConflicts = mergePreview?.gitConflicts?.hasConflicts;
  const hasUncommittedChanges = mergePreview?.uncommittedChanges?.hasChanges;
  const uncommittedCount = mergePreview?.uncommittedChanges?.count || 0;
  const hasAIConflicts = mergePreview && mergePreview.conflicts.length > 0;

  // Check if branch needs rebase (main has advanced since spec was created)
  // This requires AI merge even if no explicit file conflicts are detected
  const needsRebase = mergePreview?.gitConflicts?.needsRebase;
  const commitsBehind = mergePreview?.gitConflicts?.commitsBehind || 0;

  // Path-mapped files that need AI merge due to file renames
  const pathMappedAIMergeCount = mergePreview?.summary?.pathMappedAIMergeCount || 0;
  const totalRenames = mergePreview?.gitConflicts?.totalRenames || 0;

  // Branch is behind if needsRebase is true and there are commits to catch up on
  // This triggers AI merge for path-mapped files even without explicit conflicts
  const isBranchBehind = needsRebase && commitsBehind > 0;

  // Has path-mapped files that need AI merge
  const hasPathMappedMerges = pathMappedAIMergeCount > 0;

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      {/*
        Build Ready for Review Header Section

        Conditionally rendered based on coding status. This entire section is hidden when:
        - Coding phase is not completed (status !== 'completed')
        - No coding logs exist (entries.length === 0)

        When hidden, users will see the ReviewPlanReminder instead (shown in NoWorkspaceMessage),
        which provides friendly guidance about reviewing the implementation plan first.

        This prevents showing build statistics, file changes, and IDE/Terminal buttons
        when there's no actual code to review yet.
      */}
      {isCodingDoneAndHasLogs(phaseLogs) && (
      <div className="px-4 py-3 bg-muted/30 border-b border-border">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-medium text-sm text-foreground flex items-center gap-2">
            <GitBranch className="h-4 w-4 text-purple-400" />
            Build Ready for Review
          </h3>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onShowDiffDialog(true)}
            className="h-7 px-2 text-xs"
          >
            <Eye className="h-3.5 w-3.5 mr-1" />
            View
          </Button>
        </div>

        {/* Compact stats row */}
        <div className="flex items-center gap-4 text-xs">
          <span className="flex items-center gap-1.5 text-muted-foreground">
            <FileCode className="h-3.5 w-3.5" />
            <span className="font-medium text-foreground">{worktreeStatus.filesChanged || 0}</span> files
          </span>
          <span className="flex items-center gap-1.5 text-muted-foreground">
            <GitCommit className="h-3.5 w-3.5" />
            <span className="font-medium text-foreground">{worktreeStatus.commitCount || 0}</span> commits
          </span>
          <span className="flex items-center gap-1 text-success">
            <Plus className="h-3.5 w-3.5" />
            <span className="font-medium">{worktreeStatus.additions || 0}</span>
          </span>
          <span className="flex items-center gap-1 text-destructive">
            <Minus className="h-3.5 w-3.5" />
            <span className="font-medium">{worktreeStatus.deletions || 0}</span>
          </span>
        </div>

        {/* Branch info */}
        {worktreeStatus.branch && (
          <div className="mt-2 text-base text-muted-foreground">
            <code className="bg-background/80 px-2 py-1 rounded text-sm">{worktreeStatus.branch}</code>
            <span className="mx-2">→</span>
            <code className="bg-background/80 px-2 py-1 rounded text-sm">{worktreeStatus.baseBranch || 'main'}</code>
          </div>
        )}

        {/* Worktree path display */}
        {worktreeStatus.worktreePath && (
          <div className="mt-2 text-xs text-muted-foreground font-mono">
            📁 {worktreeStatus.worktreePath}
          </div>
        )}

        {/* Open in IDE/Terminal buttons */}
        {worktreeStatus.worktreePath && (
          <div className="flex gap-2 mt-3">
            {/* IDE button - disabled in web mode since external IDEs cannot be launched */}
            {!isWebMode() && (
              <Button
                variant="outline"
                size="sm"
                onClick={handleOpenInIDE}
                className="h-7 px-2 text-xs"
              >
                <Code className="h-3.5 w-3.5 mr-1" />
                Open in {IDE_LABELS[preferredIDE]}
              </Button>
            )}
            {/* Terminal button - uses inbuilt terminal in web mode */}
            <Button
              variant="outline"
              size="sm"
              onClick={handleOpenInTerminal}
              className="h-7 px-2 text-xs"
            >
              <Terminal className="h-3.5 w-3.5 mr-1" />
              {isWebMode() ? 'Open Terminal' : `Open in ${TERMINAL_LABELS[preferredTerminal]}`}
            </Button>
          </div>
        )}
      </div>
      )}

      {/* Status/Warnings Section */}
      <div className="px-4 py-3 space-y-3">
        {/* Workspace Error */}
        {workspaceError && (
          <div className="flex items-start gap-2 p-2.5 rounded-lg bg-destructive/10 border border-destructive/20">
            <AlertTriangle className="h-4 w-4 text-destructive mt-0.5 flex-shrink-0" />
            <p className="text-sm text-destructive">{workspaceError}</p>
          </div>
        )}

        {/* Uncommitted Changes Warning - conflict-aware */}
        {hasUncommittedChanges && (
          <div className="flex items-start justify-between gap-2 p-2.5 rounded-lg bg-warning/10 border border-warning/20">
            <div className="flex items-start gap-2 flex-1 min-w-0">
              <AlertTriangle className="h-4 w-4 text-warning mt-0.5 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-warning">
                  {mergePreview?.uncommittedChanges?.hasConflicts
                    ? `${mergePreview.uncommittedChanges.conflictingFiles?.length} file${(mergePreview.uncommittedChanges.conflictingFiles?.length || 0) !== 1 ? 's' : ''} conflict with this task`
                    : `${uncommittedCount} uncommitted ${uncommittedCount === 1 ? 'change' : 'changes'} in main project`
                  }
                </p>
                {mergePreview?.uncommittedChanges?.hasConflicts && mergePreview.uncommittedChanges.conflictingFiles && mergePreview.uncommittedChanges.conflictingFiles.length > 0 ? (
                  <p className="text-xs text-muted-foreground mt-0.5 truncate">
                    {mergePreview.uncommittedChanges.conflictingFiles.slice(0, 3).join(', ')}
                    {mergePreview.uncommittedChanges.conflictingFiles.length > 3 && ` +${mergePreview.uncommittedChanges.conflictingFiles.length - 3} more`}
                  </p>
                ) : (
                  <p className="text-xs text-muted-foreground mt-0.5">
                    Commit or stash them in your terminal before staging to avoid conflicts.
                  </p>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Loading indicator */}
        {isLoadingPreview && !mergePreview && (
          <div className="flex items-center gap-2 text-muted-foreground text-sm py-2">
            <Loader2 className="h-4 w-4 animate-spin" />
            Checking for conflicts...
          </div>
        )}

        {/* Merge Status */}
        {mergePreview && (
          <div className={cn(
            "flex items-center justify-between p-2.5 rounded-lg border",
            hasGitConflicts || isBranchBehind || hasPathMappedMerges
              ? "bg-warning/10 border-warning/20"
              : !hasAIConflicts
                ? "bg-success/10 border-success/20"
                : "bg-warning/10 border-warning/20"
          )}>
            <div className="flex items-center gap-2">
              {hasGitConflicts ? (
                <>
                  <AlertTriangle className="h-4 w-4 text-warning" />
                  <div>
                    <span className="text-sm font-medium text-warning">Branch Diverged</span>
                    <span className="text-xs text-muted-foreground ml-2">AI will resolve</span>
                  </div>
                </>
              ) : isBranchBehind || hasPathMappedMerges ? (
                <>
                  <AlertTriangle className="h-4 w-4 text-warning" />
                  <div>
                    <span className="text-sm font-medium text-warning">
                      {hasPathMappedMerges ? 'Files Renamed' : 'Branch Behind'}
                    </span>
                    <span className="text-xs text-muted-foreground ml-2">
                      AI will resolve ({hasPathMappedMerges ? `${pathMappedAIMergeCount} files` : `${commitsBehind} commits`})
                    </span>
                  </div>
                </>
              ) : !hasAIConflicts ? (
                <>
                  <CheckCircle className="h-4 w-4 text-success" />
                  <span className="text-sm font-medium text-success">Ready to merge</span>
                  <span className="text-xs text-muted-foreground ml-1">
                    {mergePreview.summary.totalFiles} files
                  </span>
                </>
              ) : (
                <>
                  <AlertTriangle className="h-4 w-4 text-warning" />
                  <span className="text-sm font-medium text-warning">
                    {mergePreview.conflicts.length} conflict{mergePreview.conflicts.length !== 1 ? 's' : ''}
                  </span>
                </>
              )}
            </div>
            <div className="flex items-center gap-1">
              {(hasGitConflicts || isBranchBehind || hasPathMappedMerges || hasAIConflicts) && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => onShowConflictDialog(true)}
                  className="h-7 text-xs"
                >
                  Details
                </Button>
              )}
              <Button
                variant="ghost"
                size="sm"
                onClick={onLoadMergePreview}
                disabled={isLoadingPreview}
                className="h-7 px-2"
                title="Refresh"
              >
                {isLoadingPreview ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <RotateCcw className="h-3.5 w-3.5" />
                )}
              </Button>
            </div>
          </div>
        )}

        {/* Git Conflicts Details */}
        {hasGitConflicts && mergePreview?.gitConflicts && (
          <div className="text-xs text-muted-foreground pl-6">
            Main branch has {mergePreview.gitConflicts.commitsBehind} new commit{mergePreview.gitConflicts.commitsBehind !== 1 ? 's' : ''}.
            {mergePreview.gitConflicts.conflictingFiles.length > 0 && (
              <span className="text-warning">
                {' '}{mergePreview.gitConflicts.conflictingFiles.length} file{mergePreview.gitConflicts.conflictingFiles.length !== 1 ? 's' : ''} need merging.
              </span>
            )}
          </div>
        )}


        {/* Branch Behind Details (no explicit conflicts but needs AI merge due to path mappings) */}
        {!hasGitConflicts && isBranchBehind && mergePreview?.gitConflicts && (
          <div className="text-xs text-muted-foreground pl-6">
            Target branch has {commitsBehind} new commit{commitsBehind !== 1 ? 's' : ''} since this build started.
            {hasPathMappedMerges ? (
              <span className="text-warning">
                {' '}{pathMappedAIMergeCount} file{pathMappedAIMergeCount !== 1 ? 's' : ''} need AI merge due to {totalRenames} file rename{totalRenames !== 1 ? 's' : ''}.
              </span>
            ) : totalRenames > 0 ? (
              <span className="text-warning"> {totalRenames} file rename{totalRenames !== 1 ? 's' : ''} detected - AI will handle the merge.</span>
            ) : (
              <span className="text-warning"> Files may have been renamed or moved - AI will handle the merge.</span>
            )}
          </div>
        )}

      </div>

      {/*
        Actions Footer - Merge Controls

        Conditionally rendered based on shouldShowMergeButton which checks:
        1. Task is NOT in plan_review state (reviewReason !== 'plan_review')
        2. Coding is completed AND has log entries (isCodingDoneAndHasLogs)

        When hidden:
        - No merge/stage actions are available
        - Users cannot accidentally merge incomplete work
        - UI clearly indicates task is not ready for merge

        When shown:
        - "Stage only" checkbox allows reviewing changes in IDE first
        - "Merge to Main" button (or "Merge with AI" if conflicts exist)
        - "Discard build" button to remove the worktree
      */}
      {shouldShowMergeButton && (
        <div className="px-4 py-3 bg-muted/20 border-t border-border space-y-3">
          {/* Stage Only Option */}
          <label className="inline-flex items-center gap-2.5 text-sm cursor-pointer select-none px-3 py-2 rounded-lg border border-border bg-background/50 hover:bg-background/80 transition-colors">
            <Checkbox
              checked={stageOnly}
              onCheckedChange={(checked) => onStageOnlyChange(checked === true)}
              className="border-muted-foreground/50 data-[state=checked]:border-primary"
            />
            <span className={cn(
              "transition-colors",
              stageOnly ? "text-foreground" : "text-muted-foreground"
            )}>Stage only (review in IDE before committing)</span>
          </label>

          {/* Merge Step Progress Indicator */}
          {mergeStep !== 'idle' && (
            <div className="p-2.5 rounded-lg border bg-muted/30 border-border space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-foreground">
                  {mergeStep === 'resolving_uncommitted' && 'Resolving uncommitted conflicts...'}
                  {mergeStep === 'resolving_git_conflicts' && 'Resolving git merge conflicts...'}
                  {mergeStep === 'merging' && `Merging to ${worktreeStatus.baseBranch || 'main'}...`}
                </span>
                {workspaceError && onAbortMerge && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={onAbortMerge}
                    disabled={isAbortingMerge}
                    className="h-6 text-xs text-muted-foreground hover:text-destructive hover:bg-destructive/10 hover:border-destructive/30"
                    title="Abort merge and reset git state"
                  >
                    {isAbortingMerge ? (
                      <>
                        <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                        Aborting...
                      </>
                    ) : (
                      <>
                        <XCircle className="h-3 w-3 mr-1" />
                        Abort Merge
                      </>
                    )}
                  </Button>
                )}
              </div>
              <div className="flex items-center gap-1.5">
                {(['resolving_uncommitted', 'resolving_git_conflicts', 'merging'] as const).map((step, idx) => {
                  const steps = ['resolving_uncommitted', 'resolving_git_conflicts', 'merging'] as const;
                  const currentIdx = steps.indexOf(mergeStep);
                  const stepIdx = idx;
                  const isCompleted = stepIdx < currentIdx;
                  const isCurrent = stepIdx === currentIdx;
                  return (
                    <div
                      key={step}
                      className={cn(
                        "h-1.5 flex-1 rounded-full transition-colors",
                        isCompleted ? "bg-success" :
                        isCurrent ? "bg-primary animate-pulse" :
                        "bg-muted-foreground/20"
                      )}
                    />
                  );
                })}
              </div>
            </div>
          )}

          {/* Primary Actions */}
          <div className="flex gap-2">
            <Button
              type="button"
              variant={hasGitConflicts || isBranchBehind || hasPathMappedMerges ? "warning" : "default"}
              onClick={onMerge}
              disabled={isMerging || isDiscarding || mergeStep !== 'idle'}
              className="flex-1"
            >
              {isMerging ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  {mergeStep === 'resolving_uncommitted' ? 'Resolving uncommitted...'
                    : mergeStep === 'resolving_git_conflicts' ? 'Resolving conflicts...'
                    : mergeStep === 'merging' ? (stageOnly ? 'Staging...' : 'Merging...')
                    : stageOnly ? 'Staging...' : 'Merging...'}
                </>
              ) : (
                <>
                  <GitMerge className="mr-2 h-4 w-4" />
                  {hasGitConflicts || isBranchBehind || hasPathMappedMerges
                    ? (stageOnly ? 'Stage with AI Merge' : 'Merge with AI')
                    : (stageOnly ? 'Stage Changes' : `Merge to ${worktreeStatus.baseBranch || 'main'}`)}
                </>
              )}
            </Button>
            {onCreatePR && (
              <Button
                type="button"
                variant="outline"
                onClick={onCreatePR}
                disabled={isMerging || isDiscarding || isCreatingPR || mergeStep !== 'idle'}
                className="flex-1"
              >
                {isCreatingPR ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Creating PR...
                  </>
                ) : (
                  <>
                    <GitPullRequestCreateArrow className="mr-2 h-4 w-4" />
                    Create PR
                  </>
                )}
              </Button>
            )}
            <Button
              type="button"
              variant="outline"
              size="icon"
              onClick={() => onShowDiscardDialog(true)}
              disabled={isMerging || isDiscarding || isCreatingPR}
              className="text-muted-foreground hover:text-destructive hover:bg-destructive/10 hover:border-destructive/30"
              title="Discard build"
            >
              <FolderX className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
