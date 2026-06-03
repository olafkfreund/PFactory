import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Github, Gitlab, RefreshCw, KeyRound, Loader2, CheckCircle2, AlertCircle, User, Lock, Globe, ChevronDown, GitBranch } from 'lucide-react';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import { Switch } from '../../ui/switch';
import { Separator } from '../../ui/separator';
import { Button } from '../../ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../ui/select';
import { GitHubOAuthFlow } from '../../project-settings/GitHubOAuthFlow';
import { PasswordInput } from '../../project-settings/PasswordInput';
import { updateProjectSettings } from '../../../stores/project-store';
import type { ProjectEnvConfig, GitHubSyncStatus, ProjectSettings } from '../../../shared/types';

// Debug logging
const DEBUG = import.meta.env.DEV || import.meta.env.VITE_DEBUG === 'true';
function debugLog(message: string, data?: unknown) {
  if (DEBUG) {
    if (data !== undefined) {
      console.warn(`[GitHubIntegration] ${message}`, data);
    } else {
      console.warn(`[GitHubIntegration] ${message}`);
    }
  }
}

interface GitHubRepo {
  fullName: string;
  description: string | null;
  isPrivate: boolean;
}

interface GitHubIntegrationProps {
  envConfig: ProjectEnvConfig | null;
  updateEnvConfig: (updates: Partial<ProjectEnvConfig>) => void;
  showGitHubToken: boolean;
  setShowGitHubToken: React.Dispatch<React.SetStateAction<boolean>>;
  gitHubConnectionStatus: GitHubSyncStatus | null;
  isCheckingGitHub: boolean;
  projectPath?: string; // Project path for fetching git branches
  projectId?: string;   // Project ID for persisting settings
  // Project settings for mainBranch (used by kanban tasks and terminal worktrees)
  settings?: ProjectSettings;
  setSettings?: React.Dispatch<React.SetStateAction<ProjectSettings>>;
}

/**
 * GitHub integration settings component.
 * Manages GitHub token (manual or OAuth), repository configuration, and connection status.
 */
