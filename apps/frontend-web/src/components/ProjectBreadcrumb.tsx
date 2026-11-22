// @factory/ui — Project breadcrumb. Canonical shared component; vendored
// byte-identical into each portal (see README.md). Shows the current project
// and a searchable list to switch to another one. It deliberately has no delete
// and no add: those live on the host's Projects page. Router-neutral: the host
// navigates in `onSelect` / `onViewAll`, so this file imports nothing but React.
import { useEffect, useId, useMemo, useRef, useState } from 'react';

export interface BreadcrumbProject {
  slug: string;
  name: string;
}

export function ProjectBreadcrumb({
  projects,
  currentSlug,
  onSelect,
  onViewAll,
}: {
  projects: BreadcrumbProject[];
  currentSlug?: string;
  onSelect: (slug: string) => void;
  onViewAll?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [sel, setSel] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const uid = useId();
  const listId = `${uid}-list`;
  const optId = (slug: string) => `${uid}-opt-${slug}`;
  const current = projects.find((p) => p.slug === currentSlug);

  const matches = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return projects.filter((p) => !needle || `${p.name} ${p.slug}`.toLowerCase().includes(needle));
  }, [projects, q]);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', away);
    return () => { document.removeEventListener('mousedown', away); };
  }, [open]);

  useEffect(() => {
    setSel((s) => Math.max(0, Math.min(s, matches.length - 1)));
  }, [matches.length]);

  // Keep the highlighted row visible when arrow keys move past the list viewport.
  useEffect(() => {
    if (open && matches[sel]) document.getElementById(optId(matches[sel].slug))?.scrollIntoView?.({ block: 'nearest' });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- optId is derived from uid
  }, [open, sel, matches]);

  const toggle = () => {
    setOpen((o) => !o);
    setQ('');
    setSel(0);
  };
  const pick = (slug: string | undefined) => {
    if (!slug) return;
    setOpen(false);
    onSelect(slug);
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
      trigger.current?.focus();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSel((s) => (matches.length ? (s + 1) % matches.length : 0));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSel((s) => (matches.length ? (s - 1 + matches.length) % matches.length : 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      pick(matches[sel]?.slug);
    }
  };

  return (
    <div ref={root} className="relative">
      <button
        ref={trigger}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={toggle}
        className="inline-flex max-w-56 items-center gap-1.5 rounded-md px-2 py-1 text-sm text-foreground hover:bg-muted"
      >
        <span className="text-muted-foreground">Project</span>
        <span className="text-muted-foreground">/</span>
        <span className="truncate font-medium">{current?.name ?? 'Select project'}</span>
        <span aria-hidden className="text-muted-foreground">▾</span>
      </button>
      {open && (
        <div className="absolute left-0 top-full z-50 mt-1 w-72 overflow-hidden rounded-lg border border-border bg-popover shadow-xl">
          <input
            autoFocus
            value={q}
            onChange={(e) => { setQ(e.target.value); setSel(0); }}
            onKeyDown={onKey}
            placeholder="Search projects…"
            aria-label="Search projects"
            role="combobox"
            aria-expanded
            aria-controls={listId}
            aria-activedescendant={matches[sel] ? optId(matches[sel].slug) : undefined}
            autoComplete="off"
            spellCheck={false}
            className="w-full border-b border-border bg-transparent px-3 py-2 text-sm text-foreground outline-none placeholder:text-muted-foreground"
          />
          <ul id={listId} role="listbox" aria-label="Projects" className="max-h-64 overflow-y-auto p-1">
            {matches.length === 0 && (
              <li className="px-2 py-3 text-center text-sm text-muted-foreground">No matching projects</li>
            )}
            {matches.map((p, i) => (
              <li
                key={p.slug}
                id={optId(p.slug)}
                role="option"
                aria-selected={i === sel}
                aria-current={p.slug === currentSlug ? 'true' : undefined}
                onMouseMove={() => { setSel(i); }}
                onClick={() => { pick(p.slug); }}
                className={`flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm ${
                  i === sel ? 'bg-muted text-foreground' : 'text-foreground/90'
                }`}
              >
                <span className="min-w-0 flex-1 truncate">{p.name}</span>
                {p.slug === currentSlug && <span aria-hidden className="text-primary">✓</span>}
              </li>
            ))}
          </ul>
          {onViewAll && (
            <button
              type="button"
              onClick={() => { setOpen(false); onViewAll(); }}
              className="w-full border-t border-border px-3 py-2 text-left text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              All projects…
            </button>
          )}
        </div>
      )}
    </div>
  );
}
