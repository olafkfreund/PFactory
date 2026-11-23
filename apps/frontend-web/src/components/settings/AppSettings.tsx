import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Save,
  Loader2,
  Bot,
  Cpu,
  Key,
  KeyRound,
  Bell,
  Settings2,
  GitBranch,
  Database,
  Sparkles,
  Globe,
  Bug,
  Plug,
  Palette,
  ShieldCheck,
  BookOpen,
  Cloud,
} from 'lucide-react';

import { Button } from '../ui/button';
import { cn } from '../../lib/utils';
import { useSettings } from './hooks/useSettings';
import { ThemeSettings } from './ThemeSettings';
import { LanguageSettings } from './LanguageSettings';
import { GeneralSettings } from './GeneralSettings';
import { IntegrationSettings } from './IntegrationSettings';
import { GitCredentialsSettings } from './sections/GitCredentialsSettings';
import { TestCredentialsSettings } from './sections/TestCredentialsSettings';
import { DocsTargetsSettings } from './sections/DocsTargetsSettings';
import { CloudAssessmentPage } from './sections/CloudAssessmentPage';
import { McpApiKeysSettings } from './sections/McpApiKeysSettings';
import { AdvancedSettings } from './AdvancedSettings';
import { DebugSettings } from './DebugSettings';
import { LLMProvidersPage } from './sections/LLMProvidersPage';
import { ProjectSettingsContent, type ProjectSettingsSection } from './ProjectSettingsContent';
import { useProjectStore } from '../../stores/project-store';
import type { UseProjectSettingsReturn } from '../project-settings/hooks/useProjectSettings';

// App-level settings sections
export type AppSection =
  | 'appearance'
  | 'language'
  | 'agent'
  | 'llmProvider'
  | 'integrations'
  | 'gitCredentials'
  | 'testCredentials'
  | 'docsTargets'
  | 'cloudAssessment'
  | 'apiKeys'
  | 'notifications'
  | 'debug';

interface NavItemConfig<T extends string> {
  id: T;
  icon: React.ElementType;
}

const appNavItemsConfig: NavItemConfig<AppSection>[] = [
  { id: 'appearance', icon: Palette },
  { id: 'agent', icon: Bot },
  { id: 'llmProvider', icon: Cpu },
  { id: 'integrations', icon: Key },
  { id: 'gitCredentials', icon: KeyRound },
  { id: 'testCredentials', icon: ShieldCheck },
  { id: 'docsTargets', icon: BookOpen },
  { id: 'cloudAssessment', icon: Cloud },
  { id: 'apiKeys', icon: KeyRound },
  { id: 'notifications', icon: Bell },
  { id: 'debug', icon: Bug },
  { id: 'language', icon: Globe },
];

const projectTabsConfig: { id: ProjectSettingsSection; label: string; icon: React.ElementType }[] = [
  { id: 'general', label: 'General', icon: Settings2 },
  { id: 'github', label: 'Git', icon: GitBranch },
  { id: 'memory', label: 'Memory', icon: Database },
  { id: 'mcp', label: 'MCP', icon: Plug },
];

export interface ProjectSettingsPageProps {
  projectId: string | null;
  initialSection?: ProjectSettingsSection;
}

/**
 * Routed Project Settings page (/p/<slug>/settings) with tabs:
 * General, Git, Memory, MCP.
 */
export function ProjectSettingsPage({ projectId, initialSection = 'general' }: ProjectSettingsPageProps) {
  const { t } = useTranslation(['settings', 'common']);
  const [activeTab, setActiveTab] = useState<ProjectSettingsSection>(initialSection);
  const projects = useProjectStore((state) => state.projects);
  const selectedProject = projects.find((p) => p.id === projectId);
  const [projectSettingsHook, setProjectSettingsHook] = useState<UseProjectSettingsReturn | null>(null);
  const [projectError, setProjectError] = useState<string | null>(null);

  const handleProjectHookReady = useCallback((hook: UseProjectSettingsReturn | null) => {
    setProjectSettingsHook(hook);
    if (hook) {
      setProjectError(hook.error || hook.envError || null);
    } else {
      setProjectError(null);
    }
  }, []);

  const handleSave = async () => {
    if (!selectedProject || !projectSettingsHook) return;
    await projectSettingsHook.handleSave(() => {});
    setProjectError(projectSettingsHook.error || projectSettingsHook.envError || null);
  };

  return (
    <div data-testid="project-settings-page" className="flex flex-col gap-6">
      <header className="f-page-head">
        <div className="f-page-head__main">
          <h1 className="f-page-head__title">{t('settings:tabs.project', 'Project Settings')}</h1>
          {selectedProject && (
            <p className="f-page-head__desc" title={selectedProject.path}>
              {selectedProject.name} — {selectedProject.path}
            </p>
          )}
        </div>
        {selectedProject && (
          <div className="f-page-head__actions">
            <Button onClick={() => { void handleSave(); }} disabled={Boolean(projectSettingsHook?.isSaving)}>
              {projectSettingsHook?.isSaving ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  {t('common:buttons.saving', 'Saving...')}
                </>
              ) : (
                <>
                  <Save className="mr-2 h-4 w-4" />
                  {t('settings:actions.save', 'Save Settings')}
                </>
              )}
            </Button>
          </div>
        )}
      </header>

      <div role="tablist" aria-label={t('settings:tabs.project', 'Project Settings')} className="flex flex-wrap gap-2 border-b border-border pb-2">
        {projectTabsConfig.map((tab) => {
          const Icon = tab.icon;
          const active = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setActiveTab(tab.id)}
              className={cn(
                'f-btn',
                active ? 'f-btn--primary' : 'f-btn--outline'
              )}
            >
              <Icon className="h-4 w-4" aria-hidden />
              <span>{tab.label}</span>
            </button>
          );
        })}
      </div>

      {projectError && (
        <div role="alert" className="f-state f-state--error">
          <div className="f-state__body">
            <span className="f-state__reason">{projectError}</span>
          </div>
        </div>
      )}

      <div className="max-w-4xl">
        <ProjectSettingsContent
          project={selectedProject}
          activeSection={activeTab}
          isOpen={true}
          onHookReady={handleProjectHookReady}
        />
      </div>
    </div>
  );
}

