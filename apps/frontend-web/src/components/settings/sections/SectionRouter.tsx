import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate } from 'react-router';
import { Package, LayoutTemplate, Cpu } from 'lucide-react';
import type { Project, ProjectSettings as ProjectSettingsType, AutoBuildVersionInfo, ProjectEnvConfig, GitHubSyncStatus } from '../../../shared/types';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../../ui/tabs';
import { RegistryPanel } from '../../planning/RegistryPanel';
import { TemplatesPanel } from '../../planning/TemplatesPanel';
import { ProvidersPanel } from '../../planning/ProvidersPanel';
import { SettingsSection } from '../SettingsSection';
import { GeneralSettings } from '../../project-settings/GeneralSettings';
import { SecuritySettings } from '../../project-settings/SecuritySettings';
import { MCPServersTab } from '../../project-settings/MCPServersTab';
import { GitHubIntegration } from '../integrations/GitHubIntegration';
import { InitializationGuard } from '../common/InitializationGuard';
import type { ProjectSettingsSection } from '../ProjectSettingsContent';

interface SectionRouterProps {
  activeSection: ProjectSettingsSection;
  project: Project;
  settings: ProjectSettingsType;
  setSettings: React.Dispatch<React.SetStateAction<ProjectSettingsType>>;
  versionInfo: AutoBuildVersionInfo | null;
  isCheckingVersion: boolean;
  isUpdating: boolean;
  envConfig: ProjectEnvConfig | null;
  isLoadingEnv: boolean;
  envError: string | null;
  updateEnvConfig: (updates: Partial<ProjectEnvConfig>) => void;
  showOpenAIKey: boolean;
  setShowOpenAIKey: React.Dispatch<React.SetStateAction<boolean>>;
  showGitHubToken: boolean;
  setShowGitHubToken: React.Dispatch<React.SetStateAction<boolean>>;
  gitHubConnectionStatus: GitHubSyncStatus | null;
  isCheckingGitHub: boolean;
  handleInitialize: () => Promise<void>;
}

/**
 * Routes to the appropriate settings section based on activeSection.
 * Handles initialization guards and section-specific configurations.
 */
export function SectionRouter({
  activeSection,
  project,
  settings,
  setSettings,
  versionInfo,
  isCheckingVersion,
  isUpdating,
  envConfig,
  isLoadingEnv,
  envError,
  updateEnvConfig,
  showOpenAIKey,
  setShowOpenAIKey,
  showGitHubToken,
  setShowGitHubToken,
  gitHubConnectionStatus,
  isCheckingGitHub,
  handleInitialize,
}: SectionRouterProps) {
  const { t } = useTranslation('settings');
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const planSourceTab = pathname.match(/\/settings\/plan-sources\/(registry|templates|providers)\/?$/)?.[1] ?? 'registry';

  switch (activeSection) {
    case 'general':
      return (
        <SettingsSection
          title="General"
          description={`Configure Auto-Build, agent model, and notifications for ${project.name}`}
        >
          <GeneralSettings
            project={project}
            settings={settings}
            setSettings={setSettings}
            versionInfo={versionInfo}
            isCheckingVersion={isCheckingVersion}
            isUpdating={isUpdating}
            handleInitialize={handleInitialize}
          />
        </SettingsSection>
      );

    case 'github':
      return (
        <SettingsSection
          title={t('projectSections.github.integrationTitle')}
          description={t('projectSections.github.integrationDescription')}
        >
          <InitializationGuard
            initialized={!!project.autoBuildPath}
            title={t('projectSections.github.integrationTitle')}
            description={t('projectSections.github.syncDescription')}
          >
            <GitHubIntegration
              envConfig={envConfig}
              updateEnvConfig={updateEnvConfig}
              showGitHubToken={showGitHubToken}
              setShowGitHubToken={setShowGitHubToken}
              gitHubConnectionStatus={gitHubConnectionStatus}
              isCheckingGitHub={isCheckingGitHub}
              projectPath={project.path}
              projectId={project.id}
              settings={settings}
              setSettings={setSettings}
            />
          </InitializationGuard>
        </SettingsSection>
      );

    case 'mcp':
      return (
        <SettingsSection
          title={t('projectSections.mcp.integrationTitle')}
          description={t('projectSections.mcp.integrationDescription')}
        >
          <MCPServersTab project={project} />
        </SettingsSection>
      );

    case 'memory':
      return (
        <SettingsSection
          title={t('projectSections.memory.integrationTitle')}
          description={t('projectSections.memory.integrationDescription')}
        >
          <InitializationGuard
            initialized={!!project.autoBuildPath}
            title={t('projectSections.memory.integrationTitle')}
            description={t('projectSections.memory.syncDescription')}
          >
            <SecuritySettings
              envConfig={envConfig}
              settings={settings}
              setSettings={setSettings}
              updateEnvConfig={updateEnvConfig}
              showOpenAIKey={showOpenAIKey}
              setShowOpenAIKey={setShowOpenAIKey}
              expanded={true}
              onToggle={() => {}}
            />
          </InitializationGuard>
        </SettingsSection>
      );

    case 'plan-sources':
      return (
        <SettingsSection
          title={t('projectSections.planSources.title', 'Plan sources')}
          description={t('projectSections.planSources.description', 'Registry entries, plan templates, and provider adapters')}
        >
          <Tabs value={planSourceTab} onValueChange={(tab) => void navigate(`${pathname.replace(/\/(registry|templates|providers)\/?$/, '')}/${tab}`)}>
            <TabsList>
              <TabsTrigger value="registry">
                <Package className="h-3.5 w-3.5 mr-1.5" aria-hidden />
                {t('projectSections.planSources.registry', 'Registry')}
              </TabsTrigger>
              <TabsTrigger value="templates">
                <LayoutTemplate className="h-3.5 w-3.5 mr-1.5" aria-hidden />
                {t('projectSections.planSources.templates', 'Templates')}
              </TabsTrigger>
              <TabsTrigger value="providers">
                <Cpu className="h-3.5 w-3.5 mr-1.5" aria-hidden />
                {t('projectSections.planSources.providers', 'Providers')}
              </TabsTrigger>
            </TabsList>
            <TabsContent value="registry" className="mt-4">
              <RegistryPanel />
            </TabsContent>
            <TabsContent value="templates" className="mt-4">
              <TemplatesPanel />
            </TabsContent>
            <TabsContent value="providers" className="mt-4">
              <ProvidersPanel />
            </TabsContent>
          </Tabs>
        </SettingsSection>
      );

    default:
      return null;
  }
}
