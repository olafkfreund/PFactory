/**
 * IPC (Inter-Process Communication) types for Electron API
 */

import type { IPCResult } from './common';
import type { SupportedIDE, SupportedTerminal } from './settings';
import type {
  Project,
  ProjectSettings,
  AutoBuildVersionInfo,
  InitializationResult,
  CreateProjectFolderResult,
  FileNode,
  FileReadResult,
  DirectoryListing,
  ProjectContextData,
  ProjectIndex,
  GraphitiMemoryStatus,
  ContextSearchResult,
  MemoryEpisode,
  ProjectEnvConfig,
  InfrastructureStatus,
  GraphitiValidationResult,
  GraphitiConnectionTestResult,
  GitStatus,
  CustomMcpServer,
  McpHealthCheckResult,
  McpTestConnectionResult,
  DetectedMcpService
} from './project';
import type {
  Task,
  TaskStatus,
  TaskStartOptions,
  ImplementationPlan,
  ExecutionProgress,
  WorktreeStatus,
  WorktreeDiff,
  WorktreeMergeResult,
  WorktreeDiscardResult,
  WorktreeListResult,
  ConflictResolveResult,
  TaskRecoveryResult,
  TaskRecoveryOptions,
  TaskMetadata,
  TaskLogs,
  TaskLogStreamChunk
} from './task';
import type {
  TerminalCreateOptions,
  TerminalSession,
  TerminalRestoreResult,
  SessionDateInfo,
  SessionDateRestoreResult,
  RateLimitInfo,
  SDKRateLimitInfo,
  RetryWithProfileRequest,
  CreateTerminalWorktreeRequest,
  TerminalWorktreeConfig,
  TerminalWorktreeResult,
} from './terminal';
import type {
  ClaudeProfileSettings,
  ClaudeProfile,
  ClaudeAutoSwitchSettings,
  ClaudeAuthResult,
  ClaudeUsageSnapshot,
  CLIAccountStatus,
  CLIAccountsDetectionResult
} from './agent';
import type { AppSettings, SourceEnvConfig, SourceEnvCheckResult, AutoBuildSourceUpdateCheck, AutoBuildSourceUpdateProgress } from './settings';
import type { AppUpdateInfo, AppUpdateProgress, AppUpdateAvailableEvent, AppUpdateDownloadedEvent } from './app-update';
import type {
  ChangelogTask,
  TaskSpecContent,
  ChangelogGenerationRequest,
  ChangelogGenerationResult,
  ChangelogSaveRequest,
  ChangelogSaveResult,
  ChangelogGenerationProgress,
  ExistingChangelog,
  GitBranchInfo,
  GitTagInfo,
  GitCommit,
  GitHistoryOptions,
  BranchDiffOptions,
  ReleaseableVersion,
  ReleasePreflightStatus,
  CreateReleaseRequest,
  CreateReleaseResult,
  ReleaseProgress
} from './changelog';
import type {
  InsightsSession,
  InsightsSessionSummary,
  InsightsChatStatus,
  InsightsStreamChunk,
  InsightsModelConfig,
  InsightsProviderInfo
} from './insights';
import type {
  GitHubRepository,
  GitHubIssue,
  GitHubSyncStatus,
  GitHubImportResult,
  GitHubInvestigationResult,
  GitHubInvestigationStatus
} from './integrations';
import type { APIProfile, ProfilesFile, TestConnectionResult, DiscoverModelsResult } from './profile';

// Electron API exposed via contextBridge
// Tab state interface (persisted in main process)
export interface TabState {
  openProjectIds: string[];
  activeProjectId: string | null;
  tabOrder: string[];
}

// Discovered project from folder scanning
export interface DiscoveredProject {
  name: string;
  path: string;
  has_git: boolean;
  has_package_json: boolean;
  has_requirements: boolean;
  has_pfactory: boolean;
  has_claude_md: boolean;
}

/**
 * Git credential row as returned by /api/git-credentials (epic #82 PR-C).
 * Token is never carried — it's encrypted at rest on the backend and
 * only the clone service reads the plaintext.
 */
export interface GitCredentialSummary {
  id: string;
  org_id: string;
  name: string;
  kind: string;
  host: string | null;
  username: string | null;
  created_at: string;
  last_used_at: string | null;
}

export interface CreateGitCredentialBody {
  org_id: string;
  name: string;
  token: string;
  kind?: string;
  host?: string;
  username?: string;
}

/**
 * API key row as returned by GET /api/keys. The raw key is NEVER
 * included — only the 8-char preview the backend computed at create
 * time. After creation the user has one chance to copy the full key;
 * losing it means revoking and minting a new one. (Issue #154.)
 */
