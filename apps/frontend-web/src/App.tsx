import { useState, useEffect, useMemo, useCallback } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { TooltipProvider } from './components/ui/tooltip';
import { Toaster } from './components/ui/toaster';
import { Sidebar, type SidebarView } from './components/Sidebar';
import { ProjectTabBar } from './components/ProjectTabBar';
import { TerminalGrid } from './components/TerminalGrid';
import { Worktrees } from './components/Worktrees';
import { Context } from './components/context/Context';
import { GitHubIssues } from './components/GitHubIssues';
import { GitHubPRs } from './components/github-prs/GitHubPRs';
import { Changelog } from './components/changelog/Changelog';
import { Insights } from './components/Insights';
import { AgentTools } from './components/AgentTools';
import { SkillsPage } from './components/SkillsPage';
import { CloudAssessmentPage } from './components/settings/sections/CloudAssessmentPage';
import { VisualReportsPage } from './components/settings/sections/VisualReportsPage';
import { WelcomeScreen } from './components/WelcomeScreen';
import { AddProjectModal } from './components/AddProjectModal';
import { AppSettingsDialog } from './components/settings';
import { TaskCreationWizard } from './components/TaskCreationWizard';
import { CloudCheckDialog } from './components/CloudCheckDialog';
import { TaskDetailModal } from './components/task-detail';
import { PFactoryPortal } from './components/pfactory/PFactoryPortal';
import { PlanningView } from './components/planning/PlanningView';
import { OnboardingWizard } from './components/onboarding';
import { LoadingScreen } from './components/LoadingScreen';
import { ProjectSwitchLoadingModal } from './components/ProjectSwitchLoadingModal';
import { LoginPage } from './pages/LoginPage';
import { EditorPage } from './pages/EditorPage';
import { ConsolePage } from './pages/ConsolePage';
import { ViewStateProvider } from './contexts/ViewStateContext';
import { useProjectStore, loadProjects } from './stores/project-store';
import { useTaskStore, loadTasks } from './stores/task-store';
import { useSettingsStore, loadSettings } from './stores/settings-store';
import { useAuthStore } from './stores/auth-store';
import { useIpcListeners } from './hooks/useIpc';
import { UI_SCALE_MIN, UI_SCALE_MAX, UI_SCALE_DEFAULT } from './shared/constants';
import type { Task, Project } from './shared/types';

