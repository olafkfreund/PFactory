import { useState, useEffect, useMemo, useCallback } from 'react';
import { Routes, Route, Navigate, Link, useLocation, useNavigate, useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { TooltipProvider } from './components/ui/tooltip';
import { Toaster } from './components/ui/toaster';
import { AppShell, type AppShellLabels } from './components/factory-ui/AppShell';
import type { PipelineStripProps } from './components/factory-ui/PipelineStrip';
import { usePFactoryShellAdapter, type SidebarView } from './components/Sidebar';
import { CommandPalette, type PaletteCommand, type PaletteTask } from './components/CommandPalette';
import { ClaudeCodeStatusBadge } from './components/ClaudeCodeStatusBadge';
import { CLIToolStatusBadge } from './components/CLIToolStatusBadge';
import { OpenAIEndpointsStatusBadge } from './components/OpenAIEndpointsStatusBadge';
import { RateLimitIndicator } from './components/RateLimitIndicator';
import { apiRequest } from './lib/api-client';
import { TerminalGrid } from './components/TerminalGrid';
import { Worktrees } from './components/Worktrees';
import { Context } from './components/context/Context';
import { GitHubIssues } from './components/GitHubIssues';
import { GitHubPRs } from './components/github-prs/GitHubPRs';
import { Changelog } from './components/changelog/Changelog';
import { Insights } from './components/Insights';
import { ChatLauncher } from './components/ChatLauncher';
import { AgentTools } from './components/AgentTools';
import { SkillsPage } from './components/SkillsPage';
import { CloudAssessmentPage } from './components/settings/sections/CloudAssessmentPage';
import { VisualReportsPage } from './components/settings/sections/VisualReportsPage';
import { WelcomeScreen } from './components/WelcomeScreen';
import { AddProjectModal } from './components/AddProjectModal';
import { ProjectSettingsPage, PreferencesPage } from './components/settings';
import { TaskCreationWizard } from './components/TaskCreationWizard';
import { CloudCheckDialog } from './components/CloudCheckDialog';
import { TaskDetailModal } from './components/task-detail';
import { PFactoryPortal } from './components/pfactory/PFactoryPortal';
import { PlanningView } from './components/planning/PlanningView';
import { OnboardingWizard } from './components/onboarding';
import { ProjectSwitchLoadingModal } from './components/ProjectSwitchLoadingModal';
import { LoginPage } from './pages/LoginPage';
import { EditorPage } from './pages/EditorPage';
import { ConsolePage } from './pages/ConsolePage';
import { ProjectsPage } from './pages/ProjectsPage';
import { ViewStateProvider } from './contexts/ViewStateContext';
import { PipelineStripContext } from './contexts/PipelineStripContext';
import { useProjectStore, loadProjects } from './stores/project-store';
import { useTaskStore, loadTasks } from './stores/task-store';
import { useSettingsStore, loadSettings, saveSettings } from './stores/settings-store';
import { useAuthStore } from './stores/auth-store';
import { useIpcListeners } from './hooks/useIpc';
import { useEnsureProjects } from './hooks/useEnsureProjects';
import { collisionProjects, findProjectByRef, parseRoute, projectPath, projectSlugs } from './lib/project-route';
import { UI_SCALE_MIN, UI_SCALE_MAX, UI_SCALE_DEFAULT } from './shared/constants';
import type { Task, Project } from './shared/types';

const RETURN_TO_KEY = 'pf-return-to';
const SIDEBAR_COLLAPSED_KEY = 'pfactory-sidebar-collapsed';
const isMac = typeof navigator !== 'undefined' && /Mac|iPod|iPhone|iPad/.test(navigator.platform);

function RedirectToLogin() {
  const { pathname, search } = useLocation();
  try {
    if (pathname !== '/' && pathname !== '/login') {
      sessionStorage.setItem(RETURN_TO_KEY, pathname + search);
    }
  } catch {
    // sessionStorage may be unavailable
  }
  return <Navigate to="/login" replace />;
}

function LegacyConsoleRedirect() {
  const { t } = useTranslation('welcome');
  const { projectId, specId } = useParams<{ projectId: string; specId?: string }>();
  const { search, hash } = useLocation();
  const ready = useEnsureProjects();
  const projects = useProjectStore((state) => state.projects);
  const error = useProjectStore((state) => state.error);
  if (!ready) return null;
  if (error) return <div role="alert">{error} <button onClick={() => void loadProjects()}>{t('projectsPage.retry')}</button></div>;
  const project = projects.find((p) => p.id.toLowerCase() === projectId?.toLowerCase());
  if (!project) return <div role="alert">{t('projectsPage.notFound', { ref: projectId })}</div>;
  const slug = projectSlugs(projects).get(project.id) ?? project.id;
  return <Navigate to={`${projectPath(slug, 'console')}${specId ? `/${encodeURIComponent(specId)}` : ''}${search}${hash}`} replace />;
}

function AuthenticatedApp() {
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const route = useMemo(() => parseRoute(pathname), [pathname]);
  const [projectsLoaded, setProjectsLoaded] = useState(false);

  // Stores
  const projects = useProjectStore((state) => state.projects);
  const projectError = useProjectStore((state) => state.error);
  const selectedProjectId = useProjectStore((state) => state.selectedProjectId);
  const activeProjectId = useProjectStore((state) => state.activeProjectId);
  const isSwitchingProject = useProjectStore((state) => state.isSwitchingProject);
  const tasks = useTaskStore((state) => state.tasks);
  const settings = useSettingsStore((state) => state.settings);
  const updateStoreSettings = useSettingsStore((state) => state.updateSettings);

  useIpcListeners();

  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [isNewTaskDialogOpen, setIsNewTaskDialogOpen] = useState(false);
  const [isCloudCheckOpen, setIsCloudCheckOpen] = useState(false);
  const [isAddProjectModalOpen, setIsAddProjectModalOpen] = useState(false);
  const [isOnboardingOpen, setIsOnboardingOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [strip, setStrip] = useState<PipelineStripProps | undefined>(undefined);
  const [needsCount, setNeedsCount] = useState(0);
  const [version, setVersion] = useState<string | undefined>(undefined);
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === 'true';
    } catch {
      return false;
    }
  });

  const handleSidebarCollapsedChange = useCallback((collapsed: boolean) => {
    setSidebarCollapsed(collapsed);
    try {
      localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(collapsed));
    } catch {
      // storage may be unavailable
    }
  }, []);

  const slugs = useMemo(() => projectSlugs(projects), [projects]);
  const switcherProjects = useMemo(
    () => projects.map((p) => ({ slug: slugs.get(p.id) ?? p.id, name: p.name, repoLabel: p.path })),
    [projects, slugs]
  );
  const paletteProjects = useMemo(
    () => projects.map((p) => ({ slug: slugs.get(p.id) ?? p.id, name: p.name, path: p.path })),
    [projects, slugs]
  );
  const urlProject = route.kind === 'project' && !projectError ? findProjectByRef(projects, route.ref) : undefined;
  const urlSlug = urlProject ? slugs.get(urlProject.id) : undefined;
  const activeView: SidebarView =
    route.kind === 'project' ? route.view : route.kind === 'pfactory' ? 'pfactory' : 'planning';
  const urlProjectId = urlProject?.id;
  const inSync = !!urlProjectId && activeProjectId === urlProjectId && selectedProjectId === urlProjectId;
  const selectedProject = inSync ? urlProject : undefined;
  const navProject = urlProject;

  const goProject = useCallback(
    (id: string, view?: SidebarView | string) => {
      void navigate(projectPath(slugs.get(id) ?? id, view));
    },
    [navigate, slugs]
  );

  const setActiveView = useCallback(
    (view: SidebarView) => {
      if (view === 'pfactory') {
        void navigate('/pfactory');
        return;
      }
      if (navProject) goProject(navProject.id, view);
    },
    [navProject, goProject, navigate]
  );

  const openProjectSettings = useCallback(() => {
    if (navProject) goProject(navProject.id, 'settings');
    else void navigate('/preferences');
  }, [navProject, goProject, navigate]);

  const openPreferences = useCallback(() => {
    void navigate('/preferences');
  }, [navigate]);

  useEffect(() => {
    if (!projectsLoaded || projectError || !urlProjectId) return;
    const state = useProjectStore.getState();
    if (state.activeProjectId === urlProjectId && state.selectedProjectId === urlProjectId) return;
    state.openProjectTab(urlProjectId);
    if (useProjectStore.getState().selectedProjectId !== urlProjectId) {
      useProjectStore.getState().selectProject(urlProjectId);
    }
  }, [projectsLoaded, projectError, urlProjectId, activeProjectId, selectedProjectId]);

  useEffect(() => {
    if (route.kind === 'project' && route.view === 'pfactory' && projectsLoaded && urlProject) {
      void navigate('/pfactory', { replace: true });
      return;
    }
    if (route.kind === 'project' && urlSlug && route.ref !== urlSlug) {
      void navigate(projectPath(urlSlug, route.view) + search, { replace: true });
    }
  }, [route, urlSlug, urlProject, projectsLoaded, search, navigate]);

  useEffect(() => {
    if (route.kind !== 'home' || pathname !== '/') return;
    try {
      const to = sessionStorage.getItem(RETURN_TO_KEY);
      sessionStorage.removeItem(RETURN_TO_KEY);
      if (to?.startsWith('/') && !to.startsWith('//')) void navigate(to, { replace: true });
    } catch {
      // sessionStorage may be unavailable
    }
  }, [route.kind, pathname, navigate]);

  const selectedTask = useMemo(() => {
    if (!selectedTaskId) return null;
    const task = tasks.find((t) => t.id === selectedTaskId || t.specId === selectedTaskId) ?? null;
    if (window.DEBUG && task) {
      console.log('[App] selectedTask derived:', task.id, 'status:', task.status, 'subtasks:', task.subtasks?.length);
    }
    return task;
  }, [selectedTaskId, tasks]);

  // Command palette (⌘K / Ctrl-K) and ⌘1–9 project switching
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPaletteOpen((o) => !o);
        return;
      }
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        (e.target as HTMLElement)?.isContentEditable
      ) {
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key >= '1' && e.key <= '9') {
        const index = parseInt(e.key, 10) - 1;
        const target = projects[index];
        if (target) {
          e.preventDefault();
          goProject(target.id, route.kind === 'project' ? route.view : undefined);
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
    };
  }, [projects, goProject, route]);

  // Needs-you count polling (#148/#149)
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

  useEffect(() => {
    void window.API?.getAppVersion?.().then((v: unknown) => {
      if (typeof v === 'string' && v) setVersion(v);
    });
  }, []);

  const paletteCommands: PaletteCommand[] = useMemo(() => {
    const views: { id: SidebarView; label: string }[] = [
      { id: 'pfactory', label: 'Plans' },
      { id: 'planning', label: 'Planning' },
      { id: 'editor', label: 'Editor' },
      { id: 'insights', label: 'Insights' },
      { id: 'terminals', label: 'Terminals' },
      { id: 'agent-tools', label: 'Agent Tools' },
      { id: 'skills', label: 'Skills' },
      { id: 'changelog', label: 'Changelog' },
      { id: 'worktrees', label: 'Worktrees' },
      { id: 'context', label: 'Context' },
      { id: 'cloud', label: 'Cloud' },
      { id: 'visual-reports', label: 'Visual Reports' },
      { id: 'settings', label: 'Project Settings' },
    ];
    const nav: PaletteCommand[] = views.map((v) => ({
      id: `view-${v.id}`,
      group: 'Go to',
      label: v.label,
      keywords: v.id,
      run: () => {
        setActiveView(v.id);
      },
    }));
    const switchProject: PaletteCommand[] = projects.map((p) => ({
      id: `project-${p.id}`,
      group: 'Go to',
      label: `Switch project: ${p.name}`,
      keywords: `project switch ${slugs.get(p.id) ?? ''}`,
      run: () => {
        goProject(p.id);
      },
    }));
    const actions: PaletteCommand[] = [
      {
        id: 'act-preferences',
        group: 'Actions',
        label: 'Open preferences',
        keywords: 'preferences settings config theme',
        run: openPreferences,
      },
      {
        id: 'act-project-settings',
        group: 'Actions',
        label: 'Open project settings',
        keywords: 'project settings git memory mcp',
        run: openProjectSettings,
      },
    ];
    return [...nav, ...switchProject, ...actions];
  }, [projects, slugs, goProject, setActiveView, openPreferences, openProjectSettings]);

  const rawCockpit: unknown = import.meta.env.VITE_CFACTORY_URL;
  const cockpitUrl = (
    typeof rawCockpit === 'string' && rawCockpit.trim()
      ? rawCockpit
      : 'https://cfactory.freundcloud.org.uk'
  ).replace(/\/+$/, '');
  const searchFleet = useCallback(async (q: string): Promise<PaletteTask[]> => {
    const res = await apiRequest<{
      results: { correlation_key: string; title: string | null; status: string | null }[];
    }>(`/search?q=${encodeURIComponent(q)}`);
    if (!res.success || !res.data) return [];
    return res.data.results.map((r) => ({
      id: r.correlation_key,
      title: r.title ?? `#${r.correlation_key}`,
      hint: r.status ?? undefined,
    }));
  }, []);

  const switchingProjectName = useMemo(() => {
    if (!isSwitchingProject || !activeProjectId) return undefined;
    return projects.find((p) => p.id === activeProjectId)?.name;
  }, [isSwitchingProject, activeProjectId, projects]);

  // Initial load
  useEffect(() => {
    void loadProjects().finally(() => setProjectsLoaded(true));
    void loadSettings();
  }, []);

  // Trigger onboarding only if CLI not installed or auth token missing
  useEffect(() => {
    if (settings.onboardingCompleted !== false) return;

    const checkSetup = async () => {
      try {
        const [versionResult, authResult] = await Promise.all([
          window.API?.checkClaudeCodeVersion?.() ?? { success: false },
          window.API?.getAuthStatus?.() ?? { success: false },
        ]);

        const cliInstalled = versionResult.success && versionResult.data?.installed;
        const hasToken = authResult.success && authResult.data?.hasToken;

        if (cliInstalled && hasToken) {
          const { updateSettings } = useSettingsStore.getState();
          updateSettings({ onboardingCompleted: true });
          void window.API?.saveSettings?.({ onboardingCompleted: true });
        } else {
          setIsOnboardingOpen(true);
        }
      } catch {
        setIsOnboardingOpen(true);
      }
    };

    void checkSetup();
  }, [settings.onboardingCompleted]);

  // Sync i18n language with settings
  const { t, i18n } = useTranslation(['common', 'navigation', 'settings', 'tasks', 'welcome']);
  useEffect(() => {
    if (settings.language && settings.language !== i18n.language) {
      void i18n.changeLanguage(settings.language);
    }
  }, [settings.language, i18n]);

  // Load tasks when project changes
  useEffect(() => {
    const currentProjectId = urlProjectId;
    if (currentProjectId) {
      useTaskStore.getState().clearTasks();
      void loadTasks(currentProjectId);
      setSelectedTaskId(null);
    } else {
      useTaskStore.getState().clearTasks();
    }
  }, [urlProjectId]);

  // Safety timeout: auto-clear stuck switching state after 10 seconds
  useEffect(() => {
    if (!isSwitchingProject) return;
    const timeout = setTimeout(() => {
      useProjectStore.getState().clearSwitchingState();
    }, 10_000);
    return () => clearTimeout(timeout);
  }, [isSwitchingProject]);

  // Apply light/dark mode
  useEffect(() => {
    const root = document.documentElement;

    const applyTheme = () => {
      if (settings.theme === 'dark') {
        root.classList.add('dark');
      } else if (settings.theme === 'light') {
        root.classList.remove('dark');
      } else {
        if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
          root.classList.add('dark');
        } else {
          root.classList.remove('dark');
        }
      }
    };

    applyTheme();

    try {
      localStorage.setItem('pfactory-theme', settings.theme ?? 'system');
    } catch {
      // localStorage may be unavailable
    }

    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handleChange = () => {
      if (settings.theme === 'system') {
        applyTheme();
      }
    };
    mediaQuery.addEventListener('change', handleChange);

    return () => {
      mediaQuery.removeEventListener('change', handleChange);
    };
  }, [settings.theme]);

  // Apply UI scale
  useEffect(() => {
    const root = document.documentElement;
    const scale = settings.uiScale ?? UI_SCALE_DEFAULT;
    const clampedScale = Math.max(UI_SCALE_MIN, Math.min(UI_SCALE_MAX, scale));
    root.setAttribute('data-ui-scale', clampedScale.toString());
  }, [settings.uiScale]);

  const handleTaskClick = (task: Task) => {
    setSelectedTaskId(task.id);
  };

  const handleAddProject = useCallback(() => {
    setIsAddProjectModalOpen(true);
  }, []);

  const handleProjectAdded = (project: Project, needsInit: boolean) => {
    console.log('[Web] Project added:', project.name, 'needs init:', needsInit);
    goProject(project.id);
  };

  const handleOpenInbuiltTerminal = useCallback(
    (id: string, cwd: string) => {
      void window.API.createTerminal({
        id,
        cwd,
        cols: 80,
        rows: 24,
      });
      setActiveView('terminals');
    },
    [setActiveView]
  );

  const { nav, sidebarHeader, openLogoutDialog, dialogs } = usePFactoryShellAdapter(urlProjectId ?? null);

  const shellLabels: AppShellLabels = useMemo(
    () => ({
      brand: t('navigation:shell.brand', 'PFactory'),
      portals: t('navigation:shell.portals', 'Factory portals'),
      portalNames: {
        plan: t('navigation:shell.portalNames.plan', 'Plan'),
        build: t('navigation:shell.portalNames.build', 'Build'),
        test: t('navigation:shell.portalNames.test', 'Test'),
        cockpit: t('navigation:shell.portalNames.cockpit', 'Cockpit'),
        meet: t('navigation:shell.portalNames.meet', 'Meet'),
      },
      navigation: t('navigation:shell.navigation', 'Project navigation'),
      openNavigation: t('navigation:shell.openNavigation', 'Open navigation'),
      closeNavigation: t('navigation:shell.closeNavigation', 'Close navigation'),
      expandSidebar: t('navigation:shell.expandSidebar', 'Expand sidebar'),
      collapseSidebar: t('navigation:shell.collapseSidebar', 'Collapse sidebar'),
      projectSettings: t('navigation:shell.projectSettings', 'Project Settings'),
      theme: t('navigation:shell.theme', 'Theme'),
      themeOptions: {
        light: t('settings:theme.light', 'Light'),
        dark: t('settings:theme.dark', 'Dark'),
        system: t('settings:theme.system', 'System'),
      },
      search: t('navigation:shell.search', 'Search'),
      searchShortcut: isMac ? '⌘K' : 'Ctrl+K',
      needsYou: (count: number) => t('navigation:shell.needsYou', { count, defaultValue: '{{count}} need you' }),
      userMenu: (name: string) => t('navigation:shell.userMenu', { name, defaultValue: 'Account menu for {{name}}' }),
      project: {
        project: t('navigation:sections.project', 'Project'),
        selectProject: t('navigation:shell.selectProject', 'Select project'),
        searchProjects: t('navigation:shell.searchProjects', 'Search projects…'),
        projects: t('navigation:shell.projects', 'Projects'),
        recentProjects: t('navigation:shell.recentProjects', 'Recent'),
        noProjects: t('navigation:shell.noProjects', 'No matching projects'),
        allProjects: t('navigation:shell.allProjects', 'All projects'),
        addProject: t('navigation:shell.addProject', 'Add project'),
      },
      pipeline: {
        pipeline: t('navigation:shell.pipeline', 'Pipeline stages'),
        clearFilter: t('navigation:shell.clearFilter', 'Clear filter'),
        failures: (count: number) => t('tasks:pipeline.failed', { count, defaultValue: '{{count}} failed' }),
        filterFailures: (stage) =>
          t('navigation:shell.filterFailures', {
            stage: stage.label,
            count: stage.failed,
            defaultValue: 'Filter {{stage}} failures ({{count}})',
          }),
      },
    }),
    [t]
  );

  const viewTitle = useMemo(() => {
    if (route.kind === 'preferences') return t('navigation:shell.preferences', 'Preferences');
    if (route.kind === 'projects') return t('navigation:shell.projects', 'Projects');
    if (route.kind === 'pfactory') return t('navigation:items.plans', 'Plans');
    if (route.kind === 'home') return t('navigation:items.planning', 'Planning');
    if (route.view === 'settings') return t('navigation:shell.projectSettings', 'Project Settings');
    if (route.view === 'terminals') return t('navigation:items.terminals', 'Terminal');
    if (route.view === 'insights') return t('navigation:items.chat', 'Chat');
    if (route.view === 'worktrees') return t('navigation:items.worktrees', 'Worktrees');
    const match = nav.find((item) => item.id === route.view);
    return match?.label ?? route.view;
  }, [route, nav, t]);

  const activeNavId = useMemo(() => {
    if (route.kind === 'project') {
      return route.view === 'settings' ? '' : route.view;
    }
    if (route.kind === 'pfactory') return 'pfactory';
    return '';
  }, [route]);

  const themeMode: 'light' | 'dark' | 'system' =
    settings.theme === 'light' || settings.theme === 'dark' || settings.theme === 'system'
      ? settings.theme
      : 'system';

  const handleThemeChange = useCallback(
    (nextTheme: 'light' | 'dark' | 'system') => {
      updateStoreSettings({ theme: nextTheme });
      void saveSettings({ theme: nextTheme });
    },
    [updateStoreSettings]
  );

  const topbarStatus = useMemo(
    () => (
      <div className="flex items-center gap-1" aria-label="Provider and CLI status">
        <ClaudeCodeStatusBadge iconOnly onOpenOnboarding={() => setIsOnboardingOpen(true)} />
        <CLIToolStatusBadge iconOnly />
        <OpenAIEndpointsStatusBadge iconOnly />
        <RateLimitIndicator />
      </div>
    ),
    []
  );

  const userConfig = useMemo(
    () => ({
      name: t('navigation:shell.userName', 'Account'),
      menu: [
        {
          id: 'preferences',
          label: t('navigation:shell.preferences', 'Preferences'),
          onSelect: openPreferences,
        },
        {
          id: 'help',
          label: t('navigation:shell.helpDocs', 'Help & docs'),
          onSelect: () => {
            window.open('https://github.com/olafkfreund/PFactory#readme', '_blank', 'noopener,noreferrer');
          },
        },
        {
          id: 'logout',
          label: t('navigation:actions.logout', 'Log Out'),
          onSelect: openLogoutDialog,
        },
      ],
    }),
    [t, openPreferences, openLogoutDialog]
  );

  const primaryAction = useMemo(
    () => ({
      label: `+ ${t('navigation:actions.newTask', 'Task')}`,
      onClick: () => setIsNewTaskDialogOpen(true),
      disabled: !urlProjectId || !urlProject?.autoBuildPath,
      disabledReason: !urlProjectId
        ? t('navigation:shell.selectProject', 'Select project')
        : !urlProject?.autoBuildPath
          ? t('navigation:messages.initializeToCreateTasks', 'Initialize PFactory to create tasks')
          : undefined,
    }),
    [t, urlProjectId, urlProject?.autoBuildPath]
  );

  const showStrip = route.kind !== 'preferences' && !(route.kind === 'project' && route.view === 'settings');
  const stripContextValue = useMemo(() => ({ setStrip }), [setStrip]);

  return (
    <ViewStateProvider>
      <TooltipProvider>
        <PipelineStripContext.Provider value={stripContextValue}>
          <AppShell
            portal="plan"
            portalProject={urlSlug ? { slug: urlSlug, portals: ['plan', 'build', 'test'] } : undefined}
            nav={nav}
            activeNav={activeNavId}
            onNavigate={(id) => setActiveView(id as SidebarView)}
            projects={switcherProjects}
            currentProject={urlSlug}
            projectSubtitle={urlProject?.path}
            onSelectProject={(slug) => {
              const target = projects.find((p) => (slugs.get(p.id) ?? p.id) === slug);
              if (target) {
                goProject(target.id, route.kind === 'project' ? route.view : undefined);
              }
            }}
            onAllProjects={() => {
              void navigate('/projects');
            }}
            onAddProject={handleAddProject}
            projectSettings={{
              active: route.kind === 'project' && route.view === 'settings',
              onOpen: openProjectSettings,
            }}
            primaryAction={primaryAction}
            view={viewTitle}
            strip={showStrip ? strip : undefined}
            topbarStatus={topbarStatus}
            sidebarHeader={sidebarHeader}
            sidebarCollapsed={sidebarCollapsed}
            onSidebarCollapsedChange={handleSidebarCollapsedChange}
            user={userConfig}
            theme={themeMode}
            onTheme={handleThemeChange}
            onSearch={() => setPaletteOpen(true)}
            needsYou={{
              count: needsCount,
              onOpen: () => {
                window.location.href = `${cockpitUrl}/?filter=needs_human`;
              },
            }}
            version={version}
            labels={shellLabels}
          >
            {/* TerminalGrid stays mounted across project switches and global routes to preserve xterm instances and PTY connections */}
            <div className={route.kind === 'project' && urlProject && activeView === 'terminals' ? 'h-full' : 'hidden'}>
              <TerminalGrid
                projectPath={urlProject?.path}
                onNewTaskClick={() => {
                  setIsNewTaskDialogOpen(true);
                }}
                isActive={route.kind === 'project' && !!urlProject && activeView === 'terminals'}
              />
            </div>

            {/* PlanningView stays mounted across project/global views so PipelineStrip lives at adapter lifetime */}
            {projectsLoaded && !projectError && (route.kind !== 'project' || urlProject) && (
              <div className={selectedProject && activeView === 'planning' ? 'h-full overflow-hidden' : 'hidden'}>
                <PlanningView
                  onTaskClick={handleTaskClick}
                  onNewTaskClick={() => {
                    setIsNewTaskDialogOpen(true);
                  }}
                  focusSessionId={selectedTaskId}
                />
              </div>
            )}

            {route.kind === 'project' && !urlProject && projectsLoaded && projectError && (
              <div role="alert">
                {projectError}{' '}
                <button onClick={() => void loadProjects()}>{t('welcome:projectsPage.retry')}</button>
              </div>
            )}
            {route.kind === 'project' &&
              !urlProject &&
              projectsLoaded &&
              !projectError &&
              collisionProjects(projects, route.ref).length > 0 && (
                <div>
                  <p>{t('welcome:projectsPage.choose')}</p>
                  {collisionProjects(projects, route.ref).map((p) => (
                    <Link key={p.id} to={projectPath(slugs.get(p.id) ?? p.id, route.view)}>
                      {p.name} ({p.id})
                    </Link>
                  ))}
                </div>
              )}
            {route.kind === 'project' &&
              !urlProject &&
              projectsLoaded &&
              !projectError &&
              collisionProjects(projects, route.ref).length === 0 && (
                <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
                  <p>{t('welcome:projectsPage.notFound', { ref: route.ref })}</p>
                  <Link to="/" className="text-sm underline">
                    {t('welcome:projectsPage.backHome')}
                  </Link>
                </div>
              )}
            {selectedProject && (
              <>
                {activeView === 'editor' && <EditorPage projectPath={selectedProject.path} />}
                {activeView === 'worktrees' && <Worktrees projectId={selectedProject.id} />}
                {activeView === 'context' && <Context projectId={selectedProject.id} />}
                {activeView === 'github-issues' && (
                  <GitHubIssues
                    onOpenSettings={openProjectSettings}
                    onNavigateToTask={(taskId) => {
                      setSelectedTaskId(taskId);
                      setActiveView('planning');
                    }}
                  />
                )}
                {activeView === 'github-prs' && (
                  <GitHubPRs onOpenSettings={openProjectSettings} isActive={true} />
                )}
                {activeView === 'changelog' && <Changelog />}
                {activeView === 'insights' && (
                  <Insights projectId={selectedProject.id} onNavigate={setActiveView} />
                )}
                {activeView === 'agent-tools' && <AgentTools />}
                {activeView === 'skills' && <SkillsPage />}
                {activeView === 'cloud' && (
                  <div className="h-full overflow-auto p-6">
                    <CloudAssessmentPage />
                  </div>
                )}
                {activeView === 'visual-reports' && (
                  <div className="h-full overflow-auto p-6">
                    <VisualReportsPage />
                  </div>
                )}
                {activeView === 'settings' && (
                  <ProjectSettingsPage projectId={selectedProject.id} />
                )}
              </>
            )}
            {route.kind === 'preferences' && (
              <PreferencesPage onRerunWizard={() => setIsOnboardingOpen(true)} />
            )}
            {route.kind === 'pfactory' && (
              <div className="h-full overflow-auto p-4">
                <PFactoryPortal />
              </div>
            )}
            {route.kind === 'projects' && projectsLoaded && projectError && (
              <div role="alert">
                {projectError}{' '}
                <button onClick={() => void loadProjects()}>{t('welcome:projectsPage.retry')}</button>
              </div>
            )}
            {route.kind === 'projects' && !projectError && (
              <ProjectsPage
                projects={projects}
                slugs={slugs}
                onAdd={handleAddProject}
                onOpen={(p) => {
                  goProject(p.id);
                }}
              />
            )}
            {route.kind === 'home' && (
              <WelcomeScreen
                projects={projects}
                onNewProject={handleAddProject}
                onOpenProject={handleAddProject}
                onSelectProject={(projectId) => {
                  goProject(projectId);
                }}
              />
            )}
          </AppShell>

          {dialogs}

          <ChatLauncher projectId={selectedProject?.id || ''} onNavigate={setActiveView} />

          <ProjectSwitchLoadingModal open={isSwitchingProject} projectName={switchingProjectName} />

          <Toaster />

          <AddProjectModal
            open={isAddProjectModalOpen}
            onOpenChange={setIsAddProjectModalOpen}
            onProjectAdded={handleProjectAdded}
          />

          <CommandPalette
            open={paletteOpen}
            onClose={() => {
              setPaletteOpen(false);
            }}
            commands={paletteCommands}
            projects={paletteProjects}
            onSwitchProject={(slug) => {
              void navigate(projectPath(slug, route.kind === 'project' ? route.view : undefined));
            }}
            onSearch={searchFleet}
            onOpenTask={(tItem) => {
              window.location.href = `${cockpitUrl}/?task=${encodeURIComponent(tItem.id)}`;
            }}
          />

          {urlProjectId && (
            <TaskCreationWizard
              projectId={urlProjectId}
              open={isNewTaskDialogOpen}
              onOpenChange={setIsNewTaskDialogOpen}
              onCloudInfra={() => setIsCloudCheckOpen(true)}
            />
          )}

          <CloudCheckDialog open={isCloudCheckOpen} onOpenChange={setIsCloudCheckOpen} />

          <TaskDetailModal
            open={!!urlProjectId && selectedTask?.projectId === urlProjectId}
            task={selectedTask?.projectId === urlProjectId ? selectedTask : null}
            projectPath={urlProject?.path}
            onOpenChange={(open) => {
              if (!open) setSelectedTaskId(null);
            }}
            onSwitchToTerminals={() => setActiveView('terminals')}
            onOpenInbuiltTerminal={handleOpenInbuiltTerminal}
          />

          <OnboardingWizard
            open={isOnboardingOpen}
            onOpenChange={setIsOnboardingOpen}
            onOpenTaskCreator={() => setIsNewTaskDialogOpen(true)}
            onOpenSettings={openPreferences}
          />
        </PipelineStripContext.Provider>
      </TooltipProvider>
    </ViewStateProvider>
  );
}