export interface ApiKeySummary {
  id: string;
  name: string;
  org_id: string;
  scopes: string[] | null;
  key_preview: string;
  last_used_at: string | null;
  expires_at: string | null;
  created_at: string;
}

/**
 * Response from POST /api/keys. Same fields as ApiKeySummary plus
 * the one-time-visible raw_key. Show, let the user copy, then drop
 * from memory — the backend can never return it again.
 */
export interface CreateApiKeyResponse extends ApiKeySummary {
  raw_key: string;
}

export interface CreateApiKeyBody {
  org_id: string;
  name: string;
  /** Scope names to grant. The stdio MCP proxy understands
   *  `mcp:read`, `project:write`, `task:write`, `task:merge`. */
  scopes?: string[];
  /** Optional expiration in days from now (1-365). */
  expires_in_days?: number;
}

export interface API {
  // Project operations
  addProject: (projectPath: string) => Promise<IPCResult<Project>>;
  /**
   * Register a project by Git URL. The backend clones the repo into
   * ``PROJECT_WORKSPACE_ROOT`` (defaults to ``~/.pfactory/workspaces/``
   * on laptop installs; a PVC on K8s) and returns the registered
   * project pointing at the local clone.
   *
   * Added in epic #82 PR-A; surfaced to the UI in PR-B.
   */
  addProjectFromGitUrl: (
    gitUrl: string,
    branch?: string,
    name?: string,
  ) => Promise<IPCResult<Project>>;
  removeProject: (projectId: string) => Promise<IPCResult>;

  // Git credentials for portal-managed clones (epic #82 PR-C). Tokens
  // are encrypted at rest on the backend; create returns the row
  // metadata but NEVER the raw token after creation.
  listGitCredentials: (orgId: string) => Promise<IPCResult<GitCredentialSummary[]>>;
  createGitCredential: (
    body: CreateGitCredentialBody,
  ) => Promise<IPCResult<GitCredentialSummary>>;
  deleteGitCredential: (credentialId: string) => Promise<IPCResult>;

  // Scoped acw_ API keys for stdio MCP control plane (#154). The
  // create response carries the raw key — shown once, then dropped.
  listApiKeys: () => Promise<IPCResult<ApiKeySummary[]>>;
  createApiKey: (
    body: CreateApiKeyBody,
  ) => Promise<IPCResult<CreateApiKeyResponse>>;
  revokeApiKey: (keyId: string) => Promise<IPCResult>;
  getProjects: () => Promise<IPCResult<Project[]>>;
  updateProjectSettings: (projectId: string, settings: Partial<ProjectSettings>) => Promise<IPCResult>;
  initializeProject: (projectId: string) => Promise<IPCResult<InitializationResult>>;
  checkProjectVersion: (projectId: string) => Promise<IPCResult<AutoBuildVersionInfo>>;

  // Tab State (persisted in main process for reliability)
  getTabState: () => Promise<IPCResult<TabState>>;
  saveTabState: (tabState: TabState) => Promise<IPCResult>;

  // Task operations
  getTasks: (projectId: string) => Promise<IPCResult<Task[]>>;
  createTask: (projectId: string, title: string, description: string, metadata?: TaskMetadata) => Promise<IPCResult<Task>>;
  generateClarifications: (taskId: string) => Promise<IPCResult<{ questions: Array<{ id: string; question: string; options: string[] }>; skip: boolean; skipReason: string }>>;
  submitClarificationAnswers: (taskId: string, answers: Array<{ questionId: string; question: string; answer: string }>) => Promise<IPCResult<Task>>;
  deleteTask: (taskId: string) => Promise<IPCResult>;
  updateTask: (taskId: string, updates: { title?: string; description?: string; metadata?: Partial<TaskMetadata> }) => Promise<IPCResult<Task>>;
  startTask: (taskId: string, options?: TaskStartOptions) => void;
  stopTask: (taskId: string) => void;
  submitReview: (taskId: string, approved: boolean, feedback?: string) => Promise<IPCResult>;
  updateTaskStatus: (taskId: string, status: TaskStatus, options?: { force?: boolean }) => Promise<IPCResult>;
  recoverStuckTask: (taskId: string, options?: TaskRecoveryOptions) => Promise<IPCResult<TaskRecoveryResult>>;
  checkTaskRunning: (taskId: string) => Promise<IPCResult<boolean>>;

