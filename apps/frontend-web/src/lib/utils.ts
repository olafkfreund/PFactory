import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * Utility function to merge Tailwind CSS classes
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Check if the app is running in web mode (vs Electron desktop mode)
 *
 * In web mode, the app uses HTTP/WebSocket for communication instead of Electron IPC.
 * Some features like opening external terminals/IDEs are not available in web mode.
 *
 * @returns true if running in web mode, false if running in Electron
 */
export function isWebMode(): boolean {
  // Check for the web mode marker set by api-adapter.ts
  // The web adapter sets window.API (not window.electronAPI)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const api = (window as any).API;
  return api?._isWebMode === true;
}

/**
 * Calculate progress percentage from subtasks
 * @param subtasks Array of subtasks with status
 * @returns Progress percentage (0-100)
 */
export function calculateProgress(subtasks: { status: string }[]): number {
  if (subtasks.length === 0) return 0;
  const completed = subtasks.filter((s) => s.status === 'completed').length;
  return Math.round((completed / subtasks.length) * 100);
}

/**
 * Format a date as a relative time string
 * Handles both Date objects and ISO date strings (from backend JSON responses)
 * Returns empty string for null/undefined/invalid dates to prevent UI crashes
 * @param date Date object or ISO date string to format
 * @returns Relative time string (e.g., "2 hours ago"), or empty string for invalid dates
 */
export function formatRelativeTime(date: Date | string | null | undefined): string {
  // Handle null/undefined dates gracefully to prevent UI crashes
  if (!date) return '';

  try {
    const now = new Date();
    const parsedDate = typeof date === 'string' ? new Date(date) : date;

    // Handle invalid dates
    if (isNaN(parsedDate.getTime())) {
      return '';
    }

    const diffMs = now.getTime() - parsedDate.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMins / 60);
    const diffDays = Math.floor(diffHours / 24);

    if (diffMins < 1) return 'just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;
    return parsedDate.toLocaleDateString();
  } catch {
    // If date parsing fails, return empty string to prevent UI crashes
    return '';
  }
}

/**
 * Sanitize and extract plain text from markdown content.
 * Strips markdown formatting and collapses whitespace for clean display in UI.
 * @param text The text that might contain markdown
 * @param maxLength Maximum length before truncation (default: 200)
 * @returns Plain text suitable for display
 */
export function sanitizeMarkdownForDisplay(text: string, maxLength: number = 200): string {
  if (!text) return '';

  let sanitized = text
    // Remove markdown headers (# ## ### etc)
    .replace(/^#{1,6}\s+/gm, '')
    // Remove bold/italic markers
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/_([^_]+)_/g, '$1')
    // Remove inline code
    .replace(/`([^`]+)`/g, '$1')
    // Remove code blocks
    .replace(/```[\s\S]*?```/g, '')
    // Remove links but keep text
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    // Remove images
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, '')
    // Remove horizontal rules
    .replace(/^[-*_]{3,}$/gm, '')
    // Remove blockquotes
    .replace(/^>\s*/gm, '')
    // Remove list markers
    .replace(/^[\s]*[-*+]\s+/gm, '')
    .replace(/^[\s]*\d+\.\s+/gm, '')
    // Remove checkbox markers
    .replace(/\[[ x]\]\s*/gi, '')
    // Collapse multiple newlines to single space
    .replace(/\n+/g, ' ')
    // Collapse multiple spaces to single space
    .replace(/\s+/g, ' ')
    .trim();

  // Truncate if needed (0 means no truncation)
  if (maxLength > 0 && sanitized.length > maxLength) {
    sanitized = sanitized.substring(0, maxLength).trim() + '...';
  }

  return sanitized;
}

/**
 * Extract the numeric portion from a task specId.
 * Spec IDs follow the format "NNN-name" (e.g., "006-bugs-hunt").
 *
 * @param specId The spec ID string (e.g., "006-bugs-hunt")
 * @returns The numeric portion (e.g., "006") or null if not found
 *
 * @example
 * extractTaskNumber("006-bugs-hunt") // "006"
 * extractTaskNumber("123-feature") // "123"
 * extractTaskNumber("invalid") // null
 * extractTaskNumber("") // null
 */
export function extractTaskNumber(specId: string | undefined | null): string | null {
  if (!specId) return null;

  // Match pattern: leading digits followed by hyphen
  const match = specId.match(/^(\d+)-/);
  return match ? match[1] : null;
}

/**
 * Format a task title with its number from specId.
 * Returns title with number in parentheses: "title (NNN)"
 * Falls back to plain title if no number is found.
 *
 * @param title The task title
 * @param specId The spec ID (e.g., "006-bugs-hunt")
 * @returns Formatted string like "bugs-hunt (006)" or plain title
 */
export function formatTaskTitleWithNumber(title: string, specId: string | undefined | null): string {
  const number = extractTaskNumber(specId);
  return number ? `${title} (${number})` : title;
}