export default function App() {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const checkAuth = useAuthStore((state) => state.checkAuth);
  const [authChecked, setAuthChecked] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void checkAuth().finally(() => {
      if (!cancelled) setAuthChecked(true);
    });
    return () => {
      cancelled = true;
    };
  }, [checkAuth]);

  if (!authChecked) return null;

  return (
    <Routes>
      <Route
        path="/login"
        element={isAuthenticated ? <Navigate to="/" replace /> : <LoginPage />}
      />
      {/* Standalone Live Agent Console — shareable deep link. */}
      <Route
        path="/p/:slug/console/:specId"
        element={isAuthenticated ? <ConsolePage /> : <RedirectToLogin />}
      />
      <Route
        path="/p/:slug/console"
        element={isAuthenticated ? <ConsolePage /> : <RedirectToLogin />}
      />
      <Route
        path="/console/:projectId/:specId"
        element={isAuthenticated ? <LegacyConsoleRedirect /> : <RedirectToLogin />}
      />
      <Route
        path="/console/:projectId"
        element={isAuthenticated ? <LegacyConsoleRedirect /> : <RedirectToLogin />}
      />
      <Route
        path="/*"
        element={isAuthenticated ? <AuthenticatedApp /> : <RedirectToLogin />}
      />
    </Routes>
  );
}
