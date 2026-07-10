/**
 * Toast Hook
 *
 * Minimal module-level toast store. Exposes the same `toast()` / `useToast()`
 * API as the shadcn reducer version, without the action/dispatch machinery.
 */
import * as React from 'react';

import type { ToastActionElement, ToastProps } from '../components/ui/toast';

const TOAST_LIMIT = 1;
// ponytail: was 1_000_000ms (~16min) — a known bug-smell. A dismissed toast is
// already invisible (open:false); this only delays dropping the closed entry
// from the array. 5s is plenty. Bump if a toast must re-open within the window.
const TOAST_REMOVE_DELAY = 5000;

type ToasterToast = ToastProps & {
  id: string;
  title?: React.ReactNode;
  description?: React.ReactNode;
  action?: ToastActionElement;
};

type Toast = Omit<ToasterToast, 'id'>;

let count = 0;
function genId() {
  count = (count + 1) % Number.MAX_SAFE_INTEGER;
  return count.toString();
}

let toasts: ToasterToast[] = [];
const listeners = new Set<(toasts: ToasterToast[]) => void>();
const removeTimeouts = new Map<string, ReturnType<typeof setTimeout>>();

function emit() {
  listeners.forEach((listener) => listener(toasts));
}

function scheduleRemove(id: string) {
  if (removeTimeouts.has(id)) return;
  const timeout = setTimeout(() => {
    removeTimeouts.delete(id);
    toasts = toasts.filter((t) => t.id !== id);
    emit();
  }, TOAST_REMOVE_DELAY);
  removeTimeouts.set(id, timeout);
}

function dismiss(toastId?: string) {
  const ids = toastId ? [toastId] : toasts.map((t) => t.id);
  toasts = toasts.map((t) =>
    toastId === undefined || t.id === toastId ? { ...t, open: false } : t
  );
  ids.forEach(scheduleRemove);
  emit();
}

function toast(props: Toast) {
  const id = genId();

  const update = (next: ToasterToast) => {
    toasts = toasts.map((t) => (t.id === id ? { ...t, ...next } : t));
    emit();
  };

  toasts = [
    {
      ...props,
      id,
      open: true,
      onOpenChange: (open) => {
        if (!open) dismiss(id);
      },
    },
    ...toasts,
  ].slice(0, TOAST_LIMIT);
  emit();

  return { id, dismiss: () => dismiss(id), update };
}

function useToast() {
  const [state, setState] = React.useState<ToasterToast[]>(toasts);

  React.useEffect(() => {
    listeners.add(setState);
    return () => {
      listeners.delete(setState);
    };
  }, []);

  return { toasts: state, toast, dismiss };
}

export { useToast, toast };