  // Workspace management (for human review)
  // Per-spec architecture: Each spec has its own worktree at .worktrees/{spec-name}/
  getWorktreeStatus: (taskId: string) => Promise<IPCResult<WorktreeStatus>>;
  getWorktreeDiff: (taskId: string) => Promise<IPCResult<WorktreeDiff>>;
  mergeWorktree: (taskId: string, options?: { noCommit?: boolean }) => Promise<IPCResult<WorktreeMergeResult>>;
  mergeWorktreePreview: (taskId: string) => Promise<IPCResult<WorktreeMergeResult>>;
  resolveWorktreeConflicts: (taskId: string, options: { useAI: boolean; strategy?: string }) => Promise<IPCResult<ConflictResolveResult>>;
  resolveUncommittedConflicts: (taskId: string) => Promise<IPCResult<{ resolved: string[]; failed?: Array<{ file: string; error: string }>; message: string }>>;
  resolveGitMergeConflicts: (taskId: string) => Promise<IPCResult<{ resolved: string[]; failed?: Array<{ file: string; error: string }>; message: string }>>;
  abortWorktreeMerge: (taskId: string) => Promise<IPCResult<{ abortedIn: string[]; message: string }>>;
  discardWorktree: (taskId: string) => Promise<IPCResult<WorktreeDiscardResult>>;
  createPRFromTask: (taskId: string, options?: {
    title?: string;
    body?: string;
    draft?: boolean;
    baseBranch?: string;
    targetRepo?: string;
  }) => Promise<IPCResult<{ prUrl: string; prNumber: number | null; branch: string; baseBranch: string }>>;
  getForkInfo: (projectPath: string) => Promise<IPCResult<{
    isFork: boolean;
    origin: string;
    defaultBranch: string;
    upstream?: string;
    upstreamDefaultBranch?: string;
  }>>;
  listWorktrees: (projectId: string) => Promise<IPCResult<WorktreeListResult>>;
  worktreeOpenInIDE: (worktreePath: string, ide: SupportedIDE, customPath?: string) => Promise<IPCResult<{ opened: boolean }>>;
  worktreeOpenInTerminal: (worktreePath: string, terminal: SupportedTerminal, customPath?: string) => Promise<IPCResult<{ opened: boolean }>>;
  worktreeDetectTools: () => Promise<IPCResult<{ ides: Array<{ id: string; name: string; path: string; installed: boolean }>; terminals: Array<{ id: string; name: string; path: string; installed: boolean }> }>>;

  // Task archive operations
  archiveTasks: (projectId: string, taskIds: string[], version?: string) => Promise<IPCResult<boolean>>;
  unarchiveTasks: (projectId: string, taskIds: string[]) => Promise<IPCResult<boolean>>;

  // Event listeners
  onTaskProgress: (callback: (taskId: string, plan: ImplementationPlan) => void) => () => void;
  onTaskError: (callback: (taskId: string, error: string) => void) => () => void;
  onTaskLog: (callback: (taskId: string, log: string) => void) => () => void;
  onTaskStatusChange: (callback: (taskId: string, status: TaskStatus, reviewReason?: string) => void) => () => void;
  onTaskExecutionProgress: (callback: (taskId: string, progress: ExecutionProgress) => void) => () => void;
  onTaskUpdate?: (callback: (data: { taskId: string; executionProgress?: ExecutionProgress; phase?: string; subtasksCompleted?: number; subtasksTotal?: number; subtasks?: { id: string; status: string }[] }) => void) => () => void;
  onTaskProfileSwitch?: (callback: (taskId: string, info: { oldProfileId?: string; newProfileId?: string; newProfileName?: string; reason?: string; timestamp?: string }) => void) => () => void;
  onTaskSubtaskUpdate?: (callback: (taskId: string, subtaskId: string, status: string, previousStatus?: string) => void) => () => void;

  // Terminal operations
  createTerminal: (options: TerminalCreateOptions) => Promise<IPCResult>;
  destroyTerminal: (id: string) => Promise<IPCResult>;
  sendTerminalInput: (id: string, data: string) => void;
  resizeTerminal: (id: string, cols: number, rows: number) => void;
  invokeClaudeInTerminal: (id: string, cwd?: string) => void;
  generateTerminalName: (command: string, cwd?: string) => Promise<IPCResult<string>>;

  // Terminal session management (persistence/restore)
  getTerminalSessions: (projectPath: string) => Promise<IPCResult<TerminalSession[]>>;
  restoreTerminalSession: (session: TerminalSession, cols?: number, rows?: number) => Promise<IPCResult<TerminalRestoreResult>>;
  clearTerminalSessions: (projectPath: string) => Promise<IPCResult>;
  resumeClaudeInTerminal: (id: string, sessionId?: string) => void;
  getTerminalSessionDates: (projectPath?: string) => Promise<IPCResult<SessionDateInfo[]>>;
  getTerminalSessionsForDate: (date: string, projectPath: string) => Promise<IPCResult<TerminalSession[]>>;
  restoreTerminalSessionsFromDate: (date: string, projectPath: string, cols?: number, rows?: number) => Promise<IPCResult<SessionDateRestoreResult>>;
  saveTerminalBuffer: (terminalId: string, serialized: string) => Promise<void>;
  checkTerminalPtyAlive: (terminalId: string) => Promise<IPCResult<{ alive: boolean }>>;

