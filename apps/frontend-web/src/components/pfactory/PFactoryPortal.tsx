/**
 * PFactory portal shell — Task 10 (#11) commit 4.
 *
 * Top-level component that ties the task list to the per-task detail.
 * State-driven (no router) for the MVP — the inherited React app's
 * router stays untouched. A future commit can hoist this into
 * react-router-dom routes once the existing portal's wiring is
 * decoupled.
 *
 * Renders <PFactoryTaskList> by default; on row click, switches to
 * <PFactoryTaskDetail>. A back button returns to the list.
 */

import { useState } from 'react';
import { ChevronLeft } from 'lucide-react';

import { PFactoryTaskDetail } from './PFactoryTaskDetail';
import { PFactoryTaskList } from './PFactoryTaskList';

interface Props {
  /** Test seam threaded into both children. */
  fetchFn?: typeof fetch;
  /** Test seam for the log viewer's WebSocket factory. */
  wsFactory?: (url: string) => WebSocket;
}

export function PFactoryPortal({ fetchFn, wsFactory }: Props) {
  const [selectedSpecId, setSelectedSpecId] = useState<string | null>(null);

  if (selectedSpecId === null) {
    return (
      <div data-testid="pfactory-portal" data-view="list" className="flex flex-col gap-3">
        <header className="border-b border-border pb-2">
          <h1 className="text-xl font-semibold">PFactory Tasks</h1>
          <p className="text-xs text-muted-foreground">
            Read-only view of PFactory workspaces under ~/.pfactory/.
          </p>
        </header>
        <PFactoryTaskList
          onSelectTask={(specId) => setSelectedSpecId(specId)}
          fetchFn={fetchFn}
        />
      </div>
    );
  }

  return (
    <div data-testid="pfactory-portal" data-view="detail" className="flex flex-col gap-3">
      <button
        type="button"
        data-testid="portal-back"
        onClick={() => setSelectedSpecId(null)}
        className="self-start inline-flex items-center gap-1 text-sm text-primary hover:text-primary"
      >
        <ChevronLeft className="h-4 w-4" aria-hidden />
        Back to tasks
      </button>
      <PFactoryTaskDetail
        specId={selectedSpecId}
        fetchFn={fetchFn}
        wsFactory={wsFactory}
      />
    </div>
  );
}