export function GitHubIntegration({
  envConfig,
  updateEnvConfig,
  showGitHubToken: _showGitHubToken,
  setShowGitHubToken: _setShowGitHubToken,
  gitHubConnectionStatus,
  isCheckingGitHub,
  projectPath,
  projectId,
  settings,
  setSettings
}: GitHubIntegrationProps) {
  const { t } = useTranslation('settings');
  const gitProvider = settings?.gitProvider || 'github';

  const handleSettingsChange = (updates: Partial<ProjectSettings>) => {
    if (setSettings) {
      setSettings(prev => {
        const next = { ...prev, ...updates };
        debugLog('handleSettingsChange - updated settings:', next);
        return next;
      });
    }

    // Auto-persist git provider fields to projects.json + .env so they survive
    // dialog close without an explicit Save click. Backend PATCH /settings maps
    // all six fields to both .env (GIT_PROVIDER, GIT_TOKEN, …) and projects.json.
    const gitFields: (keyof ProjectSettings)[] = [
      'gitProvider', 'gitToken', 'gitBaseUrl', 'gitOrg', 'gitProject', 'gitRepo',
    ];
    const gitUpdates: Partial<ProjectSettings> = {};
    for (const f of gitFields) {
      if (f in updates) {
        (gitUpdates as Record<string, unknown>)[f] = updates[f];
      }
    }
    if (projectId && Object.keys(gitUpdates).length > 0) {
      void updateProjectSettings(projectId, gitUpdates).then(ok => {
        if (!ok) debugLog('updateProjectSettings failed for git fields:', gitUpdates);
      });
    }

    // Mirror to envConfig for backward compatibility
    const envUpdates: Partial<ProjectEnvConfig> = {};
    if ('gitToken' in updates) {
      envUpdates.githubToken = updates.gitToken;
    }
    if ('gitRepo' in updates) {
      envUpdates.githubRepo = updates.gitRepo;
    }
    if (Object.keys(envUpdates).length > 0) {
      updateEnvConfig(envUpdates);
    }
  };

  const [authMode, setAuthMode] = useState<'manual' | 'oauth' | 'oauth-success'>('manual');
  const [oauthUsername, setOauthUsername] = useState<string | null>(null);
  const [repos, setRepos] = useState<GitHubRepo[]>([]);
  const [isLoadingRepos, setIsLoadingRepos] = useState(false);
  const [reposError, setReposError] = useState<string | null>(null);
  const [isAutoDetecting, setIsAutoDetecting] = useState(false);

  // Branch selection state
  const [branches, setBranches] = useState<string[]>([]);
  const [isLoadingBranches, setIsLoadingBranches] = useState(false);
  const [branchesError, setBranchesError] = useState<string | null>(null);

  debugLog('Render - authMode:', authMode);
  debugLog('Render - projectPath:', projectPath);
  debugLog('Render - envConfig:', envConfig ? { githubEnabled: envConfig.githubEnabled, hasToken: !!envConfig.githubToken, defaultBranch: envConfig.defaultBranch } : null);

  // Auto-detect GitHub CLI auth when toggled on without a token
  useEffect(() => {
    if (!envConfig?.githubEnabled || envConfig?.githubTokenSet) return;

    let cancelled = false;
    const autoDetect = async () => {
      debugLog('Auto-detecting GitHub CLI auth...');
      setIsAutoDetecting(true);
      try {
        const result = await window.API.autoDetectGitHub(projectId);
        if (cancelled) return;
        debugLog('autoDetectGitHub result:', result);

        if (result.success && result.data?.authenticated && result.data.tokenPersisted) {
          // gh CLI is authenticated — token persisted server-side
          updateEnvConfig({ githubAuthMethod: 'oauth' });
          setOauthUsername(result.data.username || null);
          setAuthMode('oauth-success');

          // Always detect repo from project git remote
          if (projectPath) {
            const repoResult = await window.API.detectGitHubRepo(projectPath);
            if (!cancelled && repoResult.success && repoResult.data) {
              debugLog('Auto-detected repo:', repoResult.data);
              updateEnvConfig({ githubRepo: repoResult.data });
            }
          }
        }
        // If not authenticated, stay in 'manual' mode — user can choose OAuth or PAT
      } catch (err) {
        debugLog('Auto-detect failed:', err);
      } finally {
        if (!cancelled) setIsAutoDetecting(false);
      }
    };

    autoDetect();
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [envConfig?.githubEnabled]);

  // Fetch branches when GitHub is enabled and project path is available
  useEffect(() => {
    debugLog(`useEffect[branches] - githubEnabled: ${envConfig?.githubEnabled}, projectPath: ${projectPath}`);
    if (envConfig?.githubEnabled && projectPath) {
      debugLog('useEffect[branches] - Triggering fetchBranches');
      fetchBranches();
    } else {
      debugLog('useEffect[branches] - Skipping fetchBranches (conditions not met)');
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [envConfig?.githubEnabled, projectPath]);

  /**
   * Handler for branch selection changes.
   * Updates BOTH project.settings.mainBranch (for Electron app) and envConfig.defaultBranch (for CLI backward compatibility).
   */
  const handleBranchChange = (branch: string) => {
    debugLog('handleBranchChange: Updating branch to:', branch);

    // Update local state
    if (setSettings) {
      setSettings(prev => ({ ...prev, mainBranch: branch }));
      debugLog('handleBranchChange: Updated settings.mainBranch');
    }

    // Persist to backend immediately so it survives page reload
    if (projectId) {
      updateProjectSettings(projectId, { mainBranch: branch });
      debugLog('handleBranchChange: Persisted mainBranch to backend');
    }

    // Also update envConfig for CLI backward compatibility
    updateEnvConfig({ defaultBranch: branch });
    debugLog('handleBranchChange: Updated envConfig.defaultBranch');
  };

  const fetchBranches = async () => {
    if (!projectPath) {
      debugLog('fetchBranches: No projectPath, skipping');
      return;
    }

    debugLog('fetchBranches: Starting with projectPath:', projectPath);
    setIsLoadingBranches(true);
    setBranchesError(null);

    try {
      debugLog('fetchBranches: Calling getGitBranches...');
      const result = await window.API.getGitBranches(projectPath);
      debugLog('fetchBranches: getGitBranches result:', { success: result.success, dataType: typeof result.data, dataLength: Array.isArray(result.data) ? result.data.length : 'N/A', error: result.error });

      // result.data is the array directly (not { branches: [] })
      if (result.success && result.data) {
        setBranches(result.data);
        debugLog('fetchBranches: Loaded branches:', result.data.length);

        // Auto-detect default branch if not set in project settings
        // Priority: settings.mainBranch > envConfig.defaultBranch > auto-detect
        if (!settings?.mainBranch && !envConfig?.defaultBranch) {
          debugLog('fetchBranches: No branch set, auto-detecting...');
          const detectResult = await window.API.detectMainBranch(projectPath);
          debugLog('fetchBranches: detectMainBranch result:', detectResult);
          if (detectResult.success && detectResult.data) {
            debugLog('fetchBranches: Auto-detected default branch:', detectResult.data);
            handleBranchChange(detectResult.data);
          }
        }
      } else {
        debugLog('fetchBranches: Failed -', result.error || 'No data returned');
        setBranchesError(result.error || 'Failed to load branches');
      }
    } catch (err) {
      debugLog('fetchBranches: Exception:', err);
      setBranchesError(err instanceof Error ? err.message : 'Failed to load branches');
    } finally {
      setIsLoadingBranches(false);
    }
  };

  const fetchUserRepos = async () => {
    debugLog('Fetching user repositories...');
    setIsLoadingRepos(true);
    setReposError(null);

    try {
      const result = await window.API.listGitHubUserRepos();
      debugLog('listGitHubUserRepos result:', result);

      if (result.success && result.data?.repos) {
        setRepos(result.data.repos);
        debugLog('Loaded repos:', result.data.repos.length);
      } else {
        setReposError(result.error || 'Failed to load repositories');
      }
    } catch (err) {
      debugLog('Error fetching repos:', err);
      setReposError(err instanceof Error ? err.message : 'Failed to load repositories');
    } finally {
      setIsLoadingRepos(false);
    }
  };

  if (!envConfig) {
    debugLog('No envConfig, returning null');
    return null;
  }

  const handleOAuthSuccess = (username?: string) => {
    debugLog('handleOAuthSuccess called');
    debugLog('OAuth username:', username);

    // Token was persisted server-side, just update auth method
    updateEnvConfig({ githubAuthMethod: 'oauth' });

    // Show success state with username
    setOauthUsername(username || null);
    setAuthMode('oauth-success');
  };

  const handleSwitchToManual = () => {
    setAuthMode('manual');
    setOauthUsername(null);
  };

  const handleSwitchToOAuth = () => {
    setAuthMode('oauth');
  };

  const handleSelectRepo = (repoFullName: string) => {
    debugLog('Selected repo:', repoFullName);
    updateEnvConfig({ githubRepo: repoFullName });
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="space-y-0.5">
          <Label className="font-normal text-foreground">
            {t('gitProviders.enableGitIntegration') || 'Enable Git Integration'}
          </Label>
          <p className="text-xs text-muted-foreground">
            {t('gitProviders.enableGitIntegrationDescription') || 'Sync issues and tasks from GitHub, GitLab, or Azure DevOps (ADO) and create tasks automatically'}
          </p>
        </div>
        <Switch
          checked={envConfig.githubEnabled}
          onCheckedChange={(checked) => updateEnvConfig({ githubEnabled: checked })}
        />
      </div>

      {envConfig.githubEnabled && (
        <>
          {/* Provider Selection */}
          <div className="space-y-2">
            <Label className="text-sm font-medium text-foreground">
              {t('gitProviders.gitProvider')}
            </Label>
            <Select
              value={gitProvider}
              onValueChange={(val) => handleSettingsChange({ gitProvider: val })}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select Git Provider" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="github">{t('gitProviders.githubProvider')}</SelectItem>
                <SelectItem value="gitlab">{t('gitProviders.gitlabProvider')}</SelectItem>
                <SelectItem value="azure_devops">{t('gitProviders.adoProvider')}</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              {t('gitProviders.gitProviderDescription')}
            </p>
          </div>

          <Separator />

          {/* Conditional provider forms */}
          {gitProvider === 'github' ? (
            <>
              {/* Auto-detecting state */}
              {isAutoDetecting && (
                <div className="rounded-lg border border-border bg-muted/30 p-4">
                  <div className="flex items-center gap-3">
                    <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                    <div>
                      <p className="text-sm font-medium text-foreground">Detecting GitHub CLI...</p>
                      <p className="text-xs text-muted-foreground">Checking if gh is already authenticated</p>
                    </div>
                  </div>
                </div>
              )}

              {/* OAuth Success State */}
              {!isAutoDetecting && authMode === 'oauth-success' && (
                <div className="space-y-4">
                  <div className="rounded-lg border border-success/30 bg-success/10 p-4">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <CheckCircle2 className="h-5 w-5 text-success" />
                        <div>
                          <p className="text-sm font-medium text-success">Connected via GitHub CLI</p>
                          {oauthUsername && (
                            <p className="text-xs text-success/80 flex items-center gap-1 mt-0.5">
                              <User className="h-3 w-3" />
                              Authenticated as {oauthUsername}
                            </p>
                          )}
                        </div>
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={handleSwitchToManual}
                        className="text-xs"
                      >
                        Use Different Token
                      </Button>
                    </div>
                  </div>

                  {/* Detected Repository (read-only from git remote) */}
                  {envConfig.githubRepo && (
                    <div className="space-y-2">
                      <Label className="text-sm font-medium text-foreground">Repository</Label>
                      <div className="flex items-center gap-2 px-3 py-2 text-sm border border-input rounded-md bg-muted/50">
                        <Github className="h-4 w-4 text-muted-foreground shrink-0" />
                        <code className="text-sm">{envConfig.githubRepo}</code>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        Detected from project git remote
                      </p>
                    </div>
                  )}
                </div>
              )}

              {/* OAuth Flow */}
              {!isAutoDetecting && authMode === 'oauth' && (
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <Label className="text-sm font-medium text-foreground">GitHub Authentication</Label>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={handleSwitchToManual}
                    >
                      Use Manual Token
                    </Button>
                  </div>
                  <GitHubOAuthFlow
                    projectId={projectId}
                    onSuccess={handleOAuthSuccess}
                    onCancel={handleSwitchToManual}
                  />
                </div>
              )}

              {/* Manual Token Entry */}
              {!isAutoDetecting && authMode === 'manual' && (
                <>
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <Label className="text-sm font-medium text-foreground">Personal Access Token</Label>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={handleSwitchToOAuth}
                        className="gap-2"
                      >
                        <KeyRound className="h-3 w-3" />
                        Use OAuth Instead
                      </Button>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Create a token with <code className="px-1 bg-muted rounded">repo</code> scope from{' '}
                      <a
                        href="https://github.com/settings/tokens/new?scopes=repo&description=Auto-Build-UI"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-info hover:underline"
                      >
                        GitHub Settings
                      </a>
                    </p>
                    <PasswordInput
                      value={envConfig.githubToken || ''}
                      onChange={(value) => {
                        updateEnvConfig({ githubToken: value });
                        handleSettingsChange({ gitToken: value });
                      }}
                      placeholder="ghp_xxxxxxxx or github_pat_xxxxxxxx"
                    />
                  </div>

                  <RepositoryInput
                    value={envConfig.githubRepo || ''}
                    onChange={(value) => {
                      updateEnvConfig({ githubRepo: value });
                      handleSettingsChange({ gitRepo: value });
                    }}
                  />
                </>
              )}
            </>
          ) : gitProvider === 'gitlab' ? (
            <div className="space-y-4">
              {/* GitLab configuration */}
              <div className="space-y-2">
                <Label className="text-sm font-medium text-foreground">
                  {t('gitProviders.gitToken')}
                </Label>
                <PasswordInput
                  value={settings?.gitToken || ''}
                  onChange={(val) => handleSettingsChange({ gitToken: val })}
                  placeholder={t('gitProviders.gitTokenPlaceholder') || "Enter your GitLab PAT"}
                />
                <p className="text-xs text-muted-foreground">
                  {t('gitProviders.gitTokenDescription')}
                </p>
              </div>

              <div className="space-y-2">
                <Label className="text-sm font-medium text-foreground">
                  {t('gitProviders.gitBaseUrl')}
                </Label>
                <Input
                  value={settings?.gitBaseUrl || ''}
                  onChange={(e) => handleSettingsChange({ gitBaseUrl: e.target.value })}
                  placeholder={t('gitProviders.gitBaseUrlPlaceholder') || "https://gitlab.com"}
                />
                <p className="text-xs text-muted-foreground">
                  {t('gitProviders.gitBaseUrlDescription')}
                </p>
              </div>

              <div className="space-y-2">
                <Label className="text-sm font-medium text-foreground">
                  {t('gitProviders.gitRepo')}
                </Label>
                <Input
                  value={settings?.gitRepo || ''}
                  onChange={(e) => handleSettingsChange({ gitRepo: e.target.value })}
                  placeholder={t('gitProviders.gitRepoPlaceholder') || "group/subgroup/repo"}
                />
                <p className="text-xs text-muted-foreground">
                  {t('gitProviders.gitRepoDescription')}
                </p>
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              {/* Azure DevOps configuration */}
              <div className="space-y-2">
                <Label className="text-sm font-medium text-foreground">
                  {t('gitProviders.gitToken')}
                </Label>
                <PasswordInput
                  value={settings?.gitToken || ''}
                  onChange={(val) => handleSettingsChange({ gitToken: val })}
                  placeholder={t('gitProviders.gitTokenPlaceholder') || "Enter Azure DevOps PAT"}
                />
                <p className="text-xs text-muted-foreground">
                  {t('gitProviders.gitTokenDescription')}
                </p>
              </div>

              <div className="space-y-2">
                <Label className="text-sm font-medium text-foreground">
                  {t('gitProviders.gitBaseUrl')}
                </Label>
                <Input
                  value={settings?.gitBaseUrl || ''}
                  onChange={(e) => handleSettingsChange({ gitBaseUrl: e.target.value })}
                  placeholder={t('gitProviders.gitBaseUrlPlaceholder') || "https://dev.azure.com"}
                />
                <p className="text-xs text-muted-foreground">
                  {t('gitProviders.gitBaseUrlDescription')}
                </p>
              </div>

              <div className="space-y-2">
                <Label className="text-sm font-medium text-foreground">
                  {t('gitProviders.gitOrg')}
                </Label>
                <Input
                  value={settings?.gitOrg || ''}
                  onChange={(e) => handleSettingsChange({ gitOrg: e.target.value })}
                  placeholder={t('gitProviders.gitOrgPlaceholder') || "e.g. MyOrg"}
                />
                <p className="text-xs text-muted-foreground">
                  {t('gitProviders.gitOrgDescription')}
                </p>
              </div>

              <div className="space-y-2">
                <Label className="text-sm font-medium text-foreground">
                  {t('gitProviders.gitProject')}
                </Label>
                <Input
                  value={settings?.gitProject || ''}
                  onChange={(e) => handleSettingsChange({ gitProject: e.target.value })}
                  placeholder={t('gitProviders.gitProjectPlaceholder') || "e.g. MyProject"}
                />
                <p className="text-xs text-muted-foreground">
                  {t('gitProviders.gitProjectDescription')}
                </p>
              </div>

              <div className="space-y-2">
                <Label className="text-sm font-medium text-foreground">
                  {t('gitProviders.gitRepo')}
                </Label>
                <Input
                  value={settings?.gitRepo || ''}
                  onChange={(e) => handleSettingsChange({ gitRepo: e.target.value })}
                  placeholder="e.g. MyRepo"
                />
                <p className="text-xs text-muted-foreground">
                  Format: Repository Name in Azure DevOps
                </p>
              </div>
            </div>
          )}

          {/* Connection Status & Details */}
          {((gitProvider === 'github' && (envConfig.githubTokenSet || envConfig.githubToken) && envConfig.githubRepo) ||
            (gitProvider !== 'github' && settings?.gitToken && settings?.gitRepo)) && (
            <ConnectionStatus
              isChecking={isCheckingGitHub}
              connectionStatus={gitHubConnectionStatus}
            />
          )}

          {gitHubConnectionStatus?.connected && <IssuesAvailableInfo gitProvider={gitProvider} />}

          <Separator />

          {/* Default Branch Selector */}
          {projectPath && (
            <BranchSelector
              branches={branches}
              selectedBranch={settings?.mainBranch || envConfig.defaultBranch || ''}
              isLoading={isLoadingBranches}
              error={branchesError}
              onSelect={handleBranchChange}
              onRefresh={fetchBranches}
            />
          )}

          <Separator />

          <AutoSyncToggle
            enabled={envConfig.githubAutoSync || false}
            onToggle={(checked) => updateEnvConfig({ githubAutoSync: checked })}
          />
        </>
      )}
    </div>
  );
}

interface RepositoryDropdownProps {
  repos: GitHubRepo[];
  selectedRepo: string;
  isLoading: boolean;
  error: string | null;
  onSelect: (repoFullName: string) => void;
  onRefresh: () => void;
  onManualEntry: () => void;
}

function RepositoryDropdown({
  repos,
  selectedRepo,
  isLoading,
  error,
  onSelect,
  onRefresh,
  onManualEntry
}: RepositoryDropdownProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [filter, setFilter] = useState('');

  const filteredRepos = repos.filter(repo => {
    const name = repo.fullName || '';
    return name.toLowerCase().includes(filter.toLowerCase()) ||
      (repo.description?.toLowerCase().includes(filter.toLowerCase()));
  });

  const selectedRepoData = repos.find(r => r.fullName === selectedRepo);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label className="text-sm font-medium text-foreground">Repository</Label>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={onRefresh}
            disabled={isLoading}
            className="h-7 px-2"
          >
            <RefreshCw className={`h-3 w-3 ${isLoading ? 'animate-spin' : ''}`} />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={onManualEntry}
            className="h-7 text-xs"
          >
            Enter Manually
          </Button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 text-xs text-destructive">
          <AlertCircle className="h-3 w-3" />
          {error}
        </div>
      )}

      <div className="relative">
        <button
          type="button"
          onClick={() => setIsOpen(!isOpen)}
          disabled={isLoading}
          className="w-full flex items-center justify-between px-3 py-2 text-sm border border-input rounded-md bg-background hover:bg-accent hover:text-accent-foreground disabled:opacity-50"
        >
          {isLoading ? (
            <span className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading repositories...
            </span>
          ) : selectedRepo ? (
            <span className="flex items-center gap-2">
              {selectedRepoData?.isPrivate ? (
                <Lock className="h-3 w-3 text-muted-foreground" />
              ) : (
                <Globe className="h-3 w-3 text-muted-foreground" />
              )}
              {selectedRepo}
            </span>
          ) : (
            <span className="text-muted-foreground">Select a repository...</span>
          )}
          <ChevronDown className={`h-4 w-4 text-muted-foreground transition-transform ${isOpen ? 'rotate-180' : ''}`} />
        </button>

        {isOpen && !isLoading && (
          <div className="absolute z-50 w-full mt-1 bg-popover border border-border rounded-md shadow-lg max-h-64 overflow-hidden">
            {/* Search filter */}
            <div className="p-2 border-b border-border">
              <Input
                placeholder="Search repositories..."
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                className="h-8 text-sm"
                autoFocus
              />
            </div>

            {/* Repository list */}
            <div className="max-h-48 overflow-y-auto">
              {filteredRepos.length === 0 ? (
                <div className="px-3 py-4 text-sm text-muted-foreground text-center">
                  {filter ? 'No matching repositories' : 'No repositories found'}
                </div>
              ) : (
                filteredRepos.map((repo) => (
                  <button
                    key={repo.fullName}
                    type="button"
                    onClick={() => {
                      onSelect(repo.fullName);
                      setIsOpen(false);
                      setFilter('');
                    }}
                    className={`w-full px-3 py-2 text-left hover:bg-accent flex items-start gap-2 ${
                      repo.fullName === selectedRepo ? 'bg-accent' : ''
                    }`}
                  >
                    {repo.isPrivate ? (
                      <Lock className="h-4 w-4 text-muted-foreground mt-0.5 shrink-0" />
                    ) : (
                      <Globe className="h-4 w-4 text-muted-foreground mt-0.5 shrink-0" />
                    )}
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium truncate">{repo.fullName}</p>
                      {repo.description && (
                        <p className="text-xs text-muted-foreground truncate">{repo.description}</p>
                      )}
                    </div>
                  </button>
                ))
              )}
            </div>
          </div>
        )}
      </div>

      {selectedRepo && (
        <p className="text-xs text-muted-foreground">
          Selected: <code className="px-1 bg-muted rounded">{selectedRepo}</code>
        </p>
      )}
    </div>
  );
}