  // Terminal worktree operations (isolated development)
  createTerminalWorktree: (request: CreateTerminalWorktreeRequest) => Promise<TerminalWorktreeResult>;
  listTerminalWorktrees: (projectPath: string) => Promise<IPCResult<TerminalWorktreeConfig[]>>;
  removeTerminalWorktree: (projectPath: string, name: string, deleteBranch?: boolean) => Promise<IPCResult>;

  // Terminal event listeners
  onTerminalOutput: (callback: (id: string, data: string) => void) => () => void;
  onTerminalExit: (callback: (id: string, exitCode: number) => void) => () => void;
  onTerminalTitleChange: (callback: (id: string, title: string) => void) => () => void;
  onTerminalClaudeSession: (callback: (id: string, sessionId: string) => void) => () => void;
  onTerminalRateLimit: (callback: (info: RateLimitInfo) => void) => () => void;
  /** Listen for OAuth authentication completion (token is auto-saved to profile, never exposed to frontend) */
  onTerminalOAuthToken: (callback: (info: {
    terminalId: string;
    profileId?: string;
    email?: string;
    success: boolean;
    message?: string;
    detectedAt: string
  }) => void) => () => void;

  // Claude profile management (multi-account support)
  getClaudeProfiles: () => Promise<IPCResult<ClaudeProfileSettings>>;
  saveClaudeProfile: (profile: ClaudeProfile) => Promise<IPCResult<ClaudeProfile>>;
  deleteClaudeProfile: (profileId: string) => Promise<IPCResult>;
  renameClaudeProfile: (profileId: string, newName: string) => Promise<IPCResult>;
  setActiveClaudeProfile: (profileId: string) => Promise<IPCResult>;
  /** Switch terminal to use a different Claude profile (restarts Claude with new config) */
  switchClaudeProfile: (terminalId: string, profileId: string) => Promise<IPCResult>;
  /** Initialize authentication for a Claude profile */
  initializeClaudeProfile: (profileId: string) => Promise<IPCResult>;
  /** Start OAuth flow for a Claude profile (launches browser via backend) */
  startClaudeProfileOAuth: (profileId: string) => Promise<IPCResult<{ authUrl?: string }>>;
  /** Complete OAuth by forwarding auth code to CLI callback inside container */
  completeClaudeProfileOAuth: (profileId: string, code: string) => Promise<IPCResult>;
  /** Set OAuth token for a profile (used when capturing from terminal) */
  setClaudeProfileToken: (profileId: string, token: string, email?: string) => Promise<IPCResult>;
  /** Get auto-switch settings */
  getAutoSwitchSettings: () => Promise<IPCResult<ClaudeAutoSwitchSettings>>;
  /** Update auto-switch settings */
  updateAutoSwitchSettings: (settings: Partial<ClaudeAutoSwitchSettings>) => Promise<IPCResult>;
  /** Request usage fetch from a terminal (sends /usage command) */
  fetchClaudeUsage: (terminalId: string) => Promise<IPCResult>;
  /** Get the best available profile (for manual switching) */
  getBestAvailableProfile: (excludeProfileId?: string) => Promise<IPCResult<ClaudeProfile | null>>;
  /** Listen for SDK/CLI rate limit events (non-terminal) */
  onSDKRateLimit: (callback: (info: SDKRateLimitInfo) => void) => () => void;
  /** Retry a rate-limited operation with a different profile */
  retryWithProfile: (request: RetryWithProfileRequest) => Promise<IPCResult>;

  // CLI Account management (Codex & Gemini)
  detectCLIAccounts: () => Promise<IPCResult<CLIAccountsDetectionResult>>;
  getCLIAccountStatus: (cli: 'codex' | 'gemini') => Promise<IPCResult<CLIAccountStatus>>;
  importCLICredentials: (cli: 'codex' | 'gemini') => Promise<IPCResult>;
  setCLIApiKey: (cli: 'codex' | 'gemini', apiKey: string) => Promise<IPCResult>;
  startCLILogin: (cli: 'codex' | 'gemini') => Promise<IPCResult>;
  startCLILoginTerminal: (cli: 'codex' | 'gemini') => Promise<IPCResult<{ terminalId: string; command: string; message: string }>>;
  removeCLIAccount: (cli: 'codex' | 'gemini') => Promise<IPCResult>;
  installCLI: (cli: 'codex' | 'gemini') => Promise<IPCResult<{ version: string; wasUpdate: boolean; message: string }>>;
  onCLIAccountAuth: (callback: (info: { cli: string; success: boolean }) => void) => () => void;

