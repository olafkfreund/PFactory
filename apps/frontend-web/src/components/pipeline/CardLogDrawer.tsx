/**
 * CardLogDrawer — in-card expandable live agent log view (issue #78, phase 3).
 *
 * Shows a small TerminalSquare icon on actively-running task cards with a
 * pulsing "live" dot indicator. Clicking the icon toggles an inline drawer
 * showing the last N lines of live agent output in Gruvbox monospace style.
 *
 * Performance guards:
 *   • Lazy-mount: the useCardLogs subscription only starts when expanded.
 *     Collapsing tears down both the file-watcher and the WebSocket listener.
 *   • Line cap: useCardLogs buffers at most MAX_LINES (200) entries total.
 *   • stopPropagation on the toggle button prevents card click / DnD triggers.
 *   • Auto-scroll only runs when user has not manually scrolled up.
 *
 * Accessibility:
 *   • Toggle button has aria-expanded + aria-label.
 *   • Log region has role="log" and aria-live="polite" so screen readers
 *     announce new output without interrupting navigation.
 *   • prefers-reduced-motion: the live dot pulse is suppressed via CSS class
 *     `.card-log-live-dot--motion` which only adds the animation; the
 *     global reduced-motion block in index.css removes it.
 *
 * Styling: Gruvbox only — bg0 (#282828 dark / #fbf1c7 light), JetBrains Mono.
 * CSS lives in index.css under the "CardLogDrawer" section.
 */