interface RepositoryInputProps {
  value: string;
  onChange: (value: string) => void;
}

function RepositoryInput({ value, onChange }: RepositoryInputProps) {
  return (
    <div className="space-y-2">
      <Label className="text-sm font-medium text-foreground">Repository</Label>
      <p className="text-xs text-muted-foreground">
        Format: <code className="px-1 bg-muted rounded">owner/repo</code> (e.g., facebook/react)
      </p>
      <Input
        placeholder="owner/repository"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

interface ConnectionStatusProps {
  isChecking: boolean;
  connectionStatus: GitHubSyncStatus | null;
}

function ConnectionStatus({ isChecking, connectionStatus }: ConnectionStatusProps) {
  return (
    <div className="rounded-lg border border-border bg-muted/30 p-3">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium text-foreground">Connection Status</p>
          <p className="text-xs text-muted-foreground">
            {isChecking ? 'Checking...' :
              connectionStatus?.connected
                ? `Connected to ${connectionStatus.repoFullName}`
                : connectionStatus?.error || 'Not connected'}
          </p>
          {connectionStatus?.connected && connectionStatus.repoDescription && (
            <p className="text-xs text-muted-foreground mt-1 italic">
              {connectionStatus.repoDescription}
            </p>
          )}
        </div>
        {isChecking ? (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        ) : connectionStatus?.connected ? (
          <CheckCircle2 className="h-4 w-4 text-success" />
        ) : (
          <AlertCircle className="h-4 w-4 text-warning" />
        )}
      </div>
    </div>
  );
}

function IssuesAvailableInfo({ gitProvider }: { gitProvider: string }) {
  const isGitLab = gitProvider === 'gitlab';
  const isADO = gitProvider === 'azure_devops';
  
  const Icon = isGitLab ? Gitlab : isADO ? GitBranch : Github;
  const providerName = isGitLab ? 'GitLab' : isADO ? 'Azure DevOps' : 'GitHub';
  const itemsName = isGitLab ? 'Issues & Merge Requests' : isADO ? 'Work Items & Pull Requests' : 'Issues';

  return (
    <div className="rounded-lg border border-info/30 bg-info/5 p-3">
      <div className="flex items-start gap-3">
        <Icon className="h-5 w-5 text-info mt-0.5" />
        <div className="flex-1">
          <p className="text-sm font-medium text-foreground">{providerName} Integration Connected</p>
          <p className="text-xs text-muted-foreground mt-1">
            Access {providerName} {itemsName} from the sidebar to view, investigate, and create tasks.
          </p>
        </div>
      </div>
    </div>
  );
}

interface AutoSyncToggleProps {
  enabled: boolean;
  onToggle: (checked: boolean) => void;
}

function AutoSyncToggle({ enabled, onToggle }: AutoSyncToggleProps) {
  return (
    <div className="flex items-center justify-between">
      <div className="space-y-0.5">
        <div className="flex items-center gap-2">
          <RefreshCw className="h-4 w-4 text-info" />
          <Label className="font-normal text-foreground">Auto-Sync on Load</Label>
        </div>
        <p className="text-xs text-muted-foreground pl-6">
          Automatically fetch issues when the project loads
        </p>
      </div>
      <Switch checked={enabled} onCheckedChange={onToggle} />
    </div>
  );
}

interface BranchSelectorProps {
  branches: string[];
  selectedBranch: string;
  isLoading: boolean;
  error: string | null;
  onSelect: (branch: string) => void;
  onRefresh: () => void;
}

function BranchSelector({
  branches,
  selectedBranch,
  isLoading,
  error,
  onSelect,
  onRefresh
}: BranchSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [filter, setFilter] = useState('');

  const filteredBranches = branches.filter(branch =>
    branch.toLowerCase().includes(filter.toLowerCase())
  );

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div className="space-y-0.5">
          <div className="flex items-center gap-2">
            <GitBranch className="h-4 w-4 text-info" />
            <Label className="text-sm font-medium text-foreground">Default Branch</Label>
          </div>
          <p className="text-xs text-muted-foreground pl-6">
            Base branch for creating task worktrees
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={onRefresh}
          disabled={isLoading}
          className="h-7 px-2"
        >
          <RefreshCw className={`h-3 w-3 ${isLoading ? 'animate-spin' : ''}`} />
        </Button>
      </div>

      {error && (
        <div className="flex items-center gap-2 text-xs text-destructive pl-6">
          <AlertCircle className="h-3 w-3" />
          {error}
        </div>
      )}

      <div className="relative pl-6">
        <button
          type="button"
          onClick={() => setIsOpen(!isOpen)}
          disabled={isLoading}
          className="w-full flex items-center justify-between px-3 py-2 text-sm border border-input rounded-md bg-background hover:bg-accent hover:text-accent-foreground disabled:opacity-50"
        >
          {isLoading ? (
            <span className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading branches...
            </span>
          ) : selectedBranch ? (
            <span className="flex items-center gap-2">
              <GitBranch className="h-3 w-3 text-muted-foreground" />
              {selectedBranch}
            </span>
          ) : (
            <span className="text-muted-foreground">Auto-detect (main/master)</span>
          )}
          <ChevronDown className={`h-4 w-4 text-muted-foreground transition-transform ${isOpen ? 'rotate-180' : ''}`} />
        </button>

        {isOpen && !isLoading && (
          <div className="absolute z-50 w-full mt-1 bg-popover border border-border rounded-md shadow-lg max-h-64 overflow-hidden">
            {/* Search filter */}
            <div className="p-2 border-b border-border">
              <Input
                placeholder="Search branches..."
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                className="h-8 text-sm"
                autoFocus
              />
            </div>

            {/* Auto-detect option */}
            <button
              type="button"
              onClick={() => {
                onSelect('');
                setIsOpen(false);
                setFilter('');
              }}
              className={`w-full px-3 py-2 text-left hover:bg-accent flex items-center gap-2 ${
                !selectedBranch ? 'bg-accent' : ''
              }`}
            >
              <span className="text-sm text-muted-foreground italic">Auto-detect (main/master)</span>
            </button>

            {/* Branch list */}
            <div className="max-h-40 overflow-y-auto border-t border-border">
              {filteredBranches.length === 0 ? (
                <div className="px-3 py-4 text-sm text-muted-foreground text-center">
                  {filter ? 'No matching branches' : 'No branches found'}
                </div>
              ) : (
                filteredBranches.map((branch) => (
                  <button
                    key={branch}
                    type="button"
                    onClick={() => {
                      onSelect(branch);
                      setIsOpen(false);
                      setFilter('');
                    }}
                    className={`w-full px-3 py-2 text-left hover:bg-accent flex items-center gap-2 ${
                      branch === selectedBranch ? 'bg-accent' : ''
                    }`}
                  >
                    <GitBranch className="h-3 w-3 text-muted-foreground" />
                    <span className="text-sm">{branch}</span>
                  </button>
                ))
              )}
            </div>
          </div>
        )}
      </div>

      {selectedBranch && (
        <p className="text-xs text-muted-foreground pl-6">
          All new tasks will branch from <code className="px-1 bg-muted rounded">{selectedBranch}</code>
        </p>
      )}
    </div>
  );
}