  // Usage Monitoring (Proactive Account Switching)
  /** Request current usage snapshot */
  requestUsageUpdate: () => Promise<IPCResult<ClaudeUsageSnapshot | null>>;
  /** Listen for usage data updates */
  onUsageUpdated: (callback: (usage: ClaudeUsageSnapshot) => void) => () => void;
  /** Listen for proactive swap notifications */
  onProactiveSwapNotification: (callback: (notification: {
    fromProfile: { id: string; name: string };
    toProfile: { id: string; name: string };
    reason: string;
    usageSnapshot: ClaudeUsageSnapshot;
  }) => void) => () => void;

  // App settings
  getSettings: () => Promise<IPCResult<AppSettings>>;
  saveSettings: (settings: Partial<AppSettings>) => Promise<IPCResult>;
  // API Profile management (custom Anthropic-compatible endpoints)
  getAPIProfiles: () => Promise<IPCResult<ProfilesFile>>;
  saveAPIProfile: (profile: Omit<APIProfile, 'id' | 'createdAt' | 'updatedAt'>) => Promise<IPCResult<APIProfile>>;
  updateAPIProfile: (profile: APIProfile) => Promise<IPCResult<APIProfile>>;
  deleteAPIProfile: (profileId: string) => Promise<IPCResult>;
  setActiveAPIProfile: (profileId: string | null) => Promise<IPCResult>;
  // Note: AbortSignal is handled in preload via separate cancel IPC channels, not passed through IPC
  testConnection: (baseUrl: string, apiKey: string, signal?: AbortSignal) => Promise<IPCResult<TestConnectionResult>>;
  discoverModels: (baseUrl: string, apiKey: string, signal?: AbortSignal) => Promise<IPCResult<DiscoverModelsResult>>;

  // Dialog operations
  selectDirectory: () => Promise<string | null>;
  createProjectFolder: (location: string, name: string, initGit: boolean) => Promise<IPCResult<CreateProjectFolderResult>>;
  getDefaultProjectLocation: () => Promise<string | null>;

  // App info
  getAppVersion: () => Promise<string>;

  // Context operations
  getProjectContext: (projectId: string) => Promise<IPCResult<ProjectContextData>>;
  refreshProjectIndex: (projectId: string) => Promise<IPCResult<ProjectIndex>>;
  getMemoryStatus: (projectId: string) => Promise<IPCResult<GraphitiMemoryStatus>>;
  searchMemories: (projectId: string, query: string) => Promise<IPCResult<ContextSearchResult[]>>;
  getRecentMemories: (projectId: string, limit?: number) => Promise<IPCResult<MemoryEpisode[]>>;

  // Environment configuration operations
  getProjectEnv: (projectId: string) => Promise<IPCResult<ProjectEnvConfig>>;
  updateProjectEnv: (projectId: string, config: Partial<ProjectEnvConfig>) => Promise<IPCResult>;
  checkClaudeAuth: (projectId: string) => Promise<IPCResult<ClaudeAuthResult>>;
  invokeClaudeSetup: (projectId: string) => Promise<IPCResult<ClaudeAuthResult>>;

  // Memory Infrastructure operations (LadybugDB - no Docker required)
  getMemoryInfrastructureStatus: (dbPath?: string) => Promise<IPCResult<InfrastructureStatus>>;
  listMemoryDatabases: (dbPath?: string) => Promise<IPCResult<string[]>>;
  testMemoryConnection: (dbPath?: string, database?: string) => Promise<IPCResult<GraphitiValidationResult>>;

  // Graphiti validation operations
  validateLLMApiKey: (provider: string, apiKey: string) => Promise<IPCResult<GraphitiValidationResult>>;
  testGraphitiConnection: (config: {
    dbPath?: string;
    database?: string;
    llmProvider: string;
    apiKey: string;
  }) => Promise<IPCResult<GraphitiConnectionTestResult>>;