import { useState, useRef, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { TerminalSquare, Loader2 } from 'lucide-react';
import { cn } from '../../lib/utils';
import { useCardLogs } from './useCardLogs';
import type { Task, TaskLogEntry } from '../../shared/types';

// ─── Props ────────────────────────────────────────────────────────────────────

interface CardLogDrawerProps {
  task: Task;
  className?: string;
}

// ─── Main component ───────────────────────────────────────────────────────────

export function CardLogDrawer({ task, className }: CardLogDrawerProps) {
  const { t } = useTranslation('tasks');
  const [isOpen, setIsOpen] = useState(false);
  const [isUserScrolledUp, setIsUserScrolledUp] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Only active while running — once a task leaves in_progress we stop showing
  // the toggle (it may still be expanded; collapsing is handled by the effect below).
  const isRunning = task.status === 'in_progress';

  // Lazy subscription: useCardLogs only activates when isOpen is true.
  const { lines, isLoading } = useCardLogs(task.specId, isOpen);

  // Auto-close drawer when task is no longer running.
  useEffect(() => {
    if (!isRunning) setIsOpen(false);
  }, [isRunning]);

  // Auto-scroll to bottom as new lines arrive, unless user scrolled up.
  useEffect(() => {
    if (!isOpen || isUserScrolledUp) return;
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [lines, isOpen, isUserScrolledUp]);

  // Handle scroll — detect if user scrolled away from bottom.
  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    setIsUserScrolledUp(!nearBottom);
  }, []);

  // Toggle the drawer; must stopPropagation so card click / DnD are unaffected.
  const handleToggle = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    setIsOpen((prev) => !prev);
    setIsUserScrolledUp(false);
  }, []);

  // Do not render anything when the task is not running.
  if (!isRunning) return null;

  return (
    <div className={cn('card-log-drawer', className)}>
      {/* ── Toggle button ── */}
      <button
        type="button"
        className="card-log-toggle"
        onClick={handleToggle}
        aria-expanded={isOpen}
        aria-label={
          isOpen
            ? (t('cardLog.hideLog', 'Hide live log') as string)
            : (t('cardLog.showLog', 'Show live log') as string)
        }
        title={
          isOpen
            ? (t('cardLog.hideLog', 'Hide live log') as string)
            : (t('cardLog.showLog', 'Show live log') as string)
        }
      >
        {/* Terminal icon */}
        <TerminalSquare
          className={cn('card-log-icon', isOpen && 'card-log-icon--open')}
          aria-hidden="true"
          strokeWidth={1.6}
        />

        {/* Pulsing live dot */}
        <span
          className="card-log-live-dot card-log-live-dot--motion"
          aria-hidden="true"
        />

        {/* Label */}
        <span className="card-log-toggle-label">
          {t('cardLog.live', 'live')}
        </span>
      </button>

      {/* ── Inline drawer ── */}
      {isOpen && (
        <div
          className="card-log-panel"
          role="log"
          aria-label={t('cardLog.logRegionLabel', 'Live agent output') as string}
          aria-live="polite"
          aria-atomic={false}
        >
          {/* Drawer inner scroll area */}
          <div
            ref={scrollRef}
            className="card-log-scroll"
            onScroll={handleScroll}
          >
            {isLoading && lines.length === 0 ? (
              <div className="card-log-empty">
                <Loader2 className="card-log-empty-icon animate-spin" aria-hidden="true" />
                <span>{t('cardLog.loading', 'Loading…')}</span>
              </div>
            ) : lines.length === 0 ? (
              <div className="card-log-empty">
                <span>{t('cardLog.waiting', 'Waiting for output…')}</span>
              </div>
            ) : (
              <>
                {lines.map((entry, idx) => (
                  <LogLine key={`${entry.timestamp}-${idx}`} entry={entry} />
                ))}
                <div ref={bottomRef} />
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Single log line ──────────────────────────────────────────────────────────

interface LogLineProps {
  entry: TaskLogEntry;
}

/** Gruvbox hex colours for tool-start/end type indicators. */
const TOOL_COLOR: Record<string, string> = {
  Read:  '#83a598',   // blue
  Glob:  '#fabd2f',   // yellow
  Grep:  '#b8bb26',   // green
  Edit:  '#d3869b',   // purple
  Write: '#8ec07c',   // aqua
  Bash:  '#fe8019',   // orange
};

function formatTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
  } catch {
    return '';
  }
}

function LogLine({ entry }: LogLineProps) {
  const color = entry.tool_name ? (TOOL_COLOR[entry.tool_name] ?? '#a89984') : undefined;

  // Tool events: render a compact coloured badge.
  if (entry.type === 'tool_start' || entry.type === 'tool_end') {
    const verb = entry.type === 'tool_start' ? '▶' : '✓';
    const name = entry.tool_name ?? '?';
    const input = entry.tool_input
      ? entry.tool_input.length > 60
        ? entry.tool_input.slice(0, 60) + '…'
        : entry.tool_input
      : '';

    return (
      <div
        className="card-log-line card-log-line--tool"
        style={{ '--tool-color': color } as React.CSSProperties}
      >
        <span className="card-log-verb" aria-hidden="true">{verb}</span>
        <span className="card-log-tool-name">{name}</span>
        {input && <span className="card-log-tool-input">{input}</span>}
      </div>
    );
  }

  // Error: red text.
  if (entry.type === 'error') {
    return (
      <div className="card-log-line card-log-line--error">
        <span className="card-log-ts">{formatTime(entry.timestamp)}</span>
        <span className="card-log-content">{entry.content}</span>
      </div>
    );
  }

  // Success: green text.
  if (entry.type === 'success') {
    return (
      <div className="card-log-line card-log-line--success">
        <span className="card-log-ts">{formatTime(entry.timestamp)}</span>
        <span className="card-log-content">{entry.content}</span>
      </div>
    );
  }

  // Phase markers: slightly different style.
  if (entry.type === 'phase_start' || entry.type === 'phase_end') {
    return (
      <div className="card-log-line card-log-line--phase">
        <span className="card-log-ts">{formatTime(entry.timestamp)}</span>
        <span className="card-log-content">{entry.content}</span>
      </div>
    );
  }

  // Default text line.
  return (
    <div className="card-log-line">
      <span className="card-log-ts">{formatTime(entry.timestamp)}</span>
      <span className="card-log-content">{entry.content}</span>
    </div>
  );
}
