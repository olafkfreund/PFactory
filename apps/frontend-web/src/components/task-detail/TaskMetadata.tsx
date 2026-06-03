import {
  Target,
  Bug,
  Wrench,
  FileCode,
  Shield,
  Gauge,
  Palette,
  Lightbulb,
  Users,
  GitBranch,
  ListChecks,
  Clock,
  Zap
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { useTranslation } from 'react-i18next';
import { Badge } from '../ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';
import { cn, formatRelativeTime } from '../../lib/utils';
import {
  TASK_CATEGORY_LABELS,
  TASK_CATEGORY_COLORS,
  TASK_COMPLEXITY_LABELS,
  TASK_COMPLEXITY_COLORS,
  TASK_IMPACT_LABELS,
  TASK_IMPACT_COLORS,
  TASK_PRIORITY_LABELS,
  TASK_PRIORITY_COLORS,
} from '../../shared/constants';
import type { Task, TaskCategory } from '../../shared/types';

// Category icon mapping
const CategoryIcon: Record<TaskCategory, typeof Target> = {
  feature: Target,
  bug_fix: Bug,
  refactoring: Wrench,
  documentation: FileCode,
  security: Shield,
  performance: Gauge,
  ui_ux: Palette,
  infrastructure: Wrench,
  testing: FileCode
};

interface TaskMetadataProps {
  task: Task;
}

export function TaskMetadata({ task }: TaskMetadataProps) {
  const { t } = useTranslation(['tasks']);
  const hasClassification = task.metadata && (
    task.metadata.category ||
    task.metadata.priority ||
    task.metadata.complexity ||
    task.metadata.impact ||
    task.metadata.securitySeverity ||
    task.metadata.sourceType ||
    task.metadata.mode === 'quick'
  );

  return (
    <div className="space-y-5">
      {/* Compact Metadata Bar: Classification + Timeline */}
      <div className="flex flex-wrap items-center justify-between gap-3 pb-4 border-b border-border">
        {/* Classification Badges - Left */}
        {hasClassification && (
          <div className="flex flex-wrap items-center gap-1.5">
            {/* Quick Mode */}
            {task.metadata?.mode === 'quick' && (
              <Badge
                variant="outline"
                className="text-xs bg-amber-500/10 text-amber-400 border-amber-500/30"
              >
                <Zap className="h-3 w-3 mr-1" />
                Quick
              </Badge>
            )}
            {/* Remote Control — click the badge to open claude.ai/code session list */}
            {task.metadata?.enableRemoteControl && (
              <a
                href="https://claude.ai/code"
                target="_blank"
                rel="noopener noreferrer"
                title={`Open this session in claude.ai/code — appears as "PFactory: ${task.specId}" in the session list`}
              >
                <Badge
                  variant="outline"
                  className="text-xs bg-blue-500/10 text-blue-400 border-blue-500/30 hover:bg-blue-500/20 transition-colors"
                >
                  Drive remotely ↗
                </Badge>
              </a>
            )}
            {/* Copilot Delegation — shown when this task was handed to
                Copilot. Links to the resulting PR once the tracker
                (#94) has set task.prNumber; falls back to the issue. */}
            {task.metadata?.enableDelegation && (() => {
              const prNumber = (task as unknown as { prNumber?: number }).prNumber;
              const prUrl = (task as unknown as { prUrl?: string }).prUrl;
              const issueUrl = task.metadata?.githubUrl;
              const href = prUrl || issueUrl || '#';
              const tooltip = prNumber
                ? `Open Copilot's PR #${prNumber}`
                : 'PFactory handed this issue to GitHub Copilot Coding Agent. Watching for the resulting PR.';
              return (
                <a
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={tooltip}
                >
                  <Badge
                    variant="outline"
                    className="text-xs bg-purple-500/10 text-purple-400 border-purple-500/30 hover:bg-purple-500/20 transition-colors"
                  >
                    Delegated to Copilot ↗
                  </Badge>
                </a>
              );
            })()}
            {/* Live Console deep link — copy a shareable URL that opens
                straight into a fullscreen agent-console (rmux pane stream)
                with no portal chrome.  Useful for sharing with a
                teammate over a VPN, or opening the same terminal from
                a phone browser without nav clicks. */}
            {task.projectId && task.specId && (
              <button
                type="button"
                onClick={(e) => {
                  e.preventDefault();
                  const url = `${window.location.origin}/console/${task.projectId}/${task.specId}`;
                  void navigator.clipboard.writeText(url);
                  // tiny inline feedback — flash the badge.  No toast
                  // dependency on this code path; we already have the
                  // useToast hook elsewhere if you want richer UX later.
                  const btn = e.currentTarget;
                  const original = btn.textContent;
                  btn.textContent = 'Copied!';
                  setTimeout(() => { btn.textContent = original; }, 1500);
                }}
                title="Copy a shareable URL to the live agent console"
              >
                <Badge
                  variant="outline"
                  className="text-xs bg-slate-500/10 text-slate-300 border-slate-500/30 hover:bg-slate-500/20 transition-colors"
                >
                  Copy console URL
                </Badge>
              </button>
            )}
            {/* Category */}
            {task.metadata?.category && (
              <Badge
                variant="outline"
                className={cn('text-xs', TASK_CATEGORY_COLORS[task.metadata.category])}
              >
                {CategoryIcon[task.metadata.category] && (() => {
                  const Icon = CategoryIcon[task.metadata.category!];
                  return <Icon className="h-3 w-3 mr-1" />;
                })()}
                {TASK_CATEGORY_LABELS[task.metadata.category]}
              </Badge>
            )}
            {/* Priority */}
            {task.metadata?.priority && (
              <Badge
                variant="outline"
                className={cn('text-xs', TASK_PRIORITY_COLORS[task.metadata.priority])}
              >
                {TASK_PRIORITY_LABELS[task.metadata.priority]}
              </Badge>
            )}
            {/* Complexity */}
            {task.metadata?.complexity && (
              <Badge
                variant="outline"
                className={cn('text-xs', TASK_COMPLEXITY_COLORS[task.metadata.complexity])}
              >
                {TASK_COMPLEXITY_LABELS[task.metadata.complexity]}
              </Badge>
            )}
            {/* Impact */}
            {task.metadata?.impact && (
              <Badge
                variant="outline"
                className={cn('text-xs', TASK_IMPACT_COLORS[task.metadata.impact])}
              >
                {TASK_IMPACT_LABELS[task.metadata.impact]}
              </Badge>
            )}
            {/* Security Severity */}
            {task.metadata?.securitySeverity && (
              <Badge
                variant="outline"
                className={cn('text-xs', TASK_IMPACT_COLORS[task.metadata.securitySeverity])}
              >
                <Shield className="h-3 w-3 mr-1" />
                {task.metadata.securitySeverity}
              </Badge>
            )}
            {/* Source Type */}
            {task.metadata?.sourceType && (
              <Badge variant="secondary" className="text-xs">
                {task.metadata.sourceType}
              </Badge>
            )}
          </div>
        )}

        {/* Timeline - Right */}
        <div className="flex items-center gap-4 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <Clock className="h-3 w-3" />
            Created {formatRelativeTime(task.createdAt)}
          </span>
          <span className="text-border">•</span>
          <span>Updated {formatRelativeTime(task.updatedAt)}</span>
        </div>
      </div>

      {/* Description - Primary Content */}
      {task.description && (
        <div className="prose prose-sm prose-invert max-w-4xl break-words prose-p:text-foreground/90 prose-p:leading-relaxed prose-headings:text-foreground prose-strong:text-foreground prose-li:text-foreground/90">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeHighlight]}
          >
            {task.description}
          </ReactMarkdown>
        </div>
      )}

      {/* Secondary Details */}
      {task.metadata && (
        <div className="space-y-4 pt-2">
          {/* Rationale */}
          {task.metadata.rationale && (
            <div>
              <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1.5 flex items-center gap-1.5">
                <Lightbulb className="h-3 w-3 text-warning" />
                Rationale
              </h3>
              <p className="text-sm text-foreground/80">{task.metadata.rationale}</p>
            </div>
          )}

          {/* Problem Solved */}
          {task.metadata.problemSolved && (
            <div>
              <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1.5 flex items-center gap-1.5">
                <Target className="h-3 w-3 text-success" />
                Problem Solved
              </h3>
              <p className="text-sm text-foreground/80">{task.metadata.problemSolved}</p>
            </div>
          )}

          {/* Target Audience */}
          {task.metadata.targetAudience && (
            <div>
              <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1.5 flex items-center gap-1.5">
                <Users className="h-3 w-3 text-info" />
                Target Audience
              </h3>
              <p className="text-sm text-foreground/80">{task.metadata.targetAudience}</p>
            </div>
          )}

          {/* Dependencies */}
          {task.metadata.dependencies && task.metadata.dependencies.length > 0 && (
            <div>
              <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1.5 flex items-center gap-1.5">
                <GitBranch className="h-3 w-3 text-purple-400" />
                Dependencies
              </h3>
              <ul className="text-sm text-foreground/80 list-disc list-inside space-y-0.5">
                {task.metadata.dependencies.map((dep, idx) => (
                  <li key={idx}>{dep}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Acceptance Criteria */}
          {task.metadata.acceptanceCriteria && task.metadata.acceptanceCriteria.length > 0 && (
            <div>
              <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1.5 flex items-center gap-1.5">
                <ListChecks className="h-3 w-3 text-success" />
                Acceptance Criteria
              </h3>
              <ul className="text-sm text-foreground/80 list-disc list-inside space-y-0.5">
                {task.metadata.acceptanceCriteria.map((criteria, idx) => (
                  <li key={idx}>{criteria}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Affected Files */}
          {task.metadata.affectedFiles && task.metadata.affectedFiles.length > 0 && (
            <div>
              <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1.5 flex items-center gap-1.5">
                <FileCode className="h-3 w-3" />
                Affected Files
              </h3>
              <div className="flex flex-wrap gap-1">
                {task.metadata.affectedFiles.map((file, idx) => (
                  <Tooltip key={idx}>
                    <TooltipTrigger asChild>
                      <Badge variant="secondary" className="text-xs font-mono cursor-help">
                        {file.split('/').pop()}
                      </Badge>
                    </TooltipTrigger>
                    <TooltipContent side="top" className="font-mono text-xs">
                      {file}
                    </TooltipContent>
                  </Tooltip>
                ))}
              </div>
            </div>
          )}

          {/* Skills */}
          {task.metadata?.selectedSkills && task.metadata.selectedSkills.length > 0 && (
            <div className="flex flex-col gap-1">
              <span className="text-sm font-medium text-muted-foreground">
                {t('tasks:skills.title')}
              </span>
              <div className="flex flex-wrap gap-1">
                {task.metadata.selectedSkills.map(skill => (
                  <Badge key={skill.id} variant="secondary">
                    {skill.name}
                  </Badge>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