  // GitHub integration operations
  getGitHubRepositories: (projectId: string) => Promise<IPCResult<GitHubRepository[]>>;
  getGitHubIssues: (projectId: string, state?: 'open' | 'closed' | 'all') => Promise<IPCResult<GitHubIssue[]>>;
  getGitHubIssue: (projectId: string, issueNumber: number) => Promise<IPCResult<GitHubIssue>>;
  checkGitHubConnection: (projectId: string) => Promise<IPCResult<GitHubSyncStatus>>;
  investigateGitHubIssue: (projectId: string, issueNumber: number, selectedCommentIds?: number[]) => Promise<IPCResult> | void;
  getIssueComments: (projectId: string, issueNumber: number) => Promise<IPCResult<Array<{ id: number; body: string; user: { login: string; avatar_url?: string }; created_at: string; updated_at: string }>>>;
  importGitHubIssues: (projectId: string, issueNumbers: number[]) => Promise<IPCResult<GitHubImportResult>>;
  closeGitHubIssue: (projectId: string, issueNumber: number) => Promise<IPCResult>;
  createGitHubRelease: (
    projectId: string,
    version: string,
    releaseNotes: string,
    options?: { draft?: boolean; prerelease?: boolean }
  ) => Promise<IPCResult<{ url: string }>>;

  // GitHub OAuth operations (gh CLI)
  checkGitHubCli: () => Promise<IPCResult<{ installed: boolean; version?: string }>>;
  installGitHubCli: () => Promise<IPCResult<{ message: string; version: string; steps_completed: string[] }>>;
  checkGitHubAuth: () => Promise<IPCResult<{ authenticated: boolean; username?: string }>>;
  checkGitHubAuthStatus: () => Promise<IPCResult<{ complete: boolean; success?: boolean; error?: string }>>;
  autoDetectGitHub: (projectId?: string) => Promise<IPCResult<{ authenticated: boolean; username?: string; tokenPersisted?: boolean; reason?: string }>>;
  startGitHubAuth: () => Promise<IPCResult<{
    success: boolean;
    message?: string;
    deviceCode?: string;
    authUrl?: string;
    awaiting?: boolean;
  }>>;
  getGitHubToken: () => Promise<IPCResult<{ hasToken: boolean }>>;
  persistGitHubToken: (projectId: string) => Promise<IPCResult<{ tokenPersisted: boolean }>>;
  getGitHubUser: () => Promise<IPCResult<{ username: string; name?: string }>>;
  listGitHubUserRepos: () => Promise<IPCResult<{ repos: Array<{ fullName: string; description: string | null; isPrivate: boolean }> }>>;
  detectGitHubRepo: (projectPath: string) => Promise<IPCResult<string>>;
  getGitHubBranches: (repo: string) => Promise<IPCResult<string[]>>;
  createGitHubRepo: (
    repoName: string,
    options: { description?: string; isPrivate?: boolean; projectPath: string; owner?: string }
  ) => Promise<IPCResult<{ fullName: string; url: string }>>;
  addGitRemote: (
    projectPath: string,
    repoFullName: string
  ) => Promise<IPCResult<{ remoteUrl: string }>>;
  listGitHubOrgs: () => Promise<IPCResult<{ orgs: Array<{ login: string; avatarUrl?: string }> }>>;

  // GitHub OAuth device code event (streams device code during auth flow)
  onGitHubAuthDeviceCode: (
    callback: (data: { deviceCode: string; authUrl: string; browserOpened: boolean }) => void
  ) => () => void;

  // GitHub event listeners
  onGitHubInvestigationProgress: (
    callback: (projectId: string, status: GitHubInvestigationStatus) => void
  ) => () => void;
  onGitHubInvestigationComplete: (
    callback: (projectId: string, result: GitHubInvestigationResult) => void
  ) => () => void;
  onGitHubInvestigationError: (
    callback: (projectId: string, error: string) => void
  ) => () => void;

  // Release operations
  getReleaseableVersions: (projectId: string) => Promise<IPCResult<ReleaseableVersion[]>>;
  runReleasePreflightCheck: (projectId: string, version: string) => Promise<IPCResult<ReleasePreflightStatus>>;
  createRelease: (request: CreateReleaseRequest) => void;

  // Release event listeners
  onReleaseProgress: (
    callback: (projectId: string, progress: ReleaseProgress) => void
  ) => () => void;
  onReleaseComplete: (
    callback: (projectId: string, result: CreateReleaseResult) => void
  ) => () => void;
  onReleaseError: (
    callback: (projectId: string, error: string) => void
  ) => () => void;

  // AI Factory source update operations
  checkAutoBuildSourceUpdate: () => Promise<IPCResult<AutoBuildSourceUpdateCheck>>;
  downloadAutoBuildSourceUpdate: () => void;
  getAutoBuildSourceVersion: () => Promise<IPCResult<string>>;

  // AI Factory source update event listeners
  onAutoBuildSourceUpdateProgress: (
    callback: (progress: AutoBuildSourceUpdateProgress) => void
  ) => () => void;

  // Electron app update operations
  checkAppUpdate: () => Promise<IPCResult<AppUpdateInfo | null>>;
  downloadAppUpdate: () => Promise<IPCResult>;
  installAppUpdate: () => void;

