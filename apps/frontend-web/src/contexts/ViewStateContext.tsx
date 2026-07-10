import type { ReactNode } from 'react';
import { create } from 'zustand';

interface ViewStateValue {
  showArchived: boolean;
  setShowArchived: (show: boolean) => void;
  toggleShowArchived: () => void;
}

/**
 * View state shared across project pages (kanban, etc.).
 * Currently just `showArchived`.
 */
export const useViewState = create<ViewStateValue>((set) => ({
  showArchived: false,
  setShowArchived: (show) => set({ showArchived: show }),
  toggleShowArchived: () => set((s) => ({ showArchived: !s.showArchived })),
}));

// ponytail: a zustand store needs no provider, but App.tsx still wraps the tree
// in <ViewStateProvider>. Kept as a no-op passthrough so App.tsx needn't change.
export function ViewStateProvider({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