function AuthenticatedApp() {
  // Loading screen state - show for 5 seconds on every page load
  const [isLoading, setIsLoading] = useState(true);

  const handleLoadingComplete = useCallback(() => {
    setIsLoading(false);
  }, []);

  // Stores
  const projects = useProjectStore((state) => state.projects);
  const selectedProjectId = useProjectStore((state) => state.selectedProjectId);
  const activeProjectId = useProjectStore((state) => state.activeProjectId);
  const openProjectIds = useProjectStore((state) => state.openProjectIds);
  const tabOrder = useProjectStore((state) => state.tabOrder);
  const openProjectTab = useProjectStore((state) => state.openProjectTab);
  const closeProjectTab = useProjectStore((state) => state.closeProjectTab);
  const setActiveProject = useProjectStore((state) => state.setActiveProject);
  const isSwitchingProject = useProjectStore((state) => state.isSwitchingProject);
  const tasks = useTaskStore((state) => state.tasks);
  const settings = useSettingsStore((state) => state.settings);

  // Set up IPC event listeners for real-time task updates via WebSocket
  useIpcListeners();

  // Compute open projects for the tab bar (respecting tab order)
  const openProjects = useMemo(() => {
    // Get projects in tab order first
    const orderedProjects = tabOrder
      .map((id) => projects.find((p) => p.id === id))
      .filter((p): p is Project => p !== undefined && openProjectIds.includes(p.id));

    // Add any open projects not in tabOrder to the end
    const remainingProjects = projects.filter(
      (p) => openProjectIds.includes(p.id) && !tabOrder.includes(p.id)
    );

    return [...orderedProjects, ...remainingProjects];
  }, [projects, openProjectIds, tabOrder]);

  // UI State - store only task ID, derive task from store for live updates
  // IMPORTANT REACTIVITY PATTERN:
  // - selectedTaskId: stable state (only changes when user selects a task)
  // - selectedTask: derived from Zustand store, recomputes when tasks array changes
  // This ensures TaskDetailModal receives fresh task data on every store update
  // (status changes, subtask updates, execution progress, etc.)
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  // Derive selectedTask from store so it updates when store changes
  // Dependencies: [selectedTaskId, tasks] - tasks is a new array ref on every store update
  const selectedTask = useMemo(
    () => {
      if (!selectedTaskId) return null;
      const task = tasks.find(t => t.id === selectedTaskId || t.specId === selectedTaskId) ?? null;
      if (window.DEBUG && task) {
        console.log('[App] selectedTask derived:', task.id, 'status:', task.status, 'subtasks:', task.subtasks?.length);
      }
      return task;
    },
    [selectedTaskId, tasks]
  );
  const [activeView, setActiveView] = useState<SidebarView>('planning');
  const [isNewTaskDialogOpen, setIsNewTaskDialogOpen] = useState(false);
  const [isCloudCheckOpen, setIsCloudCheckOpen] = useState(false);
  const [isSettingsDialogOpen, setIsSettingsDialogOpen] = useState(false);
  const [isAddProjectModalOpen, setIsAddProjectModalOpen] = useState(false);
  const [isOnboardingOpen, setIsOnboardingOpen] = useState(false);

  const selectedProject = projects.find((p) => p.id === (activeProjectId || selectedProjectId));

  // Compute project name for loading modal
  const switchingProjectName = useMemo(() => {
    if (!isSwitchingProject || !activeProjectId) return undefined;
    return projects.find(p => p.id === activeProjectId)?.name;
  }, [isSwitchingProject, activeProjectId, projects]);

  // Initial load
  useEffect(() => {
    loadProjects();
    loadSettings();
  }, []);

  // Trigger onboarding only if CLI not installed or auth token missing
  useEffect(() => {
    if (settings.onboardingCompleted !== false) return;

    // Check actual setup status before showing wizard
    const checkSetup = async () => {
      try {
        const [versionResult, authResult] = await Promise.all([
          window.API?.checkClaudeCodeVersion?.() ?? { success: false },
          window.API?.getAuthStatus?.() ?? { success: false },
        ]);

        const cliInstalled = versionResult.success && versionResult.data?.installed;
        const hasToken = authResult.success && authResult.data?.hasToken;

        if (cliInstalled && hasToken) {
          // Everything is set up — skip wizard and mark as completed
          const { updateSettings } = useSettingsStore.getState();
          updateSettings({ onboardingCompleted: true });
          window.API?.saveSettings?.({ onboardingCompleted: true });
        } else {
          setIsOnboardingOpen(true);
        }
      } catch {
        // If checks fail, show wizard as fallback
        setIsOnboardingOpen(true);
      }
    };

    checkSetup();
  }, [settings.onboardingCompleted]);

  // Sync i18n language with settings
  const { i18n } = useTranslation();
  useEffect(() => {
    if (settings.language && settings.language !== i18n.language) {
      i18n.changeLanguage(settings.language);
    }
  }, [settings.language, i18n]);

  // Load tasks when project changes
  useEffect(() => {
    const currentProjectId = activeProjectId || selectedProjectId;
    if (currentProjectId) {
      loadTasks(currentProjectId);
      setSelectedTaskId(null);
    } else {
      useTaskStore.getState().clearTasks();
    }
  }, [activeProjectId, selectedProjectId]);

  // Safety timeout: auto-clear stuck switching state after 10 seconds
  useEffect(() => {
    if (!isSwitchingProject) return;
    const timeout = setTimeout(() => {
      useProjectStore.getState().clearSwitchingState();
    }, 10_000);
    return () => clearTimeout(timeout);
  }, [isSwitchingProject]);

  // Apply light/dark mode (the color palette is handled by a separate effect)
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

    // Persist to localStorage so the inline script in index.html can apply
    // the theme synchronously on next load, preventing a flash of wrong colors
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

  // Apply color theme (palette) via the data-theme attribute. Gruvbox is the
  // default; both gruvbox and shadcn have [data-theme] blocks in index.css.
  useEffect(() => {
    const root = document.documentElement;
    const colorTheme = settings.colorTheme ?? 'gruvbox';

    root.setAttribute('data-theme', colorTheme);

    // Persist so the inline pre-paint script in index.html can apply the
    // palette synchronously on next load, preventing a flash of wrong colors.
    try {
      localStorage.setItem('pfactory-color-theme', colorTheme);
    } catch {
      // localStorage may be unavailable
    }
  }, [settings.colorTheme]);

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

  const handleAddProject = () => {
    setIsAddProjectModalOpen(true);
  };

  const handleProjectAdded = (project: Project, needsInit: boolean) => {
    console.log('[Web] Project added:', project.name, 'needs init:', needsInit);
    // Optionally navigate to the project or show init dialog
  };

  // Handler for opening inbuilt terminal with specific working directory
  const handleOpenInbuiltTerminal = useCallback((id: string, cwd: string) => {
    // Create a new terminal with the specified id and working directory
    window.API.createTerminal({
      id,
      cwd,
      cols: 80,
      rows: 24,
    });
    // Switch to terminals view to show the new terminal
    setActiveView('terminals');
  }, []);

  // Show loading screen for 2 seconds on page load
  if (isLoading) {
    return <LoadingScreen duration={2000} onComplete={handleLoadingComplete} />;
  }

  return (
    <ViewStateProvider>
      <TooltipProvider>
        <div className="flex h-screen bg-background">
          {/* Sidebar */}
          <Sidebar
            onSettingsClick={() => setIsSettingsDialogOpen(true)}
            onNewTaskClick={() => setIsNewTaskDialogOpen(true)}
            onOpenOnboarding={() => setIsOnboardingOpen(true)}
            activeView={activeView}
            onViewChange={setActiveView}
          />

          {/* Main content */}
          <div className="flex flex-1 flex-col overflow-hidden">
            {/* Project Tab Bar — always rendered so the global toolbar
                (settings cog, theme toggle, add-project, status badges)
                stays accessible even when no project tab is open. The
                tab strip on the left simply renders empty when
                ``openProjects`` is empty. */}
            <ProjectTabBar
              projects={openProjects}
              activeProjectId={activeProjectId}
              onProjectSelect={(projectId) => {
                setActiveProject(projectId);
                // Also update selectedProjectId so components use the correct project context
                useProjectStore.getState().selectProject(projectId);
              }}
              onProjectClose={(projectId) => closeProjectTab(projectId)}
              onAddProject={handleAddProject}
              onProjectAdded={handleProjectAdded}
              onSettingsClick={() => setIsSettingsDialogOpen(true)}
              onOpenOnboarding={() => setIsOnboardingOpen(true)}
            />

            <main className="flex-1 overflow-hidden">
              {activeView === 'planning' ? (
                /* Planning Portal — project-aware. Board mode renders the
                   animated KanbanBoard wired to the task-store (issue #82). */
                <div className="h-full overflow-hidden">
                  <PlanningView
                    onTaskClick={handleTaskClick}
                    onNewTaskClick={() => setIsNewTaskDialogOpen(true)}
                    focusSessionId={selectedTaskId}
                  />
                </div>
              ) : activeView === 'pfactory' ? (
                /* PFactory's own surface: the test-generation review queue.
                   Global (reads ~/.pfactory workspaces), so it renders with or
                   without a selected project. */
                <div className="h-full overflow-auto p-4">
                  <PFactoryPortal />
                </div>
              ) : selectedProject ? (
                <>
                  {/* TerminalGrid stays mounted but hidden to preserve xterm instances and PTY connections */}
                  <div className={activeView === 'terminals' ? 'h-full' : 'hidden'}>
                    <TerminalGrid
                      projectPath={selectedProject?.path}
                      onNewTaskClick={() => setIsNewTaskDialogOpen(true)}
                      isActive={activeView === 'terminals'}
                    />
                  </div>
                  {activeView === 'editor' && (
                    <EditorPage projectPath={selectedProject?.path} />
                  )}
                  {activeView === 'worktrees' && (
                    <Worktrees projectId={selectedProject?.id || ''} />
                  )}
                  {activeView === 'context' && (
                    <Context projectId={selectedProject?.id || ''} />
                  )}
                  {activeView === 'github-issues' && (
                    <GitHubIssues
                      onOpenSettings={() => setIsSettingsDialogOpen(true)}
                      onNavigateToTask={(taskId) => {
                        setSelectedTaskId(taskId);
                        setActiveView('planning');
                      }}
                    />
                  )}
                  {activeView === 'github-prs' && (
                    <GitHubPRs
                      onOpenSettings={() => setIsSettingsDialogOpen(true)}
                      isActive={true}
                    />
                  )}
                  {activeView === 'changelog' && <Changelog />}
                  {activeView === 'insights' && (
                    <Insights projectId={selectedProject?.id || ''} onNavigate={setActiveView} />
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
                </>
              ) : (
                <WelcomeScreen
                  projects={projects}
                  onNewProject={handleAddProject}
                  onOpenProject={handleAddProject}
                  onSelectProject={(projectId) => {
                    openProjectTab(projectId);
                  }}
                />
              )}
            </main>
          </div>

          {/* Project Switch Loading Modal */}
          <ProjectSwitchLoadingModal
            open={isSwitchingProject}
            projectName={switchingProjectName}
          />

          {/* Toast notifications */}
          <Toaster />

          {/* Add Project Modal */}
          <AddProjectModal
            open={isAddProjectModalOpen}
            onOpenChange={setIsAddProjectModalOpen}
            onProjectAdded={handleProjectAdded}
          />

          {/* Settings Dialog */}
          <AppSettingsDialog
            open={isSettingsDialogOpen}
            onOpenChange={setIsSettingsDialogOpen}
          />

          {/* Task Creation Wizard */}
          {/*
            Fix: Use activeProjectId first, then fall back to selectedProjectId
            This ensures the correct project path is resolved in multi-tab scenarios.
            Without this, the Browse Files button wouldn't render because projectPath
            lookup would fail when the wizard is opened from a different tab than
            the one with selectedProjectId.
          */}
          {(activeProjectId || selectedProjectId) && (
            <TaskCreationWizard
              projectId={(activeProjectId || selectedProjectId)!}
              open={isNewTaskDialogOpen}
              onOpenChange={setIsNewTaskDialogOpen}
              onCloudInfra={() => setIsCloudCheckOpen(true)}
            />
          )}

          {/* Cloud Infrastructure check — launchable from +Task or Cloud Reports */}
          <CloudCheckDialog open={isCloudCheckOpen} onOpenChange={setIsCloudCheckOpen} />

          {/* Task Detail Modal */}
          <TaskDetailModal
            open={selectedTask !== null}
            task={selectedTask}
            onOpenChange={(open) => {
              if (!open) setSelectedTaskId(null);
            }}
            onSwitchToTerminals={() => setActiveView('terminals')}
            onOpenInbuiltTerminal={handleOpenInbuiltTerminal}
          />

          {/* Onboarding Wizard */}
          <OnboardingWizard
            open={isOnboardingOpen}
            onOpenChange={setIsOnboardingOpen}
            onOpenTaskCreator={() => setIsNewTaskDialogOpen(true)}
            onOpenSettings={() => setIsSettingsDialogOpen(true)}
          />
        </div>
      </TooltipProvider>
    </ViewStateProvider>
  );
}

export default function App() {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const checkAuth = useAuthStore((state) => state.checkAuth);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  return (
    <Routes>
      <Route
        path="/login"
        element={isAuthenticated ? <Navigate to="/" replace /> : <LoginPage />}
      />
      {/* Standalone Live Agent Console — shareable deep link.  Bypasses
          the portal's sidebar + tab bar so the URL can be copied,
          opened on a phone, sent to a teammate over a VPN, etc. */}
      <Route
        path="/console/:projectId/:specId"
        element={isAuthenticated ? <ConsolePage /> : <Navigate to="/login" replace />}
      />
      <Route
        path="/*"
        element={isAuthenticated ? <AuthenticatedApp /> : <Navigate to="/login" replace />}
      />
    </Routes>
  );
}