  // Electron app update event listeners
  onAppUpdateAvailable: (
    callback: (info: AppUpdateAvailableEvent) => void
  ) => () => void;
  onAppUpdateDownloaded: (
    callback: (info: AppUpdateDownloadedEvent) => void
  ) => () => void;
  onAppUpdateProgress: (
    callback: (progress: AppUpdateProgress) => void
  ) => () => void;

  // Shell operations
  openExternal: (url: string) => Promise<void>;
  openTerminal: (dirPath: string) => Promise<IPCResult<void>>;

  // AI Factory source environment operations
  getSourceEnv: () => Promise<IPCResult<SourceEnvConfig>>;
  updateSourceEnv: (config: { claudeOAuthToken?: string }) => Promise<IPCResult>;
  checkSourceToken: () => Promise<IPCResult<SourceEnvCheckResult>>;

  // Changelog operations
  getChangelogDoneTasks: (projectId: string, tasks?: Task[]) => Promise<IPCResult<ChangelogTask[]>>;
  loadTaskSpecs: (projectId: string, taskIds: string[]) => Promise<IPCResult<TaskSpecContent[]>>;
  generateChangelog: (request: ChangelogGenerationRequest) => void; // Async with progress events
  saveChangelog: (request: ChangelogSaveRequest) => Promise<IPCResult<ChangelogSaveResult>>;
  readExistingChangelog: (projectId: string) => Promise<IPCResult<ExistingChangelog>>;
  suggestChangelogVersion: (
    projectId: string,
    taskIds: string[]
  ) => Promise<IPCResult<{ version: string; reason: string }>>;
  suggestChangelogVersionFromCommits: (
    projectId: string,
    commits: import('./changelog').GitCommit[]
  ) => Promise<IPCResult<{ version: string; reason: string }>>;

  // Changelog git operations (for git-based changelog generation)
  getChangelogBranches: (projectId: string) => Promise<IPCResult<GitBranchInfo[]>>;
  getChangelogTags: (projectId: string) => Promise<IPCResult<GitTagInfo[]>>;
  getChangelogCommitsPreview: (
    projectId: string,
    options: GitHistoryOptions | BranchDiffOptions,
    mode: 'git-history' | 'branch-diff'
  ) => Promise<IPCResult<GitCommit[]>>;
  saveChangelogImage: (
    projectId: string,
    imageData: string,
    filename: string
  ) => Promise<IPCResult<{ relativePath: string; url: string }>>;
  readLocalImage: (
    projectPath: string,
    relativePath: string
  ) => Promise<IPCResult<string>>;

  // Changelog event listeners
  onChangelogGenerationProgress: (
    callback: (projectId: string, progress: ChangelogGenerationProgress) => void
  ) => () => void;
  onChangelogGenerationComplete: (
    callback: (projectId: string, result: ChangelogGenerationResult) => void
  ) => () => void;
  onChangelogGenerationError: (
    callback: (projectId: string, error: string) => void
  ) => () => void;

  // Insights operations
  getInsightsSession: (projectId: string) => Promise<IPCResult<InsightsSession | null>>;
  detectInsightsProviders: (projectId: string) => Promise<IPCResult<InsightsProviderInfo[]>>;
  sendInsightsMessage: (projectId: string, message: string, modelConfig?: InsightsModelConfig) => void;
  stopInsightsMessage: (projectId: string) => Promise<IPCResult>;
  clearInsightsSession: (projectId: string) => Promise<IPCResult>;
  createTaskFromInsights: (
    projectId: string,
    title: string,
    description: string,
    metadata?: TaskMetadata
  ) => Promise<IPCResult<Task>>;
  generateTaskFromChat: (
    projectId: string,
    modelConfig?: InsightsModelConfig
  ) => Promise<IPCResult<{ title: string; description: string }>>;
  listInsightsSessions: (projectId: string) => Promise<IPCResult<InsightsSessionSummary[]>>;
  newInsightsSession: (projectId: string) => Promise<IPCResult<InsightsSession>>;
  switchInsightsSession: (projectId: string, sessionId: string) => Promise<IPCResult<InsightsSession | null>>;
  deleteInsightsSession: (projectId: string, sessionId: string) => Promise<IPCResult>;
  renameInsightsSession: (projectId: string, sessionId: string, newTitle: string) => Promise<IPCResult>;
  updateInsightsModelConfig: (projectId: string, sessionId: string, modelConfig: InsightsModelConfig) => Promise<IPCResult>;

  // Insights event listeners
  onInsightsStreamChunk: (
    callback: (projectId: string, chunk: InsightsStreamChunk) => void
  ) => () => void;
  onInsightsStatus: (
    callback: (projectId: string, status: InsightsChatStatus) => void
  ) => () => void;
  onInsightsError: (
    callback: (projectId: string, error: string) => void
  ) => () => void;

