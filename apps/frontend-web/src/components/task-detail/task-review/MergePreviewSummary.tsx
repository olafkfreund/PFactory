import { CheckCircle, AlertTriangle, Sparkles, Loader2 } from 'lucide-react';
import { Button } from '../../ui/button';
import { cn } from '../../../lib/utils';
import type { MergeConflict, MergeStats, GitConflictInfo } from '../../../shared/types';

interface MergePreviewSummaryProps {
  mergePreview: {
    files: string[];
    conflicts: MergeConflict[];
    summary: MergeStats;
    gitConflicts?: GitConflictInfo;
  };
  onShowConflictDialog: (show: boolean) => void;
  onResolveWithAI?: () => void;
  isResolvingConflicts?: boolean;
}

/**
 * Displays a summary of the merge preview including conflicts and statistics
 */
export function MergePreviewSummary({
  mergePreview,
  onShowConflictDialog,
  onResolveWithAI,
  isResolvingConflicts = false,
}: MergePreviewSummaryProps) {
  const hasGitConflicts = mergePreview.gitConflicts?.hasConflicts;
  const hasSemanticConflicts = mergePreview.conflicts.length > 0;
  const hasHighSeverity = mergePreview.conflicts.some(
    c => c.severity === 'high' || c.severity === 'critical'
  );

  // Categorize conflicts
  const autoMergeableCount = mergePreview.summary.autoMergeable || 0;
  const aiResolvableCount = (mergePreview.summary.totalConflicts || 0) - autoMergeableCount - (mergePreview.summary.humanRequired || 0);
  const humanRequiredCount = mergePreview.summary.humanRequired || 0;
  const canResolveWithAI = aiResolvableCount > 0 && !isResolvingConflicts;

  return (
    <div className={cn(
      "rounded-lg p-3 mb-3 border",
      hasGitConflicts
        ? "bg-warning/10 border-warning/30"
        : !hasSemanticConflicts
          ? "bg-success/10 border-success/30"
          : hasHighSeverity
            ? "bg-destructive/10 border-destructive/30"
            : "bg-warning/10 border-warning/30"
    )}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-medium flex items-center gap-2">
          {hasGitConflicts ? (
            <>
              <AlertTriangle className="h-4 w-4 text-warning" />
              Branch Diverged - AI Will Resolve
            </>
          ) : !hasSemanticConflicts ? (
            <>
              <CheckCircle className="h-4 w-4 text-success" />
              No Conflicts Detected
            </>
          ) : (
            <>
              <AlertTriangle className="h-4 w-4 text-warning" />
              {mergePreview.conflicts.length} Conflict{mergePreview.conflicts.length !== 1 ? 's' : ''} Found
            </>
          )}
        </span>
        <div className="flex items-center gap-2">
          {hasSemanticConflicts && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onShowConflictDialog(true)}
              className="h-7 text-xs"
            >
              View Details
            </Button>
          )}
          {canResolveWithAI && onResolveWithAI && (
            <Button
              variant="secondary"
              size="sm"
              onClick={onResolveWithAI}
              disabled={isResolvingConflicts}
              className="h-7 text-xs bg-purple-500/10 hover:bg-purple-500/20 text-purple-600 dark:text-purple-400"
            >
              {isResolvingConflicts ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
                  Resolving...
                </>
              ) : (
                <>
                  <Sparkles className="h-3.5 w-3.5 mr-1" />
                  Resolve with AI
                </>
              )}
            </Button>
          )}
        </div>
      </div>

      {hasGitConflicts && mergePreview.gitConflicts && (
        <div className="mb-3 p-2 bg-warning/10 rounded text-xs border border-warning/30">
          <p className="font-medium text-warning mb-1">Branch has diverged - AI will resolve</p>
          <p className="text-muted-foreground mb-2">
            The main branch has {mergePreview.gitConflicts.commitsBehind} new commit{mergePreview.gitConflicts.commitsBehind !== 1 ? 's' : ''} since this worktree was created.
            {mergePreview.gitConflicts.conflictingFiles.length > 0 && (
              <> {mergePreview.gitConflicts.conflictingFiles.length} file{mergePreview.gitConflicts.conflictingFiles.length !== 1 ? 's' : ''} will need intelligent merging:</>
            )}
          </p>
          {mergePreview.gitConflicts.conflictingFiles.length > 0 && (
            <ul className="list-disc list-inside text-muted-foreground">
              {mergePreview.gitConflicts.conflictingFiles.map((file, idx) => (
                <li key={idx} className="truncate">{file}</li>
              ))}
            </ul>
          )}
          <p className="mt-2 text-muted-foreground">
            AI will automatically merge these conflicts when you click Stage Changes.
          </p>
        </div>
      )}

      {/* Semantic conflicts breakdown */}
      {hasSemanticConflicts && !hasGitConflicts && (
        <div className="mb-3 p-2 bg-secondary/50 rounded text-xs border border-border">
          <p className="font-medium text-foreground mb-1">Semantic Conflicts Detected</p>
          <div className="grid grid-cols-3 gap-2 mt-2">
            {autoMergeableCount > 0 && (
              <div className="text-success">
                <span className="font-medium">{autoMergeableCount}</span> auto-merge
              </div>
            )}
            {aiResolvableCount > 0 && (
              <div className="text-purple-600 dark:text-purple-400">
                <span className="font-medium">{aiResolvableCount}</span> AI can resolve
              </div>
            )}
            {humanRequiredCount > 0 && (
              <div className="text-warning">
                <span className="font-medium">{humanRequiredCount}</span> manual review
              </div>
            )}
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
        <div>Files to merge: {mergePreview.summary.totalFiles}</div>
        {hasGitConflicts ? (
          <div className="text-warning">AI will resolve conflicts</div>
        ) : hasSemanticConflicts ? (
          <>
            <div>Total conflicts: {mergePreview.summary.totalConflicts || 0}</div>
            {mergePreview.summary.aiResolved !== undefined && mergePreview.summary.aiResolved > 0 && (
              <div className="text-success">AI resolved: {mergePreview.summary.aiResolved}</div>
            )}
          </>
        ) : (
          <div className="text-success">Ready to merge</div>
        )}
      </div>
    </div>
  );
}
