import { useState, useEffect, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Plus,
  Trash2,
  Columns3,
  FlaskConical,
  Terminal,
  FolderOpen,
  BookOpen,
  AlertCircle,
  Download,
  RefreshCw,
  Github,
  GitPullRequest,
  Gitlab,
  FileText,
  Sparkles,
  GitBranch,
  Wrench,
  Lightbulb,
  Cloud,
  Camera,
  LogOut,
  Map
} from 'lucide-react';
import { Button } from './ui/button';
import { ScrollArea } from './ui/scroll-area';
import { Separator } from './ui/separator';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger
} from './ui/tooltip';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from './ui/dialog';
import { cn } from '../lib/utils';
import {
  useProjectStore,
  removeProject,
  initializeProject
} from '../stores/project-store';
import { useSettingsStore } from '../stores/settings-store';
import { useAuthStore } from '../stores/auth-store';
import { AddProjectModal } from './AddProjectModal';
import { GitSetupModal } from './GitSetupModal';
import { RateLimitIndicator } from './RateLimitIndicator';
import type { Project, AutoBuildVersionInfo, GitStatus, ProjectEnvConfig } from '../shared/types';

export type SidebarView = 'pfactory' | 'kanban' | 'terminals' | 'editor' | 'context' | 'github-issues' | 'github-prs' | 'changelog' | 'insights' | 'worktrees' | 'agent-tools' | 'skills' | 'cloud' | 'visual-reports' | 'planning';

interface SidebarProps {
  onSettingsClick: () => void;
  onNewTaskClick: () => void;
  onOpenOnboarding?: () => void;
  activeView?: SidebarView;
  onViewChange?: (view: SidebarView) => void;
}

interface NavItem {
  id: SidebarView;
  labelKey: string;
  icon: React.ElementType;
}

// Base nav items always shown.
// PFactory's own job (tests / review queue) leads; the inherited
// general-agent IDE surfaces (editor/terminals/worktrees) follow.
const baseNavItems: NavItem[] = [
  { id: 'pfactory', labelKey: 'navigation:items.tests', icon: FlaskConical },
  { id: 'kanban', labelKey: 'navigation:items.kanban', icon: Columns3 },
  { id: 'editor', labelKey: 'navigation:items.editor', icon: FolderOpen },
  { id: 'insights', labelKey: 'navigation:items.chat', icon: Sparkles },
  { id: 'terminals', labelKey: 'navigation:items.terminals', icon: Terminal },
  { id: 'agent-tools', labelKey: 'navigation:items.agentTools', icon: Wrench },
  { id: 'skills', labelKey: 'navigation:items.skills', icon: Lightbulb },
  { id: 'changelog', labelKey: 'navigation:items.changelog', icon: FileText },
  { id: 'worktrees', labelKey: 'navigation:items.worktrees', icon: GitBranch },
  { id: 'context', labelKey: 'navigation:items.context', icon: BookOpen },
  { id: 'cloud', labelKey: 'navigation:items.cloud', icon: Cloud },
  { id: 'visual-reports', labelKey: 'navigation:items.visualReports', icon: Camera },
  { id: 'planning', labelKey: 'navigation:items.planning', icon: Map }
];

// GitHub nav items shown when GitHub is enabled
const githubNavItems: NavItem[] = [
  { id: 'github-issues', labelKey: 'navigation:items.githubIssues', icon: Github },
  { id: 'github-prs', labelKey: 'navigation:items.githubPRs', icon: GitPullRequest }
];

