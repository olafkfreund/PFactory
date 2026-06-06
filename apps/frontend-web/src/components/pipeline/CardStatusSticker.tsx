/**
 * CardStatusSticker — a small corner overlay on TaskCard that celebrates
 * success or flags failure with a robot + icon sticker.
 *
 * Success (task.status === 'done'):
 *   Bot + ThumbsUp in Gruvbox green (#b8bb26), with a pop-in animation.
 *
 * Failure (task.status === 'human_review' && reviewReason is 'errors'
 *          or 'qa_rejected'):
 *   Bot + ThumbsDown in Gruvbox red + a semi-transparent red ✗ stamp
 *   across the bottom-right corner.
 *
 * Nothing rendered for in-progress / backlog / ai_review states.
 * Also nothing rendered when the task is archived.
 *
 * prefers-reduced-motion: pop-in and stamp animations are disabled
 * globally by the existing reduced-motion block in index.css.
 */

import { Bot, ThumbsUp, ThumbsDown } from 'lucide-react';
import { cn } from '../../lib/utils';
import type { Task } from '../../shared/types';

// ─── Helpers ──────────────────────────────────────────────────────────────────

type StickerVariant = 'success' | 'failure' | null;

function resolveStickerVariant(task: Task): StickerVariant {
  if (task.metadata?.archivedAt) return null;

  if (task.status === 'done') return 'success';

  if (
    task.status === 'human_review' &&
    (task.reviewReason === 'errors' || task.reviewReason === 'qa_rejected')
  ) {
    return 'failure';
  }

  return null;
}

// ─── CardStatusSticker ────────────────────────────────────────────────────────

interface CardStatusStickerProps {
  task: Task;
  className?: string;
}

export function CardStatusSticker({ task, className }: CardStatusStickerProps) {
  const variant = resolveStickerVariant(task);
  if (!variant) return null;

  const isSuccess = variant === 'success';

  return (
    <div
      className={cn(
        'cardsticker',
        isSuccess ? 'cardsticker--success' : 'cardsticker--failure',
        className,
      )}
      aria-label={isSuccess ? 'Task completed successfully' : 'Task has errors'}
    >
      {/* Robot icon */}
      <Bot
        className="cardsticker-bot"
        strokeWidth={1.6}
        aria-hidden="true"
      />

      {/* Thumb icon */}
      {isSuccess ? (
        <ThumbsUp
          className="cardsticker-thumb"
          strokeWidth={1.8}
          aria-hidden="true"
        />
      ) : (
        <ThumbsDown
          className="cardsticker-thumb"
          strokeWidth={1.8}
          aria-hidden="true"
        />
      )}

      {/* Failure only: semi-transparent ✗ stamp across the corner */}
      {!isSuccess && (
        <span className="cardsticker-stamp" aria-hidden="true">✗</span>
      )}
    </div>
  );
}
