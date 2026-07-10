/**
 * Frontend Logger
 *
 * In-memory ring buffer (read by the LogViewer settings panel) with:
 * - localStorage persistence across reloads
 * - level gating (debug in dev, info in prod)
 * - error forwarding to the backend (/api/logs/frontend)
 */

export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

export interface LogEntry {
  timestamp: string;
  level: LogLevel;
  category: string;
  message: string;
  data?: unknown;
  stack?: string;
}

// Storage keys
const STORAGE_KEY = 'pfactory-logs';
const MAX_LOGS = 1000; // Maximum number of logs to keep
const ERROR_BATCH_INTERVAL = 5000; // Send errors to backend every 5 seconds

// Log level priorities
const LOG_LEVELS: Record<LogLevel, number> = {
  debug: 0,
  info: 1,
  warn: 2,
  error: 3,
};

// Minimum level to record (debug in dev, info in prod)
const minLevel: LogLevel = import.meta.env.DEV ? 'debug' : 'info';

// Error batch for sending to backend
let errorBatch: LogEntry[] = [];
let errorBatchTimer: ReturnType<typeof setTimeout> | null = null;

class Logger {
  private logs: LogEntry[] = [];
  private listeners: Set<(entry: LogEntry) => void> = new Set();

  constructor() {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) this.logs = JSON.parse(stored);
    } catch {
      this.logs = [];
    }
  }

  private saveLogs(): void {
    try {
      if (this.logs.length > MAX_LOGS) {
        this.logs = this.logs.slice(-MAX_LOGS);
      }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this.logs));
    } catch (e) {
      // localStorage full - clear older logs
      if (e instanceof DOMException && e.name === 'QuotaExceededError') {
        this.logs = this.logs.slice(-Math.floor(MAX_LOGS / 2));
        try {
          localStorage.setItem(STORAGE_KEY, JSON.stringify(this.logs));
        } catch {
          // Give up on persistence
        }
      }
    }
  }

  private log(level: LogLevel, category: string, message: string, data?: unknown): void {
    if (LOG_LEVELS[level] < LOG_LEVELS[minLevel]) return;

    const entry: LogEntry = {
      timestamp: new Date().toISOString(),
      level,
      category,
      message,
      data: data !== undefined ? this.sanitizeData(data) : undefined,
    };

    // Capture stack trace for errors
    if (level === 'error' && data instanceof Error) {
      entry.stack = data.stack;
    }

    this.logs.push(entry);
    this.saveLogs();

    // Queue error logs for backend persistence
    if (level === 'error') {
      this.queueErrorForBackend(entry);
    }

    // Notify listeners
    this.listeners.forEach(listener => {
      try {
        listener(entry);
      } catch {
        // Ignore listener errors
      }
    });

    // Also log to console
    const consoleMethod = level === 'debug' ? 'log' : level;
    const prefix = `[${category}]`;
    if (data !== undefined) {
      console[consoleMethod](prefix, message, data);
    } else {
      console[consoleMethod](prefix, message);
    }
  }

  private sanitizeData(data: unknown): unknown {
    // Prevent circular references and limit depth
    try {
      return JSON.parse(JSON.stringify(data, (key, value) => {
        if (typeof value === 'function') return '[Function]';
        if (Array.isArray(value) && value.length > 100) {
          return [...value.slice(0, 100), `... ${value.length - 100} more items`];
        }
        if (value instanceof HTMLElement) return '[HTMLElement]';
        return value;
      }));
    } catch {
      return String(data);
    }
  }

  // Public logging methods
  debug(category: string, message: string, data?: unknown): void {
    this.log('debug', category, message, data);
  }

  info(category: string, message: string, data?: unknown): void {
    this.log('info', category, message, data);
  }

  warn(category: string, message: string, data?: unknown): void {
    this.log('warn', category, message, data);
  }

  error(category: string, message: string, data?: unknown): void {
    this.log('error', category, message, data);
  }

  // Get logs (optionally the last N). Read by the LogViewer panel.
  getLogs(options?: { limit?: number }): LogEntry[] {
    const result = [...this.logs];
    return options?.limit ? result.slice(-options.limit) : result;
  }

  // Clear all logs
  clear(): void {
    this.logs = [];
    localStorage.removeItem(STORAGE_KEY);
  }

  // Download logs as file
  download(format: 'json' | 'text' = 'json'): void {
    const content =
      format === 'json'
        ? JSON.stringify(this.logs, null, 2)
        : this.logs
            .map(log => {
              const data = log.data ? ` | ${JSON.stringify(log.data)}` : '';
              const stack = log.stack ? `\n${log.stack}` : '';
              return `${log.timestamp} | ${log.level.toUpperCase().padEnd(5)} | ${log.category} | ${log.message}${data}${stack}`;
            })
            .join('\n');
    const blob = new Blob([content], { type: format === 'json' ? 'application/json' : 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `pfactory-logs-${new Date().toISOString().split('T')[0]}.${format === 'json' ? 'json' : 'txt'}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  // Subscribe to new log entries
  subscribe(listener: (entry: LogEntry) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  // Get log statistics
  getStats(): { total: number; byLevel: Record<LogLevel, number> } {
    const byLevel: Record<LogLevel, number> = { debug: 0, info: 0, warn: 0, error: 0 };
    this.logs.forEach(log => {
      byLevel[log.level]++;
    });
    return { total: this.logs.length, byLevel };
  }

  // Queue error for backend persistence
  private queueErrorForBackend(entry: LogEntry): void {
    errorBatch.push(entry);
    if (!errorBatchTimer) {
      errorBatchTimer = setTimeout(() => {
        this.flushErrorsToBackend();
      }, ERROR_BATCH_INTERVAL);
    }
  }

  // Send queued errors to backend
  private async flushErrorsToBackend(): Promise<void> {
    if (errorBatch.length === 0) return;

    if (errorBatchTimer) {
      clearTimeout(errorBatchTimer);
      errorBatchTimer = null;
    }

    const batch = [...errorBatch];
    errorBatch = [];

    try {
      const response = await fetch('/api/logs/frontend', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          entries: batch.map(entry => ({
            timestamp: entry.timestamp,
            level: entry.level,
            category: entry.category,
            message: entry.message,
            data: entry.data ? this.sanitizeData(entry.data) : null,
            stack: entry.stack || null,
          })),
        }),
      });

      if (!response.ok) {
        errorBatch = [...batch, ...errorBatch];
        console.warn('[Logger] Failed to send errors to backend:', response.status);
      }
    } catch (err) {
      errorBatch = [...batch, ...errorBatch];
      console.warn('[Logger] Failed to send errors to backend:', err);
    }
  }
}

// Singleton instance
export const logger = new Logger();

// Global error handler to capture uncaught errors
if (typeof window !== 'undefined') {
  window.addEventListener('error', (event) => {
    logger.error('window', 'Uncaught error', {
      message: event.message,
      filename: event.filename,
      lineno: event.lineno,
      colno: event.colno,
      error: event.error,
    });
  });

  window.addEventListener('unhandledrejection', (event) => {
    logger.error('window', 'Unhandled promise rejection', {
      reason: event.reason,
    });
  });

  // Flush pending errors before page unload (sendBeacon survives unload)
  window.addEventListener('beforeunload', () => {
    if (errorBatch.length > 0) {
      const payload = JSON.stringify({
        entries: errorBatch.map(entry => ({
          timestamp: entry.timestamp,
          level: entry.level,
          category: entry.category,
          message: entry.message,
          data: entry.data || null,
          stack: entry.stack || null,
        })),
      });
      navigator.sendBeacon('/api/logs/frontend', payload);
    }
  });
}

// Helper to create category-specific loggers
export function createLogger(category: string) {
  return {
    debug: (message: string, data?: unknown) => logger.debug(category, message, data),
    info: (message: string, data?: unknown) => logger.info(category, message, data),
    warn: (message: string, data?: unknown) => logger.warn(category, message, data),
    error: (message: string, data?: unknown) => logger.error(category, message, data),
  };
}

export default logger;
