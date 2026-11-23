import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';

export interface SwitcherProject {
  slug: string;
  name: string;
  repoLabel?: string | undefined;
}

export interface ProjectSwitcherLabels {
  project: string;
  selectProject: string;
  searchProjects: string;
  projects: string;
  recentProjects: string;
  noProjects: string;
  allProjects: string;
  addProject: string;
}

export interface ProjectSwitcherProps {
  projects: SwitcherProject[];
  currentProject?: string | undefined;
  projectSubtitle?: string | undefined;
  onSelectProject(slug: string): void;
  onAllProjects?: (() => void) | undefined;
  onAddProject?: (() => void) | undefined;
  labels: ProjectSwitcherLabels;
}

export function ProjectSwitcher({
  projects, currentProject, projectSubtitle, onSelectProject, onAllProjects, onAddProject, labels,
}: ProjectSwitcherProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  const [recents, setRecents] = useState<string[]>(() => {
    try {
      const saved: unknown = JSON.parse(window.localStorage.getItem('factory-ui:recent-projects') ?? '[]');
      return Array.isArray(saved) ? saved.filter((slug): slug is string => typeof slug === 'string').slice(0, 5) : [];
    } catch { return []; }
  });
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const listId = useId();
  const current = projects.find((project) => project.slug === currentProject);
  const subtitle = projectSubtitle ?? current?.repoLabel;
  const needle = query.trim().toLowerCase();
  const recentProjects = needle ? [] : recents.flatMap((slug) => {
    const project = projects.find((entry) => entry.slug === slug);
    return project ? [project] : [];
  });
  const matches = projects.filter((project) =>
    needle
      ? `${project.name} ${project.slug} ${project.repoLabel ?? ''}`.toLowerCase().includes(needle)
      : !recents.includes(project.slug),
  );
  const options = [...recentProjects, ...matches];
  const index = Math.min(selected, options.length - 1);
  const activeId = index >= 0 ? `${listId}-${index}` : undefined;

  useEffect(() => {
    if (currentProject) {
      setRecents((previous) => [currentProject, ...previous.filter((slug) => slug !== currentProject)].slice(0, 5));
    }
  }, [currentProject]);

  useEffect(() => {
    try { window.localStorage.setItem('factory-ui:recent-projects', JSON.stringify(recents)); } catch { /* storage is optional */ }
  }, [recents]);

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener('pointerdown', dismiss);
    return () => document.removeEventListener('pointerdown', dismiss);
  }, [open]);

  useEffect(() => {
    if (open && activeId) document.getElementById(activeId)?.scrollIntoView?.({ block: 'nearest' });
  }, [open, activeId]);

  const openList = () => {
    setQuery('');
    setSelected(0);
    setOpen(true);
  };

  const finish = (action?: () => void) => {
    setOpen(false);
    trigger.current?.focus();
    action?.();
  };

  const handleKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.nativeEvent.isComposing) return;
    if ((event.key === 'ArrowUp' || event.key === 'ArrowDown') && options.length) {
      event.preventDefault();
      const offset = event.key === 'ArrowDown' ? 1 : -1;
      setSelected((index + options.length + offset) % options.length);
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      const project = options[index];
      if (project) finish(() => onSelectProject(project.slug));
    }
  };

  const option = (project: SwitcherProject, position: number) => (
    <li
      key={project.slug}
      id={`${listId}-${position}`}
      role="option"
      aria-selected={position === index}
      aria-current={project.slug === currentProject || undefined}
      className="f-project-switcher__option"
      onPointerMove={() => setSelected(position)}
      onMouseDown={(event) => event.preventDefault()}
      onClick={() => finish(() => onSelectProject(project.slug))}
    >
      <span className="f-project-switcher__option-main">
        <span className="f-project-switcher__option-name">{project.name}</span>
        {project.repoLabel && <span className="f-project-switcher__repo">{project.repoLabel}</span>}
      </span>
      {project.slug === currentProject && <span className="f-project-switcher__check" aria-hidden>✓</span>}
    </li>
  );

  return (
    <div
      ref={root}
      className="f-project-switcher"
      onBlur={(event) => {
        if (!(event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget))) setOpen(false);
      }}
      onKeyDown={(event) => {
        if (open && event.key === 'Escape' && !event.nativeEvent.isComposing) {
          event.preventDefault();
          event.stopPropagation();
          finish();
        }
      }}
    >
      <button
        ref={trigger}
        type="button"
        className="f-project-switcher__trigger"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={open ? listId : undefined}
        onClick={() => open ? setOpen(false) : openList()}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' && !open) {
            event.preventDefault();
            openList();
          }
        }}
      >
        <span className="f-project-switcher__prefix">{labels.project}</span>
        <span className="f-project-switcher__name" title={current?.name}>
          {current?.name ?? labels.selectProject}
        </span>
        {subtitle && <span className="f-project-switcher__subtitle" title={subtitle}>{subtitle}</span>}
        <span className="f-project-switcher__caret" aria-hidden>▾</span>
      </button>
      {open && (
        <div className="f-project-switcher__popover">
          <input
            autoFocus
            className="f-project-switcher__input"
            role="combobox"
            aria-autocomplete="list"
            aria-expanded
            aria-controls={listId}
            aria-activedescendant={activeId}
            aria-label={labels.searchProjects}
            placeholder={labels.searchProjects}
            autoComplete="off"
            value={query}
            onChange={(event) => { setQuery(event.target.value); setSelected(0); }}
            onKeyDown={handleKey}
          />
          <div className="f-project-switcher__list">
            {!options.length && <div className="f-project-switcher__empty" role="status">{labels.noProjects}</div>}
            <div id={listId} role="listbox" aria-label={labels.projects}>
              {!!recentProjects.length && (
                <ul className="f-project-switcher__options" role="group" aria-label={labels.recentProjects}>
                  <li className="f-project-switcher__group" role="presentation" aria-hidden>{labels.recentProjects}</li>
                  {recentProjects.map(option)}
                </ul>
              )}
              {!!matches.length && (
                <ul className="f-project-switcher__options" role="group" aria-label={labels.projects}>
                  {matches.map((project, i) => option(project, recentProjects.length + i))}
                </ul>
              )}
            </div>
          </div>
          {(onAllProjects || onAddProject) && (
            <div className="f-project-switcher__footer">
              {onAllProjects && <button type="button" className="f-project-switcher__action" onClick={() => finish(onAllProjects)}>{labels.allProjects}</button>}
              {onAddProject && <button type="button" className="f-project-switcher__action" onClick={() => finish(onAddProject)}>{labels.addProject}</button>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
