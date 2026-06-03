import { memo, useMemo, useEffect } from 'react';
import { CheckCircle2, Clock, XCircle, AlertCircle, ListChecks, FileCode } from 'lucide-react';
import { Badge } from '../ui/badge';
import { ScrollArea } from '../ui/scroll-area';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';
import { cn, calculateProgress } from '../../lib/utils';
import type { Task, Subtask } from '../../shared/types';

interface TaskSubtasksProps {
  task: Task;
}

function getSubtaskStatusIcon(status: string) {
  switch (status) {
    case 'completed':
      return <CheckCircle2 className="h-4 w-4 text-[var(--success)]" />;
    case 'in_progress':
      return <Clock className="h-4 w-4 text-[var(--info)] animate-pulse" />;
    case 'failed':
      return <XCircle className="h-4 w-4 text-[var(--error)]" />;
    default:
      return <AlertCircle className="h-4 w-4 text-muted-foreground" />;
  }
}

/**
 * Compare two subtask arrays to determine if they've changed.
 * Returns true if arrays are equal (no change), false if different.
 */
function areSubtasksEqual(prevSubtasks: Subtask[], nextSubtasks: Subtask[]): boolean {
  if (prevSubtasks.length !== nextSubtasks.length) return false;

  // Check each subtask's status and id
  for (let i = 0; i < prevSubtasks.length; i++) {
    const prev = prevSubtasks[i];
    const next = nextSubtasks[i];
    if (prev.id !== next.id || prev.status !== next.status) {
      return false;
    }
  }
  return true;
}

/**
 * Custom comparison function for React.memo to ensure component updates
 * when subtask statuses change. This prevents stale UI while avoiding
 * unnecessary re-renders.
 */
function arePropsEqual(prevProps: TaskSubtasksProps, nextProps: TaskSubtasksProps): boolean {
  // Always re-render if task ID changes
  if (prevProps.task.id !== nextProps.task.id) return false;

  // Check if subtasks have changed (status updates trigger re-render)
  return areSubtasksEqual(prevProps.task.subtasks, nextProps.task.subtasks);
}

export const TaskSubtasks = memo(function TaskSubtasks({ task }: TaskSubtasksProps) {
  // Use useMemo for derived values to ensure they update correctly
  const { progress, completedCount } = useMemo(() => ({
    progress: calculateProgress(task.subtasks),
    completedCount: task.subtasks.filter(c => c.status === 'completed').length
  }), [task.subtasks]);

  // Debug logging to trace subtask updates
  useEffect(() => {
    if (window.DEBUG) {
      console.log('[TaskSubtasks] Rendered with:', {
        taskId: task.id,
        subtasksCount: task.subtasks.length,
        completedCount,
        progress,
        statuses: task.subtasks.map(s => ({ id: s.id, status: s.status }))
      });
    }
  }, [task.id, task.subtasks, completedCount, progress]);

  return (
    <ScrollArea className="h-full">
      <div className="p-4 space-y-3">
        {task.subtasks.length === 0 ? (
          <div className="text-center py-12">
            <ListChecks className="h-10 w-10 mx-auto mb-3 text-muted-foreground/30" />
            <p className="text-sm font-medium text-muted-foreground mb-1">No subtasks defined</p>
            <p className="text-xs text-muted-foreground/70">
              Implementation subtasks will appear here after planning
            </p>
          </div>
        ) : (
          <>
            {/* Progress summary - uses memoized completedCount for real-time updates */}
            <div className="flex items-center justify-between text-xs text-muted-foreground pb-2 border-b border-border/50">
              <span>{completedCount} of {task.subtasks.length} completed</span>
              <span className="tabular-nums">{progress}%</span>
            </div>
            {task.subtasks.map((subtask, index) => (
              <div
                key={subtask.id}
                className={cn(
                  'rounded-xl border border-border bg-secondary/30 p-3 transition-all duration-200 hover:bg-secondary/50',
                  subtask.status === 'in_progress' && 'border-[var(--info)]/50 bg-[var(--info-light)] ring-1 ring-info/20',
                  subtask.status === 'completed' && 'border-[var(--success)]/50 bg-[var(--success-light)]',
                  subtask.status === 'failed' && 'border-[var(--error)]/50 bg-[var(--error-light)]'
                )}
              >
                <div className="flex items-start gap-2">
                  {getSubtaskStatusIcon(subtask.status)}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className={cn(
                        'text-[10px] font-medium px-1.5 py-0.5 rounded-full',
                        subtask.status === 'completed' ? 'bg-success/20 text-success' :
                        subtask.status === 'in_progress' ? 'bg-info/20 text-info' :
                        subtask.status === 'failed' ? 'bg-destructive/20 text-destructive' :
                        'bg-muted text-muted-foreground'
                      )}>
                        #{index + 1}
                      </span>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="text-sm font-medium text-foreground truncate cursor-default">
                            {subtask.id}
                          </span>
                        </TooltipTrigger>
                        <TooltipContent side="top" className="max-w-xs">
                          <p className="font-mono text-xs">{subtask.id}</p>
                        </TooltipContent>
                      </Tooltip>
                    </div>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <p className="mt-1 text-xs text-muted-foreground line-clamp-2 cursor-default">
                          {subtask.description}
                        </p>
                      </TooltipTrigger>
                      {subtask.description && subtask.description.length > 80 && (
                        <TooltipContent side="bottom" className="max-w-sm">
                          <p className="text-xs">{subtask.description}</p>
                        </TooltipContent>
                      )}
                    </Tooltip>
                    {subtask.files && subtask.files.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {subtask.files.map((file) => (
                          <Tooltip key={file}>
                            <TooltipTrigger asChild>
                              <Badge
                                variant="secondary"
                                className="text-xs font-mono cursor-help"
                              >
                                <FileCode className="mr-1 h-3 w-3" />
                                {file.split('/').pop()}
                              </Badge>
                            </TooltipTrigger>
                            <TooltipContent side="top" className="font-mono text-xs">
                              {file}
                            </TooltipContent>
                          </Tooltip>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </>
        )}
      </div>
    </ScrollArea>
  );
}, arePropsEqual);
