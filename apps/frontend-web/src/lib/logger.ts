/**
 * Frontend Logger
 *
 * Comprehensive logging service that:
 * - Stores logs in localStorage for persistence
 * - Supports multiple log levels (debug, info, warn, error)
 * - Provides export functionality
 * - Auto-rotates to prevent localStorage overflow
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

// Default minimum level (can be overridden)
let minLevel: LogLevel = import.meta.env.DEV ? 'debug' : 'info';

// Error batch for sending to backend
let errorBatch: LogEntry[] = [];
let errorBatchTimer: ReturnType<typeof setTimeout> | null = null;

class Logger {
  private logs: LogEntry[] = [];
  private listeners: Set<(entry: LogEntry) => void> = new Set();
  private initialized = false;

  constructor() {
    this.loadLogs();
  }

  private loadLogs(): void {
    if (this.initialized) return;

    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        this.logs = JSON.parse(stored);
      }
    } catch {
      this.logs = [];
    }
    this.initialized = true;
  }

  private saveLogs(): void {
    try {
      // Rotate logs if over limit
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

  private shouldLog(level: LogLevel): boolean {
    return LOG_LEVELS[level] >= LOG_LEVELS[minLevel];
  }

  private log(level: LogLevel, category: string, message: string, data?: unknown): void {
    if (!this.shouldLog(level)) return;

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

    // Also log to console in development
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
        // Skip functions
        if (typeof value === 'function') return '[Function]';
        // Skip large arrays
        if (Array.isArray(value) && value.length > 100) {
          return [...value.slice(0, 100), `... ${value.length - 100} more items`];
        }
        // Skip DOM elements
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

  // Get logs with optional filters
  getLogs(options?: {
    level?: LogLevel;
    category?: string;
    since?: Date;
    limit?: number;
  }): LogEntry[] {
    let result = [...this.logs];

    if (options?.level) {
      const levelPriority = LOG_LEVELS[options.level];
      result = result.filter(log => LOG_LEVELS[log.level] >= levelPriority);
    }

    if (options?.category) {
      result = result.filter(log =>
        log.category.toLowerCase().includes(options.category!.toLowerCase())
      );
    }

    if (options?.since) {
      const sinceTime = options.since.getTime();
      result = result.filter(log => new Date(log.timestamp).getTime() >= sinceTime);
    }

    if (options?.limit) {
      result = result.slice(-options.limit);
    }

    return result;
  }

  // Get error logs only
  getErrors(limit = 50): LogEntry[] {
    return this.getLogs({ level: 'error', limit });
  }

  // Clear all logs
  clear(): void {
    this.logs = [];
    localStorage.removeItem(STORAGE_KEY);
  }

  // Export logs as JSON
  exportJSON(): string {
    return JSON.stringify(this.logs, null, 2);
  }

  // Export logs as text
  exportText(): string {
    return this.logs
      .map(log => {
        const data = log.data ? ` | ${JSON.stringify(log.data)}` : '';
        const stack = log.stack ? `\n${log.stack}` : '';
        return `${log.timestamp} | ${log.level.toUpperCase().padEnd(5)} | ${log.category} | ${log.message}${data}${stack}`;
      })
      .join('\n');
  }

  // Download logs as file
  download(format: 'json' | 'text' = 'json'): void {
    const content = format === 'json' ? this.exportJSON() : this.exportText();
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

  // Set minimum log level
  setMinLevel(level: LogLevel): void {
    minLevel = level;
  }

  // Get current log count
  get count(): number {
    return this.logs.length;
  }

  // Get log statistics
  getStats(): { total: number; byLevel: Record<LogLevel, number> } {
    const byLevel: Record<LogLevel, number> = {
      debug: 0,
      info: 0,
      warn: 0,
      error: 0,
    };

    this.logs.forEach(log => {
      byLevel[log.level]++;
    });

    return { total: this.logs.length, byLevel };
  }

  // Queue error for backend persistence
  private queueErrorForBackend(entry: LogEntry): void {
    errorBatch.push(entry);

    // Start batch timer if not already running
    if (!errorBatchTimer) {
      errorBatchTimer = setTimeout(() => {
        this.flushErrorsToBackend();
      }, ERROR_BATCH_INTERVAL);
    }
  }

  // Send queued errors to backend
  async flushErrorsToBackend(): Promise<void> {
    if (errorBatch.length === 0) return;

    // Clear timer
    if (errorBatchTimer) {
      clearTimeout(errorBatchTimer);
      errorBatchTimer = null;
    }

    // Take current batch and clear
    const batch = [...errorBatch];
    errorBatch = [];

    try {
      const response = await fetch('/api/logs/frontend', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
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
        // Put entries back in batch for retry
        errorBatch = [...batch, ...errorBatch];
        console.warn('[Logger] Failed to send errors to backend:', response.status);
      }
    } catch (err) {
      // Put entries back in batch for retry
      errorBatch = [...batch, ...errorBatch];
      console.warn('[Logger] Failed to send errors to backend:', err);
    }
  }

  // Send all pending errors immediately (useful before page unload)
  async sendPendingErrors(): Promise<void> {
    await this.flushErrorsToBackend();
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

  // Flush pending errors before page unload
  window.addEventListener('beforeunload', () => {
    // Use sendBeacon for reliability during page unload
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