export function Sidebar({
  onSettingsClick,
  onNewTaskClick,
  onOpenOnboarding,
  activeView = 'kanban',
  onViewChange
}: SidebarProps) {
  const { t } = useTranslation(['navigation', 'dialogs', 'common']);
  const projects = useProjectStore((state) => state.projects);
  const selectedProjectId = useProjectStore((state) => state.selectedProjectId);
  const settings = useSettingsStore((state) => state.settings);
  const logout = useAuthStore((state) => state.logout);

  const [showAddProjectModal, setShowAddProjectModal] = useState(false);
  const [showInitDialog, setShowInitDialog] = useState(false);
  const [showGitSetupModal, setShowGitSetupModal] = useState(false);
  const [gitStatus, setGitStatus] = useState<GitStatus | null>(null);
  const [pendingProject, setPendingProject] = useState<Project | null>(null);
  const [isInitializing, setIsInitializing] = useState(false);
  const [envConfig, setEnvConfig] = useState<ProjectEnvConfig | null>(null);
  const [showLogoutDialog, setShowLogoutDialog] = useState(false);

  // Persist skipped git setup in localStorage so it survives page refresh
  const [skippedGitSetup, setSkippedGitSetup] = useState<Set<string>>(() => {
    try {
      const saved = localStorage.getItem('skippedGitSetup');
      return saved ? new Set(JSON.parse(saved)) : new Set();
    } catch {
      return new Set();
    }
  });

  // Use ref to access skippedGitSetup in effect without re-running
  const skippedGitSetupRef = useRef(skippedGitSetup);
  skippedGitSetupRef.current = skippedGitSetup;

  // Persist skipped init in localStorage so it survives page refresh
  const [skippedInit, setSkippedInit] = useState<Set<string>>(() => {
    try {
      const saved = localStorage.getItem('skippedInit');
      return saved ? new Set(JSON.parse(saved)) : new Set();
    } catch {
      return new Set();
    }
  });

  // Use ref to access skippedInit in effect without re-running
  const skippedInitRef = useRef(skippedInit);
  skippedInitRef.current = skippedInit;

  const selectedProject = projects.find((p) => p.id === selectedProjectId);

  // Load env config when project changes to check GitHub enabled state
  useEffect(() => {
    const loadEnvConfig = async () => {
      if (selectedProject?.autoBuildPath) {
        try {
          const result = await window.API.getProjectEnv(selectedProject.id);
          if (result.success && result.data) {
            setEnvConfig(result.data);
          } else {
            setEnvConfig(null);
          }
        } catch {
          setEnvConfig(null);
        }
      } else {
        setEnvConfig(null);
      }
    };
    loadEnvConfig();
  }, [selectedProject?.id, selectedProject?.autoBuildPath]);

  // Compute visible nav items based on git provider state
  const visibleNavItems = useMemo(() => {
    const items = [...baseNavItems];

    // Determine the active provider
    const provider = envConfig?.gitProvider || selectedProject?.settings?.gitProvider || (envConfig?.githubEnabled ? 'github' : null);

    if (provider) {
      if (provider === 'gitlab') {
        items.push(
          { id: 'github-issues', labelKey: 'navigation:items.gitlabIssues', icon: Gitlab },
          { id: 'github-prs', labelKey: 'navigation:items.gitlabMRs', icon: GitPullRequest }
        );
      } else if (provider === 'azure_devops' || provider === 'ado') {
        items.push(
          { id: 'github-issues', labelKey: 'navigation:items.adoIssues', icon: AlertCircle },
          { id: 'github-prs', labelKey: 'navigation:items.adoPRs', icon: GitPullRequest }
        );
      } else {
        // Default / GitHub
        items.push(
          { id: 'github-issues', labelKey: 'navigation:items.githubIssues', icon: Github },
          { id: 'github-prs', labelKey: 'navigation:items.githubPRs', icon: GitPullRequest }
        );
      }
    }

    return items;
  }, [envConfig?.githubEnabled, envConfig?.gitProvider, selectedProject?.settings?.gitProvider]);

  // Check git status when project changes
  // Use selectedProjectId instead of selectedProject to avoid re-running on every render
  useEffect(() => {
    const checkGit = async () => {
      const project = projects.find((p) => p.id === selectedProjectId);
      if (project) {
        try {
          const result = await window.API.checkGitStatus(project.path);
          if (result.success && result.data) {
            setGitStatus(result.data);
            // Show git setup modal if project is not a git repo or has no commits
            // BUT only if user hasn't skipped it for this project
            // Use ref to avoid re-running effect when skippedGitSetup changes
            if ((!result.data.isGitRepo || !result.data.hasCommits) && !skippedGitSetupRef.current.has(project.id)) {
              setShowGitSetupModal(true);
            }
          }
        } catch (error) {
          console.error('Failed to check git status:', error);
        }
      } else {
        setGitStatus(null);
      }
    };
    checkGit();
    // Only re-run when selectedProjectId changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedProjectId]);

  // Check if selected project needs initialization
  useEffect(() => {
    const project = projects.find((p) => p.id === selectedProjectId);
    if (project && !project.autoBuildPath && !skippedInitRef.current.has(project.id)) {
      setPendingProject(project);
      setShowInitDialog(true);
    }
    // Only re-run when selectedProjectId changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedProjectId]);

  const handleAddProject = () => {
    setShowAddProjectModal(true);
  };

  const handleProjectAdded = (project: Project, needsInit: boolean) => {
    if (needsInit) {
      setPendingProject(project);
      setShowInitDialog(true);
    }
  };

  const handleInitialize = async () => {
    if (!pendingProject) return;

    const projectId = pendingProject.id;
    setIsInitializing(true);
    try {
      const result = await initializeProject(projectId);
      if (result?.success) {
        // Clear pendingProject FIRST before closing dialog
        // This prevents onOpenChange from triggering skip logic
        setPendingProject(null);
        setShowInitDialog(false);
      }
    } finally {
      setIsInitializing(false);
    }
  };

  const handleSkipInit = () => {
    if (pendingProject) {
      setSkippedInit(prev => {
        const newSet = new Set(prev).add(pendingProject.id);
        localStorage.setItem('skippedInit', JSON.stringify([...newSet]));
        return newSet;
      });
    }
    setShowInitDialog(false);
    setPendingProject(null);
  };

  const handleGitInitialized = async () => {
    // Refresh git status after initialization
    if (selectedProject) {
      try {
        const result = await window.API.checkGitStatus(selectedProject.path);
        if (result.success && result.data) {
          setGitStatus(result.data);
          // Also add to skipped list so modal doesn't show again even if there's a race condition
          setSkippedGitSetup(prev => {
            const newSet = new Set(prev).add(selectedProject.id);
            localStorage.setItem('skippedGitSetup', JSON.stringify([...newSet]));
            return newSet;
          });
        }
      } catch (error) {
        console.error('Failed to refresh git status:', error);
      }
    }
  };

  const _handleRemoveProject = async (projectId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    await removeProject(projectId);
  };


  const handleNavClick = (view: SidebarView) => {
    onViewChange?.(view);
  };

  const renderNavItem = (item: NavItem) => {
    const isActive = activeView === item.id;
    const Icon = item.icon;

    return (
      <button
        key={item.id}
        onClick={() => handleNavClick(item.id)}
        disabled={!selectedProjectId}
        className={cn(
          'flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-all duration-200',
          'hover:bg-primary/10 hover:text-primary',
          'disabled:pointer-events-none disabled:opacity-50',
          isActive && 'bg-primary/20 text-primary font-medium'
        )}
      >
        <Icon className="h-4 w-4 shrink-0" />
        <span className="flex-1 text-left">{t(item.labelKey)}</span>
      </button>
    );
  };

  return (
    <TooltipProvider>
      <div className="flex h-full w-64 flex-col bg-sidebar border-r border-border">
        {/* Header with drag area - extra top padding for macOS traffic lights */}
        <div className="electron-drag flex h-14 items-center gap-2.5 px-4 pt-6">
          <img src="/logo.svg" alt="PFactory" className="electron-no-drag h-7 w-7 rounded" />
          <span className="electron-no-drag text-lg font-bold tracking-tight text-foreground">
            <span className="text-primary">T</span>Factory
          </span>
        </div>

        <Separator className="mt-2" />

        {/* Navigation */}
        <ScrollArea className="flex-1">
          <div className="px-3 py-4">
            {/* Project Section */}
            <div>
              <h3 className="mb-1 px-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {t('sections.project')}{selectedProject ? ` — ${selectedProject.name}` : ''}
              </h3>
              {selectedProject && (
                <p className="mb-2 px-3 text-[10px] text-muted-foreground/60 truncate" title={selectedProject.path}>
                  {selectedProject.path}
                </p>
              )}
              <nav className="space-y-1">
                {visibleNavItems.map(renderNavItem)}
              </nav>
            </div>
          </div>
        </ScrollArea>

        <Separator />

        {/* Rate Limit Indicator - shows when Claude is rate limited */}
        <RateLimitIndicator />

        {/* Bottom section with New Task */}
        <div className="p-4 space-y-3">
          {/* New Task button */}
          <Button
            className="w-full bg-primary hover:bg-primary/90 text-primary-foreground"
            onClick={onNewTaskClick}
            disabled={!selectedProjectId || !selectedProject?.autoBuildPath}
          >
            <Plus className="mr-2 h-4 w-4" />
            {t('actions.newTask')}
          </Button>
          {selectedProject && !selectedProject.autoBuildPath && (
            <Button
              variant="outline"
              size="sm"
              className="w-full mt-2"
              onClick={() => {
                setPendingProject(selectedProject);
                setShowInitDialog(true);
              }}
            >
              <Download className="mr-2 h-3.5 w-3.5" />
              {t('messages.initializeToCreateTasks')}
            </Button>
          )}

          {/* Logout */}
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={() => setShowLogoutDialog(true)}
                className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-xs text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors"
              >
                <LogOut className="h-3.5 w-3.5" />
                {t('actions.logout')}
              </button>
            </TooltipTrigger>
            <TooltipContent side="right">
              {t('tooltips.logout')}
            </TooltipContent>
          </Tooltip>
        </div>
      </div>

      {/* Initialize AI Factory Dialog */}
      <Dialog open={showInitDialog} onOpenChange={(open) => {
        // Only allow closing if user manually closes (not during initialization)
        if (!open && !isInitializing) {
          handleSkipInit();
        }
      }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Download className="h-5 w-5" />
              {t('dialogs:initialize.title')}
            </DialogTitle>
            <DialogDescription>
              {t('dialogs:initialize.description')}
            </DialogDescription>
          </DialogHeader>
          <div className="py-4">
            <div className="rounded-lg bg-muted p-4 text-sm">
              <p className="font-medium mb-2">{t('dialogs:initialize.willDo')}</p>
              <ul className="list-disc list-inside space-y-1 text-muted-foreground">
                <li>{t('dialogs:initialize.createFolder')}</li>
                <li>{t('dialogs:initialize.copyFramework')}</li>
                <li>{t('dialogs:initialize.setupSpecs')}</li>
              </ul>
            </div>
            {!settings.autoBuildPath && (
              <div className="mt-4 rounded-lg border border-warning/50 bg-warning/10 p-4 text-sm">
                <div className="flex items-start gap-2">
                  <AlertCircle className="h-4 w-4 text-warning mt-0.5 shrink-0" />
                  <div>
                    <p className="font-medium text-warning">{t('dialogs:initialize.sourcePathNotConfigured')}</p>
                    <p className="text-muted-foreground mt-1">
                      {t('dialogs:initialize.sourcePathNotConfiguredDescription')}
                    </p>
                  </div>
                </div>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={handleSkipInit} disabled={isInitializing}>
              {t('common:buttons.skip')}
            </Button>
            <Button
              onClick={handleInitialize}
              disabled={isInitializing || !settings.autoBuildPath}
            >
              {isInitializing ? (
                <>
                  <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
                  {t('common:labels.initializing')}
                </>
              ) : (
                <>
                  <Download className="mr-2 h-4 w-4" />
                  {t('common:buttons.initialize')}
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Add Project Modal */}
      <AddProjectModal
        open={showAddProjectModal}
        onOpenChange={setShowAddProjectModal}
        onProjectAdded={handleProjectAdded}
      />

      {/* Git Setup Modal */}
      <GitSetupModal
        open={showGitSetupModal}
        onOpenChange={setShowGitSetupModal}
        project={selectedProject || null}
        gitStatus={gitStatus}
        onGitInitialized={handleGitInitialized}
        onSkip={() => {
          if (selectedProject) {
            setSkippedGitSetup(prev => {
              const newSet = new Set(prev).add(selectedProject.id);
              localStorage.setItem('skippedGitSetup', JSON.stringify([...newSet]));
              return newSet;
            });
          }
        }}
      />

      {/* Logout Confirmation Dialog */}
      <Dialog open={showLogoutDialog} onOpenChange={setShowLogoutDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <LogOut className="h-5 w-5" />
              {t('logoutDialog.title')}
            </DialogTitle>
            <DialogDescription>
              {t('logoutDialog.description')}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowLogoutDialog(false)}>
              {t('logoutDialog.cancel')}
            </Button>
            <Button variant="destructive" onClick={() => { setShowLogoutDialog(false); logout(); }}>
              <LogOut className="mr-2 h-4 w-4" />
              {t('logoutDialog.confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </TooltipProvider>
  );
}
