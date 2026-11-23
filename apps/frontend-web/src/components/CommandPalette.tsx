// @factory/ui — Command palette (⌘K). Canonical shared component; vendored
// byte-identical into each portal (see README.md). Jump to any view or task and
// run actions from the keyboard. Self-contained: the host assembles the flat
// command list (navigation + actions) and supplies the task lane one of two
// ways — a local `tasks` list (filtered here by case-insensitive substring), or
// an async `onSearch(q)` that returns already-ranked results (federated search,
// #149; debounced, stale-response-guarded). role="dialog" + a listbox of
// role="option" rows, Escape to close, ↑↓ to move.
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { ComponentType, KeyboardEvent } from 'react';

export interface PaletteCommand {
  id: string;
  group: 'Go to' | 'Actions';
  label: string;
  keywords?: string | undefined;
  Icon?: ComponentType<{ className?: string }> | undefined;
  run: () => void;
}

export interface PaletteTask {
  id: string;
  title: string;
  hint?: string | undefined; // right-aligned context, e.g. a status or portal
}

type Row =
  | { kind: 'cmd'; group: string; cmd: PaletteCommand; keepOpen?: boolean }
  | { kind: 'task'; group: string; task: PaletteTask }
  | { kind: 'project'; group: string; project: { slug: string; name: string } };

const has = (hay: string, q: string) => hay.toLowerCase().includes(q);

export function CommandPalette({
  open,
  onClose,
  commands,
  tasks = [],
  onOpenTask,
  onSearch,
  projects,
  onSwitchProject,
}: {
  open: boolean;
  onClose: () => void;
  commands: PaletteCommand[];
  tasks?: PaletteTask[] | undefined;
  onOpenTask: (task: PaletteTask) => void;
  onSearch?: ((q: string) => Promise<PaletteTask[]>) | undefined;
  projects?: { slug: string; name: string }[] | undefined;
  onSwitchProject?: ((slug: string) => void) | undefined;
}) {
  const [q, setQ] = useState('');
  const [sel, setSel] = useState(0);
  const [projectMode, setProjectMode] = useState(false);
  const [results, setResults] = useState<PaletteTask[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const listId = useId();

  useEffect(() => {
    if (open) {
      previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      setQ('');
      setSel(0);
      setProjectMode(false);
      requestAnimationFrame(() => inputRef.current?.focus());
    } else {
      previousFocus.current?.focus();
    }
  }, [open]);

  // Entering project mode unmounts the clicked "Switch project..." button: put focus back on the input.
  useEffect(() => {
    if (projectMode) inputRef.current?.focus();
  }, [projectMode]);

  // Federated mode (#149): when the host supplies onSearch, debounce the query
  // and render its ranked results; a stale in-flight response is discarded so
  // the list always reflects the latest keystroke. No-op when onSearch is unset
  // (the palette then filters the local `tasks` list, unchanged).
  useEffect(() => {
    if (!onSearch || projectMode) return;
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
  }, [q, open, onSearch, projectMode]);

  const rows: Row[] = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (projectMode) {
      return (projects ?? [])
        .filter((p) => has(p.name, needle) || has(p.slug, needle))
        .map((p) => ({ kind: 'project', group: 'Projects', project: p }));
    }
    const cmdRows: Row[] = commands
      .filter((c) => !needle || has(`${c.label} ${c.keywords ?? ''}`, needle))
      .map((c) => ({ kind: 'cmd', group: c.group, cmd: c }));
    const switchCommand: PaletteCommand = {
      id: 'switch-project', group: 'Actions', label: 'Switch project...',
      keywords: 'project switch change',
      run: () => { setQ(''); setSel(0); setProjectMode(true); },
    };
    if (projects && onSwitchProject && (!needle || has(`${switchCommand.label} ${switchCommand.keywords}`, needle))) {
      cmdRows.push({ kind: 'cmd', group: 'Actions', cmd: switchCommand, keepOpen: true });
    }
    const matched = onSearch
      ? results
      : tasks.filter((t) => has(t.title, needle)).slice(0, 8);
    const taskRows: Row[] = !needle
      ? []
      : matched.map((t) => ({ kind: 'task', group: 'Tasks', task: t }));
    return [...taskRows, ...cmdRows];
  }, [q, commands, tasks, results, onSearch, projects, onSwitchProject, projectMode]);

  useEffect(() => {
    setSel((s) => Math.max(0, Math.min(s, rows.length - 1)));
  }, [rows.length]);

  if (!open) return null;

  const runRow = (row: Row | undefined) => {
    if (!row) return;
    if (row.kind === 'cmd') {
      row.cmd.run();
      if (row.keepOpen) return;
    } else if (row.kind === 'task') onOpenTask(row.task);
    else onSwitchProject?.(row.project.slug);
    onClose();
  };

  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    } else if (e.key === 'Tab') {
      const controls = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('input, button:not([tabindex="-1"])') ?? []);
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (first && last && (e.shiftKey ? document.activeElement === first : document.activeElement === last)) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      }
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
      className="f-cmdk-backdrop"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        onKeyDown={onKey}
        aria-modal="true"
        aria-label="Command palette"
        className="f-cmdk"
      >
        <div className="f-cmdk__header">
          <span className="f-cmdk__glyph" aria-hidden />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => { setQ(e.target.value); }}
            placeholder={projectMode ? 'Switch to project…' : 'Search tasks, or jump to a view…'}
            className="f-cmdk__input"
            role="combobox"
            aria-expanded
            aria-label={projectMode ? 'Switch to project' : 'Search tasks or views'}
            aria-controls={listId}
            aria-activedescendant={rows[sel] ? `${listId}-${sel}` : undefined}
            autoComplete="off"
            spellCheck={false}
          />
          <kbd className="f-cmdk__kbd">esc</kbd>
        </div>

        <div id={listId} role="listbox" className="f-cmdk__list">
          {rows.length === 0 && (
            <div className="f-cmdk__empty">
              {projectMode ? 'No matching projects.' : 'No matches — try a view, an action, or a task.'}
            </div>
          )}
          {rows.map((row, i) => {
            const header = row.group !== lastGroup ? ((lastGroup = row.group), row.group) : null;
            const selected = i === sel;
            return (
              <div key={row.kind === 'cmd' ? row.cmd.id : row.kind === 'task' ? `t-${row.task.id}` : `p-${row.project.slug}`}>
                {header && (
                  <div className="f-cmdk__group">
                    {header}
                  </div>
                )}
                <button
                  id={`${listId}-${i}`}
                  type="button"
                  role="option"
                  tabIndex={-1}
                  aria-selected={selected}
                  className="f-cmdk__row"
                  onMouseMove={() => { setSel(i); }}
                  onClick={() => { runRow(row); }}
                >
                  <span className="f-cmdk__icon" aria-hidden>
                    {row.kind === 'cmd' && row.cmd.Icon ? <row.cmd.Icon className="f-cmdk__icon-svg" /> : null}
                  </span>
                  <span className="f-cmdk__label">
                    {row.kind === 'cmd' ? row.cmd.label : row.kind === 'task' ? row.task.title : row.project.name}
                  </span>
                  {row.kind === 'task' && row.task.hint && (
                    <span className="f-cmdk__hint">
                      {row.task.hint}
                    </span>
                  )}
                </button>
              </div>
            );
          })}
        </div>

        <div className="f-cmdk__footer">
          <span><b>↑↓</b> navigate</span>
          <span><b>↵</b> open</span>
          <span><b>esc</b> close</span>
        </div>
      </div>
    </div>
  );
}
