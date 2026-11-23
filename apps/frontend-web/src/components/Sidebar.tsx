import { useState, useEffect, useMemo, useRef, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  FolderOpen,
  BookOpen,
  AlertCircle,
  Download,
  RefreshCw,
  Github,
  GitPullRequest,
  Gitlab,
  Wrench,
  Lightbulb,
  LogOut,
  Map,
} from 'lucide-react';
import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { useProjectStore, initializeProject } from '../stores/project-store';
import { useSettingsStore } from '../stores/settings-store';
import { useAuthStore } from '../stores/auth-store';
import { GitSetupModal } from './GitSetupModal';
import type { ProjectView } from '../lib/project-route';
import type { Project, GitStatus, ProjectEnvConfig } from '../shared/types';

export type SidebarView = ProjectView;

interface NavItemConfig {
  id: SidebarView;
  labelKey: string;
  icon: React.ElementType;
}

const baseNavItems: NavItemConfig[] = [
  { id: 'pfactory', labelKey: 'navigation:items.plans', icon: Map },
  { id: 'planning', labelKey: 'navigation:items.planning', icon: Map },
  { id: 'editor', labelKey: 'navigation:items.editor', icon: FolderOpen },
  { id: 'agent-tools', labelKey: 'navigation:items.agentTools', icon: Wrench },
  { id: 'skills', labelKey: 'navigation:items.skills', icon: Lightbulb },
  { id: 'context', labelKey: 'navigation:items.context', icon: BookOpen },
];

export interface ShellNavAdapterItem {
  id: string;
  label: string;
  icon: ReactNode;
  disabled?: boolean | undefined;
}

