import { useState } from 'react';
import { Eye, FileCode, ChevronDown, ChevronRight } from 'lucide-react';
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
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from '../../ui/collapsible';
import { cn } from '../../../lib/utils';
import { DiffViewer } from './DiffViewer';
import type { WorktreeDiff, WorktreeDiffFile } from '../../../shared/types';

interface DiffViewDialogProps {
  open: boolean;
  worktreeDiff: WorktreeDiff | null;
  onOpenChange: (open: boolean) => void;
}

/**
 * Single file entry with expandable diff content
 */
function FileEntry({
  file,
  isExpanded,
  onToggle
}: {
  file: WorktreeDiffFile;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  const hasDiff = !!file.diff;

  return (
    <Collapsible
      open={isExpanded}
      onOpenChange={hasDiff ? onToggle : undefined}
    >
      <CollapsibleTrigger asChild disabled={!hasDiff}>
        <div
          className={cn(
            "flex items-center justify-between p-2 rounded-lg bg-secondary/30 transition-colors",
            hasDiff && "cursor-pointer hover:bg-secondary/50",
            !hasDiff && "cursor-default"
          )}
        >
          <div className="flex items-center gap-2 min-w-0 flex-1">
            {hasDiff ? (
              isExpanded ? (
                <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
              ) : (
                <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
              )
            ) : (
              <div className="w-4 h-4 shrink-0" /> // Spacer for alignment
            )}
            <FileCode className={cn(
              'h-4 w-4 shrink-0',
              file.status === 'added' && 'text-success',
              file.status === 'deleted' && 'text-destructive',
              file.status === 'modified' && 'text-info',
              file.status === 'renamed' && 'text-warning'
            )} />
            <span className="text-sm font-mono truncate">{file.path}</span>
          </div>
          <div className="flex items-center gap-2 shrink-0 ml-2">
            <Badge
              variant="secondary"
              className={cn(
                'text-xs',
                file.status === 'added' && 'bg-success/10 text-success',
                file.status === 'deleted' && 'bg-destructive/10 text-destructive',
                file.status === 'modified' && 'bg-info/10 text-info',
                file.status === 'renamed' && 'bg-warning/10 text-warning'
              )}
            >
              {file.status}
            </Badge>
            <span className="text-xs text-success">+{file.additions}</span>
            <span className="text-xs text-destructive">-{file.deletions}</span>
          </div>
        </div>
      </CollapsibleTrigger>
      {hasDiff && (
        <CollapsibleContent className="mt-1 ml-6">
          <DiffViewer diff={file.diff!} showLineNumbers={false} />
        </CollapsibleContent>
      )}
    </Collapsible>
  );
}

/**
 * Dialog displaying the list of changed files with their status, line changes,
 * and expandable diff content
 */
export function DiffViewDialog({
  open,
  worktreeDiff,
  onOpenChange
}: DiffViewDialogProps) {
  const [expandedFiles, setExpandedFiles] = useState<Set<number>>(new Set());

  const toggleFile = (idx: number) => {
    setExpandedFiles(prev => {
      const next = new Set(prev);
      if (next.has(idx)) {
        next.delete(idx);
      } else {
        next.add(idx);
      }
      return next;
    });
  };

  const expandAll = () => {
    if (!worktreeDiff?.files) return;
    const allIndices = worktreeDiff.files
      .map((_, idx) => idx)
      .filter(idx => worktreeDiff.files[idx].diff);
    setExpandedFiles(new Set(allIndices));
  };

  const collapseAll = () => {
    setExpandedFiles(new Set());
  };

  const hasAnyDiff = worktreeDiff?.files?.some(f => f.diff);
  const allExpanded = worktreeDiff?.files?.every((f, idx) => !f.diff || expandedFiles.has(idx));

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent className="max-w-4xl max-h-[85vh] overflow-hidden flex flex-col">
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            <Eye className="h-5 w-5 text-purple-400" />
            Changed Files
          </AlertDialogTitle>
          <AlertDialogDescription className="flex items-center justify-between">
            <span>{worktreeDiff?.summary || 'No changes found'}</span>
            {hasAnyDiff && (
              <button
                onClick={allExpanded ? collapseAll : expandAll}
                className="text-xs text-primary hover:underline ml-2"
              >
                {allExpanded ? 'Collapse all' : 'Expand all'}
              </button>
            )}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="flex-1 overflow-auto min-h-0 -mx-6 px-6">
          {worktreeDiff?.files && worktreeDiff.files.length > 0 ? (
            <div className="space-y-2">
              {worktreeDiff.files.map((file, idx) => (
                <FileEntry
                  key={idx}
                  file={file}
                  isExpanded={expandedFiles.has(idx)}
                  onToggle={() => toggleFile(idx)}
                />
              ))}
            </div>
          ) : (
            <div className="text-center py-8 text-muted-foreground">
              No changed files found
            </div>
          )}
        </div>
        <AlertDialogFooter className="mt-4">
          <AlertDialogCancel>Close</AlertDialogCancel>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
