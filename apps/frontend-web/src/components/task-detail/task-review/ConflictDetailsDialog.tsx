import { AlertTriangle, GitMerge, CheckCircle, Sparkles, Info } from 'lucide-react';
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '../../ui/alert-dialog';
import { Badge } from '../../ui/badge';
import { cn } from '../../../lib/utils';
import { getSeverityIcon, getSeverityVariant } from './utils';
import type { MergeConflict, MergeStats, GitConflictInfo } from '../../../shared/types';

interface ConflictDetailsDialogProps {
  open: boolean;
  mergePreview: { files: string[]; conflicts: MergeConflict[]; summary: MergeStats; gitConflicts?: GitConflictInfo } | null;
  onOpenChange: (open: boolean) => void;
}

/**
 * Dialog displaying detailed information about merge conflicts
 */
export function ConflictDetailsDialog({
  open,
  mergePreview,
  onOpenChange,
}: ConflictDetailsDialogProps) {
  // Categorize conflicts
  const autoMergeable = mergePreview?.conflicts.filter(c => c.canAutoMerge) || [];
  const needsAI = mergePreview?.conflicts.filter(c => !c.canAutoMerge && (c.severity === 'low' || c.severity === 'medium')) || [];
  const needsHuman = mergePreview?.conflicts.filter(c => !c.canAutoMerge && (c.severity === 'high' || c.severity === 'critical')) || [];

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent className="max-w-2xl max-h-[80vh] overflow-hidden flex flex-col">
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-warning" />
            {mergePreview?.gitConflicts?.hasConflicts || (mergePreview?.conflicts?.length ?? 0) > 0
              ? 'Merge Conflicts Analysis'
              : 'Branch Divergence Details'}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {mergePreview?.gitConflicts?.hasConflicts ? (
              <>
                Branch has diverged with {mergePreview.gitConflicts.commitsBehind} commit{mergePreview.gitConflicts.commitsBehind !== 1 ? 's' : ''} behind
                and {mergePreview.gitConflicts.conflictingFiles.length} conflicting file{mergePreview.gitConflicts.conflictingFiles.length !== 1 ? 's' : ''}.
              </>
            ) : mergePreview?.gitConflicts?.needsRebase ? (
              <>
                Branch is {mergePreview.gitConflicts.commitsBehind} commit{mergePreview.gitConflicts.commitsBehind !== 1 ? 's' : ''} behind.
                AI will rebase and resolve during merge.
              </>
            ) : (
              <>
                {mergePreview?.conflicts.length || 0} potential conflict{(mergePreview?.conflicts.length || 0) !== 1 ? 's' : ''} detected.
              </>
            )}
            {autoMergeable.length > 0 && (
              <span className="text-success ml-1">
                {autoMergeable.length} can be auto-merged.
              </span>
            )}
            {needsAI.length > 0 && (
              <span className="text-purple-600 dark:text-purple-400 ml-1">
                {needsAI.length} can be resolved by AI.
              </span>
            )}
            {needsHuman.length > 0 && (
              <span className="text-warning ml-1">
                {needsHuman.length} require{needsHuman.length === 1 ? 's' : ''} manual review.
              </span>
            )}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="flex-1 overflow-auto min-h-0 -mx-6 px-6">
          {/* Git-level conflicts (branch divergence) */}
          {mergePreview?.gitConflicts?.hasConflicts && (
            <div className="mb-4 p-3 bg-warning/10 rounded-lg border border-warning/30">
              <div className="flex items-center gap-2 mb-2 text-sm font-medium text-warning">
                <GitMerge className="h-4 w-4" />
                Branch Diverged
              </div>
              <p className="text-xs text-muted-foreground mb-2">
                The main branch has{' '}
                <span className="font-medium text-foreground">
                  {mergePreview.gitConflicts.commitsBehind}
                </span>{' '}
                new commit{mergePreview.gitConflicts.commitsBehind !== 1 ? 's' : ''} since this
                worktree was created.
                {mergePreview.gitConflicts.conflictingFiles.length > 0 && (
                  <span>
                    {' '}{mergePreview.gitConflicts.conflictingFiles.length} file
                    {mergePreview.gitConflicts.conflictingFiles.length !== 1 ? 's' : ''} have
                    conflicting changes:
                  </span>
                )}
              </p>
              {mergePreview.gitConflicts.conflictingFiles.length > 0 && (
                <ul className="space-y-1 mb-2">
                  {mergePreview.gitConflicts.conflictingFiles.map((file, idx) => (
                    <li
                      key={idx}
                      className="text-xs font-mono text-muted-foreground flex items-center gap-2 p-1.5 bg-secondary/30 rounded border border-border"
                    >
                      <AlertTriangle className="h-3 w-3 text-warning shrink-0" />
                      <span className="truncate">{file}</span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="text-xs text-muted-foreground flex items-center gap-1.5 mt-2">
                <Sparkles className="h-3.5 w-3.5 text-purple-400 shrink-0" />
                AI will automatically merge these conflicts when you click Stage Changes.
              </p>
            </div>
          )}

          {/* Branch behind (needs rebase, no file conflicts) */}
          {!mergePreview?.gitConflicts?.hasConflicts && mergePreview?.gitConflicts?.needsRebase && (
            <div className="mb-4 p-3 bg-warning/10 rounded-lg border border-warning/30">
              <div className="flex items-center gap-2 mb-2 text-sm font-medium text-warning">
                <GitMerge className="h-4 w-4" />
                Branch Behind
              </div>
              <p className="text-xs text-muted-foreground mb-2">
                The target branch has{' '}
                <span className="font-medium text-foreground">
                  {mergePreview.gitConflicts.commitsBehind}
                </span>{' '}
                new commit{mergePreview.gitConflicts.commitsBehind !== 1 ? 's' : ''} since this
                build started. No file conflicts detected.
              </p>
              {(mergePreview.gitConflicts.totalRenames ?? 0) > 0 && (
                <p className="text-xs text-muted-foreground">
                  {mergePreview.gitConflicts.totalRenames} file rename{mergePreview.gitConflicts.totalRenames !== 1 ? 's' : ''} detected — AI will handle path mapping.
                </p>
              )}
              <p className="text-xs text-muted-foreground flex items-center gap-1.5 mt-2">
                <Sparkles className="h-3.5 w-3.5 text-purple-400 shrink-0" />
                AI will automatically rebase and merge when you click Merge.
              </p>
            </div>
          )}

          {mergePreview?.conflicts && mergePreview.conflicts.length > 0 ? (
            <div className="space-y-3">
              {/* Auto-mergeable conflicts section */}
              {autoMergeable.length > 0 && (
                <div className="mb-4">
                  <div className="flex items-center gap-2 mb-2 text-sm font-medium text-success">
                    <CheckCircle className="h-4 w-4" />
                    Auto-mergeable ({autoMergeable.length})
                  </div>
                  <div className="space-y-2">
                    {autoMergeable.map((conflict, idx) => (
                      <ConflictItem key={`auto-${idx}`} conflict={conflict} variant="auto" />
                    ))}
                  </div>
                </div>
              )}

              {/* AI-resolvable conflicts section */}
              {needsAI.length > 0 && (
                <div className="mb-4">
                  <div className="flex items-center gap-2 mb-2 text-sm font-medium text-purple-600 dark:text-purple-400">
                    <Sparkles className="h-4 w-4" />
                    AI Can Resolve ({needsAI.length})
                  </div>
                  <div className="space-y-2">
                    {needsAI.map((conflict, idx) => (
                      <ConflictItem key={`ai-${idx}`} conflict={conflict} variant="ai" />
                    ))}
                  </div>
                </div>
              )}

              {/* Human-required conflicts section */}
              {needsHuman.length > 0 && (
                <div className="mb-4">
                  <div className="flex items-center gap-2 mb-2 text-sm font-medium text-warning">
                    <AlertTriangle className="h-4 w-4" />
                    Manual Review Required ({needsHuman.length})
                  </div>
                  <div className="space-y-2">
                    {needsHuman.map((conflict, idx) => (
                      <ConflictItem key={`human-${idx}`} conflict={conflict} variant="human" />
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : !mergePreview?.gitConflicts?.hasConflicts && !mergePreview?.gitConflicts?.needsRebase ? (
            <div className="text-center py-8 text-muted-foreground">
              No conflicts detected
            </div>
          ) : null}
        </div>
        <AlertDialogFooter className="mt-4">
          <AlertDialogCancel>Close</AlertDialogCancel>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/**
 * Individual conflict item display
 */
function ConflictItem({
  conflict,
  variant,
}: {
  conflict: MergeConflict;
  variant: 'auto' | 'ai' | 'human';
}) {
  const variantStyles = {
    auto: "bg-secondary/30 border-border",
    ai: "bg-purple-500/5 border-purple-500/20",
    human: conflict.severity === 'critical'
      ? "bg-destructive/10 border-destructive/30"
      : "bg-warning/10 border-warning/30",
  };

  return (
    <div className={cn("p-3 rounded-lg border", variantStyles[variant])}>
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          {getSeverityIcon(conflict.severity)}
          <span className="text-sm font-mono truncate">{conflict.file}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Badge
            variant="secondary"
            className={cn('text-xs', getSeverityVariant(conflict.severity))}
          >
            {conflict.severity}
          </Badge>
          {conflict.canAutoMerge && (
            <Badge variant="secondary" className="text-xs bg-success/10 text-success">
              auto-merge
            </Badge>
          )}
          {conflict.type === 'semantic' && (
            <Badge variant="secondary" className="text-xs bg-purple-500/10 text-purple-600 dark:text-purple-400">
              semantic
            </Badge>
          )}
        </div>
      </div>
      <div className="text-xs text-muted-foreground space-y-1">
        {conflict.location && (
          <div className="flex items-start gap-1">
            <span className="text-foreground/70 shrink-0">Location:</span>
            <span className="font-mono">{conflict.location}</span>
          </div>
        )}
        {conflict.reason && (
          <div className="flex items-start gap-1">
            <span className="text-foreground/70 shrink-0">Reason:</span>
            <span>{conflict.reason}</span>
          </div>
        )}
        {conflict.strategy && (
          <div className="flex items-center gap-1">
            <Info className="h-3 w-3 text-foreground/50" />
            <span className="text-foreground/70">Strategy:</span>
            <code className="bg-secondary/50 px-1 rounded text-[11px]">{conflict.strategy}</code>
          </div>
        )}
        {conflict.tasks && conflict.tasks.length > 1 && (
          <div className="flex items-start gap-1">
            <span className="text-foreground/70 shrink-0">Tasks involved:</span>
            <span>{conflict.tasks.join(', ')}</span>
          </div>
        )}
      </div>
    </div>
  );
}