export interface PreferencesPageProps {
  initialSection?: AppSection;
  onRerunWizard?: () => void;
}

/**
 * Routed Application Preferences page (/preferences), reached from the user menu.
 */
export function PreferencesPage({ initialSection = 'agent', onRerunWizard }: PreferencesPageProps) {
  const { t } = useTranslation(['settings', 'common', 'navigation']);
  const { settings, setSettings, isSaving, error, saveSettings, commitTheme, revertTheme } = useSettings();
  const [appSection, setAppSection] = useState<AppSection>(initialSection);
  const [version, setVersion] = useState<string>('');

  useEffect(() => () => revertTheme(), [revertTheme]);

  useEffect(() => {
    void window.API?.getAppVersion?.().then((v: unknown) => {
      if (typeof v === 'string') setVersion(v);
    });
  }, []);

  const handleSave = async () => {
    const ok = await saveSettings();
    if (ok) {
      commitTheme();
    }
  };

  const renderAppSection = () => {
    switch (appSection) {
      case 'appearance':
        return <ThemeSettings settings={settings} onSettingsChange={setSettings} />;
      case 'language':
        return <LanguageSettings settings={settings} onSettingsChange={setSettings} />;
      case 'agent':
        return <GeneralSettings settings={settings} onSettingsChange={setSettings} section="agent" />;
      case 'llmProvider':
        return <LLMProvidersPage isOpen={true} />;
      case 'integrations':
        return <IntegrationSettings settings={settings} onSettingsChange={setSettings} />;
      case 'gitCredentials':
        return <GitCredentialsSettings />;
      case 'testCredentials':
        return <TestCredentialsSettings />;
      case 'docsTargets':
        return <DocsTargetsSettings />;
      case 'cloudAssessment':
        return <CloudAssessmentPage />;
      case 'apiKeys':
        return <McpApiKeysSettings />;
      case 'notifications':
        return <AdvancedSettings settings={settings} onSettingsChange={setSettings} section="notifications" version={version} />;
      case 'debug':
        return <DebugSettings />;
      default:
        return null;
    }
  };

  return (
    <div data-testid="preferences-page" className="flex flex-col gap-6">
      <header className="f-page-head">
        <div className="f-page-head__main">
          <h1 className="f-page-head__title">{t('navigation:shell.preferences', 'Preferences')}</h1>
          <p className="f-page-head__desc">{t('settings:tabs.app', 'App Settings')}</p>
        </div>
        <div className="f-page-head__actions">
          <Button onClick={() => { void handleSave(); }} disabled={isSaving}>
            {isSaving ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                {t('common:buttons.saving', 'Saving...')}
              </>
            ) : (
              <>
                <Save className="mr-2 h-4 w-4" />
                {t('settings:actions.save', 'Save Settings')}
              </>
            )}
          </Button>
        </div>
      </header>

      {error && (
        <div role="alert" className="f-state f-state--error">
          <div className="f-state__body">
            <span className="f-state__reason">{error}</span>
          </div>
        </div>
      )}

      <div className="flex flex-col md:flex-row gap-6">
        <nav aria-label={t('settings:tabs.app', 'App Settings')} className="w-full md:w-72 shrink-0 space-y-1">
          {appNavItemsConfig.map((item) => {
            const Icon = item.icon;
            const isActive = appSection === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setAppSection(item.id)}
                className={cn(
                  'w-full flex items-start gap-3 p-3 rounded-lg text-left transition-all',
                  isActive
                    ? 'bg-accent/70 text-accent-foreground'
                    : 'hover:bg-accent/50 text-muted-foreground hover:text-foreground'
                )}
              >
                <Icon className="h-5 w-5 mt-0.5 shrink-0" />
                <div className="min-w-0">
                  <div className="font-medium text-sm">{t(`settings:sections.${item.id}.title`)}</div>
                  <div className={cn('text-xs truncate', isActive ? 'text-white/80' : 'text-muted-foreground')}>
                    {t(`settings:sections.${item.id}.description`)}
                  </div>
                </div>
              </button>
            );
          })}
          {onRerunWizard && (
            <button
              type="button"
              onClick={onRerunWizard}
              className={cn(
                'w-full flex items-start gap-3 p-3 rounded-lg text-left transition-all mt-2',
                'border border-dashed border-muted-foreground/30',
                'hover:bg-accent/50 text-muted-foreground hover:text-foreground'
              )}
            >
              <Sparkles className="h-5 w-5 mt-0.5 shrink-0" />
              <div className="min-w-0">
                <div className="font-medium text-sm">{t('settings:actions.rerunWizard')}</div>
                <div className="text-xs text-muted-foreground truncate">{t('settings:actions.rerunWizardDescription')}</div>
              </div>
            </button>
          )}
        </nav>

        <div className="flex-1 min-w-0 max-w-4xl">
          {renderAppSection()}
        </div>
      </div>
    </div>
  );
}