  // Task logs operations
  getTaskLogs: (projectId: string, specId: string) => Promise<IPCResult<TaskLogs | null>>;
  watchTaskLogs: (projectId: string, specId: string) => Promise<IPCResult>;
  unwatchTaskLogs: (projectId: string, specId: string) => Promise<IPCResult>;

  // Task logs event listeners
  onTaskLogsChanged: (
    callback: (specId: string, logs: TaskLogs) => void
  ) => () => void;
  onTaskLogsStream: (
    callback: (specId: string, chunk: TaskLogStreamChunk) => void
  ) => () => void;

  // File explorer operations
  listDirectory: (dirPath: string) => Promise<IPCResult<FileNode[] | DirectoryListing>>;
  readFile: (filePath: string) => Promise<IPCResult<FileReadResult>>;
  writeFile: (filePath: string, content: string) => Promise<IPCResult<{ success: boolean }>>;

  // Project discovery
  discoverProjects: (basePath: string, maxDepth?: number) => Promise<IPCResult<DiscoveredProject[]>>;

  // Git operations
  getGitBranches: (projectPath: string) => Promise<IPCResult<string[]>>;
  getCurrentGitBranch: (projectPath: string) => Promise<IPCResult<string | null>>;
  detectMainBranch: (projectPath: string) => Promise<IPCResult<string | null>>;
  checkGitStatus: (projectPath: string) => Promise<IPCResult<GitStatus>>;
  initializeGit: (projectPath: string) => Promise<IPCResult<InitializationResult>>;

  // Ollama model detection operations
  checkOllamaStatus: (baseUrl?: string) => Promise<IPCResult<{
    running: boolean;
    url: string;
    version?: string;
    message?: string;
  }>>;
  checkOllamaInstalled: () => Promise<IPCResult<{
    installed: boolean;
    path?: string;
    version?: string;
  }>>;
  installOllama: () => Promise<IPCResult<{ command: string }>>;
  listOllamaModels: (baseUrl?: string) => Promise<IPCResult<{
    models: Array<{
      name: string;
      size_bytes: number;
      size_gb: number;
      modified_at: string;
      is_embedding: boolean;
      embedding_dim?: number | null;
      description?: string;
    }>;
    count: number;
  }>>;
  listOllamaEmbeddingModels: (baseUrl?: string) => Promise<IPCResult<{
    embedding_models: Array<{
      name: string;
      embedding_dim: number | null;
      description: string;
      size_bytes: number;
      size_gb: number;
    }>;
    count: number;
  }>>;
  pullOllamaModel: (modelName: string, baseUrl?: string) => Promise<IPCResult<{
    model: string;
    status: 'completed' | 'failed';
    output: string[];
  }>>;

  // Ollama download progress listener
  onDownloadProgress: (
    callback: (data: {
      modelName: string;
      status: string;
      completed: number;
      total: number;
      percentage: number;
    }) => void
  ) => () => void;

  // GitHub API (nested for organized access)
  github: import('./github-api').GitHubAPI;

  // Claude Code CLI operations
  checkClaudeCodeVersion: () => Promise<IPCResult<import('./cli').ClaudeCodeVersionInfo>>;
  installClaudeCode: () => Promise<IPCResult<{ command: string }>>;

  // Auth status operations
  getAuthStatus: () => Promise<IPCResult<{ hasToken: boolean; profileCount: number; source: string | null; email: string | null }>>;
  checkClaudeCredentialsExist: () => Promise<IPCResult<{ exists: boolean }>>;
  importClaudeCredentials: () => Promise<IPCResult<{ success: boolean; profileId: string; profileName: string }>>;

  // Debug operations
  getDebugInfo: () => Promise<{
    systemInfo: Record<string, string>;
    recentErrors: string[];
    logsPath: string;
    debugReport: string;
  }>;
  openLogsFolder: () => Promise<{ success: boolean; error?: string }>;
  copyDebugInfo: () => Promise<{ success: boolean; error?: string }>;
  getRecentErrors: (maxCount?: number) => Promise<string[]>;
  listLogFiles: () => Promise<Array<{
    name: string;
    path: string;
    size: number;
    modified: string;
  }>>;

  // MCP Server health check operations
  checkMcpHealth: (server: CustomMcpServer) => Promise<IPCResult<McpHealthCheckResult>>;
  testMcpConnection: (server: CustomMcpServer) => Promise<IPCResult<McpTestConnectionResult>>;
  detectMcpServices: () => Promise<IPCResult<DetectedMcpService[]>>;
}

declare global {
  interface Window {
    API: API;
    DEBUG: boolean;
  }
}
