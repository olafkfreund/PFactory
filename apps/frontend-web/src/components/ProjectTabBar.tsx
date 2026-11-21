import { useEffect, useState } from 'react';
import { Settings, HelpCircle, Sun, Moon } from 'lucide-react';
import { cn } from '../lib/utils';
import { apiRequest } from '../lib/api-client';
import { Button } from './ui/button';
import { Separator } from './ui/separator';
import { SortableProjectTab } from './SortableProjectTab';
import { ProjectBreadcrumb } from './ProjectBreadcrumb';
import { PortalSwitcher } from './PortalSwitcher';
import { ClaudeCodeStatusBadge } from './ClaudeCodeStatusBadge';
import { CLIToolStatusBadge } from './CLIToolStatusBadge';
import { OpenAIEndpointsStatusBadge } from './OpenAIEndpointsStatusBadge';
import { useProjectStore } from '../stores/project-store';
import { projectSlugs } from '../lib/project-route';
import { useSettingsStore, saveSettings } from '../stores/settings-store';
import type { Project } from '../shared/types';

interface ProjectTabBarProps {
  projects: Project[];
  activeProjectId: string | null;
  onProjectSelect: (projectId: string) => void;
  onProjectClose: (projectId: string) => void;
  /** Canonical slug of the project the URL shows */
  currentSlug?: string;
  /** Open the Projects page (add / delete live there) */
  onViewAllProjects: () => void;
  className?: string;
  // Control props for active tab
  onSettingsClick?: () => void;
  onOpenOnboarding?: () => void;
}

export function ProjectTabBar({
  projects,
  activeProjectId,
  onProjectSelect,
  onProjectClose,
  currentSlug,
  onViewAllProjects,
  className,
  onSettingsClick,
  onOpenOnboarding,
}: ProjectTabBarProps) {
  const allProjects = useProjectStore((state) => state.projects);
  const slugs = projectSlugs(allProjects);
  const breadcrumbProjects = allProjects.map((p) => ({ slug: slugs.get(p.id) ?? p.id, name: p.name }));
  const theme = useSettingsStore((state) => state.settings.theme);
  const updateStoreSettings = useSettingsStore((state) => state.updateSettings);
  const isDark = theme === 'dark' || (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);

  const toggleTheme = () => {
    const newTheme = isDark ? 'light' : 'dark';
    updateStoreSettings({ theme: newTheme });
    saveSettings({ theme: newTheme });
  };

  // Fleet 'needs you' count (#148/#149): poll the cockpit (via our same-origin
  // proxy) so the shared switcher shows the same human-blocked badge as every
  // other portal. Best-effort — a failed/absent cockpit just yields 0.
  const [needsCount, setNeedsCount] = useState(0);
  useEffect(() => {
    let active = true;
    const load = () => {
      apiRequest<{ count: number }>('/needs-you/count')
        .then((res) => {
          if (active && res.success && res.data) setNeedsCount(res.data.count);
        })
        .catch(() => {
          /* best-effort badge */
        });
    };
    load();
    const timer = setInterval(load, 30000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  // Keyboard shortcuts for tab navigation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Skip if in input fields
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        (e.target as HTMLElement)?.isContentEditable
      ) {
        return;
      }

      const isMod = e.metaKey || e.ctrlKey;
      if (!isMod) return;

      // Cmd/Ctrl + 1-9: Switch to tab N
      if (e.key >= '1' && e.key <= '9') {
        e.preventDefault();
        const index = parseInt(e.key) - 1;
        if (index < projects.length) {
          onProjectSelect(projects[index].id);
        }
        return;
      }

      // Cmd/Ctrl + Tab: Next tab
      // Cmd/Ctrl + Shift + Tab: Previous tab
      if (e.key === 'Tab') {
        e.preventDefault();
        const currentIndex = projects.findIndex((p) => p.id === activeProjectId);
        if (currentIndex === -1 || projects.length === 0) return;

        const nextIndex = e.shiftKey
          ? (currentIndex - 1 + projects.length) % projects.length
          : (currentIndex + 1) % projects.length;
        onProjectSelect(projects[nextIndex].id);
        return;
      }

      // Cmd/Ctrl + W: Close current tab (only if more than one tab)
      if (e.key === 'w' && activeProjectId && projects.length > 1) {
        e.preventDefault();
        onProjectClose(activeProjectId);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [projects, activeProjectId, onProjectSelect, onProjectClose]);

  // Render the bar even when no project tabs are open — the global
  // toolbar on the right (settings, theme, project switcher, add
  // project, status badges) is the user's primary entry point and
  // must always be reachable.

  return (
    <div className={cn(
      'flex items-center border-b border-border bg-background',
      'overflow-x-auto scrollbar-thin scrollbar-thumb-border scrollbar-track-transparent',
      className
    )}>
      <div className="flex items-center flex-1 min-w-0">
        {projects.map((project, index) => {
          const isActiveTab = activeProjectId === project.id;
          return (
            <SortableProjectTab
              key={project.id}
              project={project}
              isActive={isActiveTab}
              canClose={projects.length > 1}
              tabIndex={index}
              onSelect={() => { onProjectSelect(project.id); }}
              onClose={(e) => {
                e.stopPropagation();
                onProjectClose(project.id);
              }}
              // Pass control props only for active tab
              onSettingsClick={isActiveTab ? onSettingsClick : undefined}
            />
          );
        })}
      </div>

      <div className="flex items-center gap-2 px-2 py-1">
        <PortalSwitcher
          current="plan"
          needsCount={needsCount}
          project={currentSlug ? { slug: currentSlug, portals: ['plan', 'build', 'test'] } : undefined}
        />
        <Separator orientation="vertical" className="h-4 mx-0.5" />
        <ProjectBreadcrumb
          projects={breadcrumbProjects}
          currentSlug={currentSlug}
          onSelect={(slug) => {
            const p = allProjects.find((x) => slugs.get(x.id) === slug);
            if (p) onProjectSelect(p.id);
          }}
          onViewAll={onViewAllProjects}
        />
        <Separator orientation="vertical" className="h-4 mx-0.5" />
        {/* Provider / CLI health, grouped into one cohesive status cluster
            (each badge keeps its own hover tooltip). */}
        <div
          className="flex items-center gap-0.5 rounded-lg bg-muted/40 px-1 py-0.5 ring-1 ring-inset ring-border/60"
          aria-label="Provider and CLI status"
        >
          <ClaudeCodeStatusBadge iconOnly onOpenOnboarding={onOpenOnboarding} />
          <CLIToolStatusBadge iconOnly />
          <OpenAIEndpointsStatusBadge iconOnly />
        </div>
        <Separator orientation="vertical" className="h-4 mx-0.5" />
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8"
          onClick={toggleTheme}
          title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
        >
          {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 hover:bg-accent/50 active:bg-accent/70"
          onClick={onSettingsClick}
          title="Settings"
        >
          <Settings className="h-4 w-4" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8"
          title="Help & docs"
          onClick={() =>
            window.open(
              'https://github.com/olafkfreund/PFactory#readme',
              '_blank',
              'noopener,noreferrer',
            )
          }
        >
          <HelpCircle className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
