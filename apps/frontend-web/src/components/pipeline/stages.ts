/**
 * Shared stage metadata for the pipeline rail and kanban columns.
 *
 * Single source of truth for per-status accents, icons, and sub-labels so
 * the rail rings always match the column headers exactly.
 */

import { Inbox, Loader2, Bot, UserCheck, CheckCircle2 } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { TASK_STATUS_COLUMNS, TASK_STATUS_LABELS } from '../../shared/constants';
import type { TaskStatus } from '../../shared/types';

export type { TaskStatus };

/** Gruvbox hex accent per status (matches STAGE_ACCENT in KanbanBoard). */
export const STAGE_ACCENT: Record<TaskStatus, string> = {
  backlog:      '#fabd2f',  // yellow — queued / ready (vivid, cockpit brand)
  in_progress:  '#fe8019',  // orange — active work
  ai_review:    '#d3869b',  // purple — scanning
  human_review: '#83a598',  // blue   — awaiting you
  done:         '#b8bb26',  // green  — shipped
};

/** Lucide icon component per status. */
export const STAGE_ICON: Record<TaskStatus, LucideIcon> = {
  backlog:      Inbox,
  in_progress:  Loader2,
  ai_review:    Bot,
  human_review: UserCheck,
  done:         CheckCircle2,
};

/**
 * Monospace sub-label shown beneath each ring in the rail
 * (a short pipeline-stage code, cockpit style).
 */
export const STAGE_SUB_LABEL: Record<TaskStatus, string> = {
  backlog:      'queue',
  in_progress:  'exec',
  ai_review:    'review',
  human_review: 'approve',
  done:         'shipped',
};

/** Ordered list of statuses — same order as the kanban columns. */
export const PIPELINE_STAGES = TASK_STATUS_COLUMNS as readonly TaskStatus[];

/** Human-readable label per status, sourced from shared constants. */
export const STAGE_LABEL: Record<TaskStatus, string> = TASK_STATUS_LABELS as Record<TaskStatus, string>;
