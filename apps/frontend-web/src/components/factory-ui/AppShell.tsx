import { useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { PipelineStrip } from './PipelineStrip';
import type { PipelineStripLabels, PipelineStripProps } from './PipelineStrip';
import { PortalSwitcher } from './PortalSwitcher';
import type { FactoryPortal } from './PortalSwitcher';
import { ProjectSwitcher } from './ProjectSwitcher';
import type { ProjectSwitcherLabels, ProjectSwitcherProps } from './ProjectSwitcher';

type Portal = FactoryPortal;
type Theme = 'light' | 'dark' | 'system';

const themes = ['light', 'dark', 'system'] as const;

export interface AppShellLabels {
  brand: string;
  portals: string;
  portalNames: Record<Portal, string>;
  navigation: string;
  openNavigation: string;
  closeNavigation: string;
  expandSidebar: string;
  collapseSidebar: string;
  projectSettings: string;
  theme: string;
  themeOptions: Record<Theme, string>;
  search: string;
  searchShortcut: string;
  needsYou(count: number): string;
  userMenu(name: string): string;
  project: ProjectSwitcherLabels;
  pipeline: PipelineStripLabels;
}

export interface AppShellProps extends Omit<ProjectSwitcherProps, 'labels'> {
  portal: Portal;
  portalProject?: { slug: string; portals: FactoryPortal[] } | undefined;
  nav: {
    id: string;
    label: string;
    icon: ReactNode;
    badge?: number | undefined;
    disabled?: boolean | undefined;
    external?: { href: string } | undefined;
  }[];
  activeNav: string;
  onNavigate(id: string): void;
  view: string;
  projectSettings?: { active: boolean; onOpen(): void } | undefined;
  primaryAction?: {
    label: string;
    onClick(): void;
    disabled?: boolean | undefined;
    busy?: boolean | undefined;
    disabledReason?: string | undefined;
  } | undefined;
  strip?: PipelineStripProps | undefined;
  topbarStatus?: ReactNode | undefined;
  sidebarHeader?: ReactNode | undefined;
  sidebarFooter?: ReactNode | undefined;
  sidebarCollapsed: boolean;
  onSidebarCollapsedChange(collapsed: boolean): void;
  user?: { name: string; menu: { id: string; label: string; onSelect(): void }[] } | undefined;
  theme: Theme;
  onTheme(theme: Theme): void;
  onSearch?: (() => void) | undefined;
  needsYou?: { count: number; onOpen(): void } | undefined;
  version?: string | undefined;
  labels: AppShellLabels;
  children: ReactNode;
}

export function AppShell({
  portal, portalProject, nav, activeNav, onNavigate, projects, currentProject, onSelectProject, onAllProjects,
  onAddProject, projectSubtitle, projectSettings, primaryAction, view, strip, topbarStatus,
  sidebarHeader, sidebarFooter, sidebarCollapsed, onSidebarCollapsedChange, user, theme,
  onTheme, onSearch, needsYou, version, labels, children,
}: AppShellProps) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const drawerToggle = useRef<HTMLButtonElement>(null);
  const sidebar = useRef<HTMLElement>(null);
  const userTrigger = useRef<HTMLElement>(null);
  const userMenu = useRef<HTMLDetailsElement>(null);
  const sidebarId = useId();
  const sidebarLabel = sidebarCollapsed ? labels.expandSidebar : labels.collapseSidebar;

  useEffect(() => {
    if (!drawerOpen) return;
    sidebar.current?.querySelector<HTMLButtonElement>('.f-side__close')?.focus();
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setDrawerOpen(false);
        requestAnimationFrame(() => drawerToggle.current?.focus());
      } else if (event.key === 'Tab') {
        const controls = Array.from(sidebar.current?.querySelectorAll<HTMLElement>('a[href], button:not(:disabled), [tabindex="0"]') ?? []);
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (!first || !last) return;
        if (event.shiftKey && (document.activeElement === first || !sidebar.current?.contains(document.activeElement))) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && (document.activeElement === last || !sidebar.current?.contains(document.activeElement))) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    const containFocus = (event: FocusEvent) => {
      if (event.target instanceof Node && !sidebar.current?.contains(event.target)) {
        sidebar.current?.querySelector<HTMLButtonElement>('.f-side__close')?.focus();
      }
    };
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('focusin', containFocus);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      document.removeEventListener('focusin', containFocus);
    };
  }, [drawerOpen]);

  useEffect(() => {
    if (!user) return;
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Node && !userMenu.current?.contains(event.target) && userMenu.current) userMenu.current.open = false;
    };
    document.addEventListener('pointerdown', dismiss);
    return () => document.removeEventListener('pointerdown', dismiss);
  }, [user]);

  const closeDrawer = () => {
    if (drawerOpen) {
      setDrawerOpen(false);
      requestAnimationFrame(() => drawerToggle.current?.focus());
    }
  };

  return (
    <div className="f-shell" data-portal={portal}>
      <header className="f-topbar">
        <div className="f-topbar__left">
          <button
            ref={drawerToggle}
            type="button"
            className="f-icon-btn f-topbar__menu-toggle"
            aria-controls={sidebarId}
            aria-expanded={drawerOpen}
            aria-label={drawerOpen ? labels.closeNavigation : labels.openNavigation}
            onClick={() => drawerOpen ? closeDrawer() : setDrawerOpen(true)}
          >
            <span aria-hidden>{drawerOpen ? '×' : '☰'}</span>
          </button>
          <span className="f-brand">
            <span className="f-brand__mark" aria-hidden>F</span>
            <span className="f-brand__name">{labels.brand}</span>
          </span>
          <PortalSwitcher current={portal} project={portalProject} labels={labels.portalNames} ariaLabel={labels.portals} />
        </div>
        <div className="f-topbar__crumbs">
          <ProjectSwitcher
            projects={projects}
            currentProject={currentProject}
            projectSubtitle={projectSubtitle}
            onSelectProject={onSelectProject}
            onAllProjects={onAllProjects}
            onAddProject={onAddProject}
            labels={labels.project}
          />
          <span className="f-topbar__sep" aria-hidden>/</span>
          <span className="f-topbar__view">{view}</span>
        </div>
        <div className="f-topbar__right">
          <div className="f-theme-switch" role="group" aria-label={labels.theme}>
            {themes.map((option) => (
              <button
                key={option}
                type="button"
                className="f-theme-switch__opt"
                aria-pressed={option === theme}
                onClick={() => onTheme(option)}
              >
                {labels.themeOptions[option]}
              </button>
            ))}
          </div>
          {onSearch && (
            <button type="button" className="f-topbar__search" onClick={onSearch}>
              <span className="f-topbar__search-glyph" aria-hidden />
              {labels.search}
              <kbd className="f-topbar__kbd" aria-hidden>{labels.searchShortcut}</kbd>
            </button>
          )}
          {needsYou && (
            <button
              type="button"
              className={`f-topbar__needs${needsYou.count > 0 ? ' f-topbar__needs--active' : ''}`}
              aria-label={labels.needsYou(needsYou.count)}
              title={labels.needsYou(needsYou.count)}
              onClick={needsYou.onOpen}
            >
              <span aria-hidden>⚑</span> {needsYou.count}
            </button>
          )}
          {topbarStatus && <div className="f-topbar__status">{topbarStatus}</div>}
          {version && <span className="f-topbar__version">{version}</span>}
          {user && (
            <details
              ref={userMenu}
              className="f-user-menu"
              onBlur={(event) => {
                if (!(event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget))) event.currentTarget.open = false;
              }}
              onKeyDown={(event) => {
                if (event.key === 'Escape' && event.currentTarget.open) {
                  event.stopPropagation();
                  event.currentTarget.open = false;
                  userTrigger.current?.focus();
                }
              }}
            >
              <summary ref={userTrigger} className="f-user-menu__trigger" aria-label={labels.userMenu(user.name)}>
                {user.name} <span aria-hidden>▾</span>
              </summary>
              <div className="f-user-menu__popover">
                {user.menu.map(({ id, label, onSelect }) => (
                  <button
                    key={id}
                    type="button"
                    className="f-user-menu__item"
                    onClick={(event) => {
                      event.currentTarget.closest('details')?.removeAttribute('open');
                      userTrigger.current?.focus();
                      onSelect();
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </details>
          )}
        </div>
      </header>
      <div className="f-shell__content">
        {strip && !projectSettings?.active && <PipelineStrip {...strip} labels={labels.pipeline} />}
        <div className="f-shell__body">
          {drawerOpen && (
            <button type="button" className="f-side-backdrop is-open" tabIndex={-1} aria-label={labels.closeNavigation} onClick={closeDrawer} />
          )}
          <aside
            ref={sidebar}
            id={sidebarId}
            role={drawerOpen ? 'dialog' : undefined}
            aria-modal={drawerOpen || undefined}
            tabIndex={-1}
            className={`f-side${drawerOpen ? ' is-open' : ''}${sidebarCollapsed ? ' f-side--collapsed' : ''}`}
          >
            <button type="button" className="f-side__close" aria-label={labels.closeNavigation} onClick={closeDrawer}>×</button>
            <button
              type="button"
              className="f-side__toggle"
              aria-label={sidebarLabel}
              title={sidebarLabel}
              aria-expanded={!sidebarCollapsed}
              onClick={() => onSidebarCollapsedChange(!sidebarCollapsed)}
            >
              <span aria-hidden>{sidebarCollapsed ? '»' : '«'}</span>
              <span className="f-side__label">{sidebarLabel}</span>
            </button>
            {sidebarHeader && <div className="f-side__header">{sidebarHeader}</div>}
            <nav className="f-side__nav" aria-label={labels.navigation}>
              {nav.map(({ id, label, icon, badge, disabled, external }) => {
                const attributes = {
                  className: 'f-side__item',
                  'aria-current': activeNav === id ? 'page' as const : undefined,
                  'aria-label': badge === undefined ? label : `${label} (${badge})`,
                  title: label,
                };
                const content = (
                  <>
                    <span className="f-side__icon" aria-hidden>{icon}</span>
                    <span className="f-side__label">{label}</span>
                    {badge !== undefined && <span className="f-side__badge" aria-hidden>{badge}</span>}
                    {external && <span className="f-side__ext-icon" aria-hidden>↗</span>}
                  </>
                );
                return external && !disabled ? (
                  <a key={id} href={external.href} {...attributes} onClick={closeDrawer}>{content}</a>
                ) : (
                  <button key={id} type="button" {...attributes} disabled={disabled} onClick={() => { closeDrawer(); onNavigate(id); }}>
                    {content}
                  </button>
                );
              })}
            </nav>
            {(projectSettings || primaryAction || sidebarFooter) && (
              <div className="f-side__bottom">
                {sidebarFooter && <div className="f-side__footer-content">{sidebarFooter}</div>}
                {projectSettings && (
                  <button
                    type="button"
                    className="f-side__item"
                    aria-label={labels.projectSettings}
                    title={labels.projectSettings}
                    aria-current={projectSettings.active ? 'page' : undefined}
                    onClick={() => { closeDrawer(); projectSettings.onOpen(); }}
                  >
                    <span className="f-side__icon" aria-hidden>⚙</span>
                    <span className="f-side__label">{labels.projectSettings}</span>
                  </button>
                )}
                {primaryAction && (
                  <button
                    type="button"
                    className="f-btn f-btn--primary f-side__primary"
                    aria-label={primaryAction.label}
                    aria-busy={primaryAction.busy}
                    disabled={primaryAction.disabled || primaryAction.busy}
                    title={primaryAction.disabled ? primaryAction.disabledReason ?? primaryAction.label : primaryAction.label}
                    onClick={() => { closeDrawer(); primaryAction.onClick(); }}
                  >
                    <span className="f-side__primary-icon" aria-hidden>+</span>
                    <span className="f-side__primary-label">{primaryAction.label}</span>
                  </button>
                )}
              </div>
            )}
          </aside>
          <main className="f-page" inert={drawerOpen}>{children}</main>
        </div>
      </div>
    </div>
  );
}
