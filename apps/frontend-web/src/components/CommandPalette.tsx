// @factory/ui — Command palette (⌘K). Canonical shared component; vendored
// byte-identical into each portal (see README.md). Jump to any view or task and
// run actions from the keyboard. Self-contained: the host assembles the flat
// command list (navigation + actions) and supplies the task lane one of two
// ways — a local `tasks` list (filtered here by case-insensitive substring), or
// an async `onSearch(q)` that returns already-ranked results (federated search,
// #149; debounced, stale-response-guarded). role="dialog" + a listbox of
// role="option" rows, Escape to close, ↑↓ to move.
import { useEffect, useMemo, useRef, useState } from 'react';
import type { ComponentType } from 'react';

export interface PaletteCommand {
  id: string;
  group: 'Go to' | 'Actions';
  label: string;
  keywords?: string;
  Icon?: ComponentType<{ className?: string }>;
  run: () => void;
}

export interface PaletteTask {
  id: string;
  title: string;
  hint?: string; // right-aligned context, e.g. a status or portal
}

type Row =
  | { kind: 'cmd'; group: string; cmd: PaletteCommand }
  | { kind: 'task'; group: string; task: PaletteTask };

const has = (hay: string, q: string) => hay.toLowerCase().includes(q);

export function CommandPalette({
  open,
  onClose,
  commands,
  tasks = [],
  onOpenTask,
  onSearch,
}: {
  open: boolean;
  onClose: () => void;
  commands: PaletteCommand[];
  tasks?: PaletteTask[];
  onOpenTask: (task: PaletteTask) => void;
  onSearch?: (q: string) => Promise<PaletteTask[]>;
}) {
  const [q, setQ] = useState('');
  const [sel, setSel] = useState(0);
  const [results, setResults] = useState<PaletteTask[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQ('');
      setSel(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  // Federated mode (#149): when the host supplies onSearch, debounce the query
  // and render its ranked results; a stale in-flight response is discarded so
  // the list always reflects the latest keystroke. No-op when onSearch is unset
  // (the palette then filters the local `tasks` list, unchanged).
  useEffect(() => {
    if (!onSearch) return;
    const needle = q.trim();
    if (!open || !needle) {
      setResults([]);
      return;
    }
    let stale = false;
    const timer = setTimeout(() => {
      onSearch(needle)
        .then((r) => {
          if (!stale) setResults(r);
        })
        .catch(() => {
          if (!stale) setResults([]);
        });
    }, 140);
    return () => {
      stale = true;
      clearTimeout(timer);
    };
  }, [q, open, onSearch]);

  const rows: Row[] = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const cmdRows: Row[] = commands
      .filter((c) => !needle || has(`${c.label} ${c.keywords ?? ''}`, needle))
      .map((c) => ({ kind: 'cmd', group: c.group, cmd: c }));
    const matched = onSearch
      ? results
      : tasks.filter((t) => has(t.title, needle)).slice(0, 8);
    const taskRows: Row[] = !needle
      ? []
      : matched.map((t) => ({ kind: 'task', group: 'Tasks', task: t }));
    return [...taskRows, ...cmdRows];
  }, [q, commands, tasks, results, onSearch]);

  useEffect(() => {
    setSel((s) => Math.max(0, Math.min(s, rows.length - 1)));
  }, [rows.length]);

  if (!open) return null;

  const runRow = (row: Row | undefined) => {
    if (!row) return;
    if (row.kind === 'cmd') row.cmd.run();
    else onOpenTask(row.task);
    onClose();
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSel((s) => (rows.length ? (s + 1) % rows.length : 0));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSel((s) => (rows.length ? (s - 1 + rows.length) % rows.length : 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      runRow(rows[sel]);
    }
  };

  let lastGroup = '';

  return (
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions, jsx-a11y/click-events-have-key-events -- backdrop dismiss; keyboard close (Escape) is handled on the focused input below
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/55 pt-[11vh] backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="flex max-h-[70vh] w-[min(620px,92vw)] flex-col overflow-hidden rounded-xl border border-border bg-popover shadow-2xl"
      >
        <div className="flex items-center gap-3 border-b border-border px-4 py-3">
          <span className="h-3.5 w-3.5 flex-none rounded-full border-2 border-muted-foreground" />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => { setQ(e.target.value); }}
            onKeyDown={onKey}
            placeholder="Search tasks, or jump to a view…"
            className="flex-1 bg-transparent text-base text-foreground outline-none placeholder:text-muted-foreground"
            role="combobox"
            aria-expanded
            aria-controls="cmdk-list"
            autoComplete="off"
            spellCheck={false}
          />
          <kbd className="rounded border border-border bg-muted px-1.5 font-mono text-xs text-muted-foreground">esc</kbd>
        </div>

        <div id="cmdk-list" role="listbox" className="overflow-y-auto p-1.5">
          {rows.length === 0 && (
            <div className="px-3 py-5 text-center text-sm text-muted-foreground">
              No matches — try a view, an action, or a task.
            </div>
          )}
          {rows.map((row, i) => {
            const header = row.group !== lastGroup ? ((lastGroup = row.group), row.group) : null;
            const selected = i === sel;
            return (
              <div key={row.kind === 'cmd' ? row.cmd.id : `t-${row.task.id}`}>
                {header && (
                  <div className="px-2 pb-1 pt-2 font-mono text-[0.62rem] uppercase tracking-wider text-muted-foreground">
                    {header}
                  </div>
                )}
                <button
                  role="option"
                  aria-selected={selected}
                  className={`flex w-full items-center gap-3 rounded-md px-2 py-2 text-left text-sm ${
                    selected ? 'bg-muted text-foreground' : 'text-foreground/90'
                  }`}
                  onMouseMove={() => { setSel(i); }}
                  onClick={() => { runRow(row); }}
                >
                  <span className={`flex h-4 w-4 flex-none items-center justify-center ${selected ? 'text-primary' : 'text-muted-foreground'}`}>
                    {row.kind === 'cmd' && row.cmd.Icon ? <row.cmd.Icon className="h-4 w-4" /> : null}
                  </span>
                  <span className="min-w-0 flex-1 truncate">
                    {row.kind === 'cmd' ? row.cmd.label : row.task.title}
                  </span>
                  {row.kind === 'task' && row.task.hint && (
                    <span className="flex-none font-mono text-[0.62rem] uppercase tracking-wider text-muted-foreground">
                      {row.task.hint}
                    </span>
                  )}
                </button>
              </div>
            );
          })}
        </div>

        <div className="flex gap-4 border-t border-border px-4 py-2 font-mono text-[0.68rem] text-muted-foreground">
          <span><b className="text-foreground/70">↑↓</b> navigate</span>
          <span><b className="text-foreground/70">↵</b> open</span>
          <span><b className="text-foreground/70">esc</b> close</span>
        </div>
      </div>
    </div>
  );
}
