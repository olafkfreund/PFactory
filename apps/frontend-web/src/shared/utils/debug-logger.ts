/**
 * Debug Logger
 * Only logs when DEBUG=true in environment
 */

const isDebugEnabled = (): boolean => {
  if (import.meta.env.DEV || (window as Window & { DEBUG?: boolean }).DEBUG) {
    return true;
  }
  if (typeof process !== 'undefined' && process.env) {
    return process.env.DEBUG === 'true';
  }
  return false;
};

export const debugLog = (...args: unknown[]): void => {
  if (isDebugEnabled()) {
    console.warn(...args);
  }
};

export const debugWarn = (...args: unknown[]): void => {
  if (isDebugEnabled()) {
    console.warn(...args);
  }
};

export const debugError = (...args: unknown[]): void => {
  if (isDebugEnabled()) {
    console.error(...args);
  }
};