export function usePFactoryShellAdapter(projectId: string | null) {
  const { t } = useTranslation(['navigation', 'dialogs', 'common']);
  const projects = useProjectStore((state) => state.projects);
  const settings = useSettingsStore((state) => state.settings);
  const logout = useAuthStore((state) => state.logout);

  const [showInitDialog, setShowInitDialog] = useState(false);
  const [showGitSetupModal, setShowGitSetupModal] = useState(false);
  const [gitStatus, setGitStatus] = useState<GitStatus | null>(null);
  const [pendingProject, setPendingProject] = useState<Project | null>(null);
  const [isInitializing, setIsInitializing] = useState(false);
  const [envConfig, setEnvConfig] = useState<ProjectEnvConfig | null>(null);
  const [showLogoutDialog, setShowLogoutDialog] = useState(false);

  const [skippedGitSetup, setSkippedGitSetup] = useState<Set<string>>(() => {
    try {
      const saved = localStorage.getItem('skippedGitSetup');
      return saved ? new Set(JSON.parse(saved) as string[]) : new Set();
    } catch {
      return new Set();
    }
  });
  const skippedGitSetupRef = useRef(skippedGitSetup);
  skippedGitSetupRef.current = skippedGitSetup;

  const [skippedInit, setSkippedInit] = useState<Set<string>>(() => {
    try {
      const saved = localStorage.getItem('skippedInit');
      return saved ? new Set(JSON.parse(saved) as string[]) : new Set();
    } catch {
      return new Set();
    }
  });
  const skippedInitRef = useRef(skippedInit);
  skippedInitRef.current = skippedInit;

  const selectedProject = projects.find((p) => p.id === projectId);

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
    void loadEnvConfig();
  }, [selectedProject?.id, selectedProject?.autoBuildPath]);

  const visibleNavItems = useMemo(() => {
    const items = [...baseNavItems];
    const provider =
      envConfig?.gitProvider ||
      selectedProject?.settings?.gitProvider ||
      (envConfig?.githubEnabled ? 'github' : null);

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
        items.push(
          { id: 'github-issues', labelKey: 'navigation:items.githubIssues', icon: Github },
          { id: 'github-prs', labelKey: 'navigation:items.githubPRs', icon: GitPullRequest }
        );
      }
    }

    return items;
  }, [envConfig?.githubEnabled, envConfig?.gitProvider, selectedProject?.settings?.gitProvider]);

  const nav: ShellNavAdapterItem[] = useMemo(
    () =>
      visibleNavItems.map((item) => {
        const Icon = item.icon;
        return {
          id: item.id,
          label: t(item.labelKey),
          icon: <Icon className="h-4 w-4" />,
          disabled: !projectId && item.id !== 'pfactory',
        };
      }),
    [visibleNavItems, projectId, t]
  );

  useEffect(() => {
    const checkGit = async () => {
      const project = projects.find((p) => p.id === projectId);
      if (project) {
        try {
          const result = await window.API.checkGitStatus(project.path);
          if (result.success && result.data) {
            setGitStatus(result.data);
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
    void checkGit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    const project = projects.find((p) => p.id === projectId);
    if (project && !project.autoBuildPath && !skippedInitRef.current.has(project.id)) {
      setPendingProject(project);
      setShowInitDialog(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const handleInitialize = async () => {
    if (!pendingProject) return;
    const targetId = pendingProject.id;
    setIsInitializing(true);
    try {
      const result = await initializeProject(targetId);
      if (result?.success) {
        setPendingProject(null);
        setShowInitDialog(false);
      }
    } finally {
      setIsInitializing(false);
    }
  };

  const handleSkipInit = () => {
    if (pendingProject) {
      setSkippedInit((prev) => {
        const newSet = new Set(prev).add(pendingProject.id);
        localStorage.setItem('skippedInit', JSON.stringify([...newSet]));
        return newSet;
      });
    }
    setShowInitDialog(false);
    setPendingProject(null);
  };

  const handleGitInitialized = async () => {
    if (selectedProject) {
      try {
        const result = await window.API.checkGitStatus(selectedProject.path);
        if (result.success && result.data) {
          setGitStatus(result.data);
          setSkippedGitSetup((prev) => {
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

  const sidebarHeader: ReactNode =
    selectedProject && !selectedProject.autoBuildPath ? (
      <Button
        variant="outline"
        size="sm"
        className="w-full"
        onClick={() => {
          setPendingProject(selectedProject);
          setShowInitDialog(true);
        }}
      >
        <Download className="mr-2 h-3.5 w-3.5" />
        {t('navigation:messages.initializeToCreateTasks')}
      </Button>
    ) : undefined;

  const dialogs: ReactNode = (
    <>
      <Dialog
        open={showInitDialog}
        onOpenChange={(open) => {
          if (!open && !isInitializing) {
            handleSkipInit();
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Download className="h-5 w-5" />
              {t('dialogs:initialize.title')}
            </DialogTitle>
            <DialogDescription>{t('dialogs:initialize.description')}</DialogDescription>
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
            <Button onClick={() => { void handleInitialize(); }} disabled={isInitializing || !settings.autoBuildPath}>
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

      <GitSetupModal
        open={showGitSetupModal}
        onOpenChange={setShowGitSetupModal}
        project={selectedProject || null}
        gitStatus={gitStatus}
        onGitInitialized={handleGitInitialized}
        onSkip={() => {
          if (selectedProject) {
            setSkippedGitSetup((prev) => {
              const newSet = new Set(prev).add(selectedProject.id);
              localStorage.setItem('skippedGitSetup', JSON.stringify([...newSet]));
              return newSet;
            });
          }
        }}
      />

      <Dialog open={showLogoutDialog} onOpenChange={setShowLogoutDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <LogOut className="h-5 w-5" />
              {t('navigation:logoutDialog.title')}
            </DialogTitle>
            <DialogDescription>{t('navigation:logoutDialog.description')}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowLogoutDialog(false)}>
              {t('navigation:logoutDialog.cancel')}
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                setShowLogoutDialog(false);
                logout();
              }}
            >
              <LogOut className="mr-2 h-4 w-4" />
              {t('navigation:logoutDialog.confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );

  return {
    nav,
    sidebarHeader,
    openLogoutDialog: () => setShowLogoutDialog(true),
    dialogs,
  };
}
