/**
 * Web API Adapter - Replaces window.API with HTTP/WebSocket calls
 *
 * This module provides the same interface as API but uses REST endpoints
 * and WebSocket connections instead of Electron IPC.
 */

import { get, post, put, del, patch } from './api-client';
import { wsManager, terminalWs, taskLogsWs, taskProgressWs } from './websocket';
import { createLogger } from './logger';
import type { API, TabState } from '../shared/types';

const log = createLogger('api-adapter');
import type {
  Project,
  ProjectSettings,
  Task,
  TaskStatus,
  TaskStartOptions,
  TaskRecoveryOptions,
  TaskMetadata,
  TaskLogs,
  IPCResult,
  TerminalCreateOptions,
  TerminalSession,
  TerminalRestoreResult,
  AppSettings,
  InsightsModelConfig,
  ChangelogGenerationRequest,
  ChangelogSaveRequest,
  GitHistoryOptions,
  BranchDiffOptions,
  CreateReleaseRequest,
  ClaudeProfile,
  ClaudeAutoSwitchSettings,
  RetryWithProfileRequest,
  CreateTerminalWorktreeRequest,
  CustomMcpServer,
  ProjectEnvConfig,
} from '../shared/types';

// Event callback storage
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Callback = (...args: any[]) => void;
const eventCallbacks = new Map<string, Set<Callback>>();

// Terminal-specific callback storage (per-terminal WebSocket)
const terminalOutputCallbacks = new Set<(id: string, data: string) => void>();
const terminalExitCallbacks = new Set<(id: string, exitCode: number) => void>();
const terminalTitleCallbacks = new Set<(id: string, title: string) => void>();
const terminalClaudeSessionCallbacks = new Set<(id: string, sessionId: string) => void>();
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const terminalRateLimitCallbacks = new Set<(info: any) => void>();
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const terminalOAuthTokenCallbacks = new Set<(info: any) => void>();

// Active terminal WebSocket subscriptions
const activeTerminalSubscriptions = new Map<string, () => void>();

// Track pending task start requests to prevent duplicates
const pendingTaskStarts = new Set<string>();

function registerCallback<T extends Callback>(event: string, callback: T): () => void {
  if (!eventCallbacks.has(event)) {
    eventCallbacks.set(event, new Set());
  }
  eventCallbacks.get(event)!.add(callback);
  return () => eventCallbacks.get(event)?.delete(callback);
}

/**
 * Subscribe to a terminal's WebSocket and route messages to registered callbacks.
 * Called when a terminal is created to set up the connection.
 */
function setupTerminalWebSocket(terminalId: string): void {
  // Already subscribed
  if (activeTerminalSubscriptions.has(terminalId)) {
    return;
  }

  const unsubscribe = terminalWs.subscribe(terminalId, (data: unknown) => {
    // Handle different message types
    if (typeof data === 'string') {
      // Plain text output
      terminalOutputCallbacks.forEach((cb) => cb(terminalId, data));
    } else if (typeof data === 'object' && data !== null) {
      const msg = data as { type?: string; [key: string]: unknown };

      if (msg.type === 'exit') {
        const exitCode = typeof msg.code === 'number' ? msg.code : 0;
        terminalExitCallbacks.forEach((cb) => cb(terminalId, exitCode));
        // Clean up subscription on exit
        cleanupTerminalWebSocket(terminalId);
      } else if (msg.type === 'title') {
        const title = typeof msg.title === 'string' ? msg.title : '';
        terminalTitleCallbacks.forEach((cb) => cb(terminalId, title));
      } else if (msg.type === 'claude-session') {
        const sessionId = typeof msg.sessionId === 'string' ? msg.sessionId : '';
        terminalClaudeSessionCallbacks.forEach((cb) => cb(terminalId, sessionId));
      } else if (msg.type === 'rate-limit') {
        // Rate limit passes full info object including terminalId
        terminalRateLimitCallbacks.forEach((cb) => cb({ ...msg, terminalId }));
      } else if (msg.type === 'oauth-token') {
        // OAuth token passes full info object including terminalId
        terminalOAuthTokenCallbacks.forEach((cb) => cb({ ...msg, terminalId }));
      } else if (msg.type === 'connected') {
        // Connection confirmed, nothing to do
        log.debug(`Terminal ${terminalId} WebSocket connected`);
      } else if (msg.type === 'error') {
        log.error(`Terminal ${terminalId} error:`, msg.message);
      }
    }
  });

  activeTerminalSubscriptions.set(terminalId, unsubscribe);
  log.debug(`Set up WebSocket for terminal ${terminalId}`);
}

/**
 * Clean up terminal WebSocket subscription.
 */
function cleanupTerminalWebSocket(terminalId: string): void {
  const unsubscribe = activeTerminalSubscriptions.get(terminalId);
  if (unsubscribe) {
    unsubscribe();
    activeTerminalSubscriptions.delete(terminalId);
    log.debug(`Cleaned up WebSocket for terminal ${terminalId}`);
  }
}

function emitEvent<T>(event: string, data: T): void {
  eventCallbacks.get(event)?.forEach((cb) => cb(data));
}

// GitHub API subset (nested object in API)
const githubAPI: API['github'] = {
  getGitHubRepositories: async () => ({ success: true, data: [] }),
  getGitHubIssues: async () => ({ success: true, data: [] }),
  getGitHubIssue: async () => ({ success: true, data: null as never }),
  getIssueComments: async () => ({ success: true, data: [] }),
  checkGitHubConnection: async () => ({ success: true, data: { connected: false, repoFullName: undefined, error: undefined } }),
  investigateGitHubIssue: () => { console.warn('[WebAPI] investigateGitHubIssue not implemented'); },
  importGitHubIssues: async () => ({ success: true, data: { success: true, imported: 0, failed: 0, issues: [] } }),
  closeGitHubIssue: async () => ({ success: false, error: 'Not implemented' }),
  createGitHubRelease: async () => ({ success: true, data: { url: '' } }),
  suggestReleaseVersion: async () => ({ success: true, data: { suggestedVersion: '1.0.0', currentVersion: '0.0.0', bumpType: 'minor' as const, commitCount: 0, reason: 'Initial' } }),
  checkGitHubCli: async () => ({ success: true, data: { installed: false, version: undefined } }),
  installGitHubCli: async () => ({ success: false, error: 'Not implemented' }),
  checkGitHubAuth: async () => ({ success: true, data: { authenticated: false } }),
  checkGitHubAuthStatus: async () => ({ success: true, data: { complete: false } }),
  autoDetectGitHub: async () => ({ success: true, data: { authenticated: false } }),
  startGitHubAuth: async () => ({ success: true, data: { success: false } }),
  getGitHubToken: async () => ({ success: true, data: { hasToken: false } }),
  persistGitHubToken: async () => ({ success: true, data: { tokenPersisted: false } }),
  getGitHubUser: async () => ({ success: true, data: { username: '' } }),
  listGitHubUserRepos: async () => ({ success: true, data: { repos: [] } }),
  detectGitHubRepo: async () => ({ success: true, data: '' }),
  getGitHubBranches: async () => ({ success: true, data: [] }),
  createGitHubRepo: async () => ({ success: true, data: { fullName: '', url: '' } }),
  addGitRemote: async () => ({ success: true, data: { remoteUrl: '' } }),
  listGitHubOrgs: async () => ({ success: true, data: { orgs: [] } }),
  onGitHubAuthDeviceCode: () => () => {},
  onGitHubInvestigationProgress: () => () => {},
  onGitHubInvestigationComplete: () => () => {},
  onGitHubInvestigationError: () => () => {},
  getAutoFixConfig: async (projectId: string) => {
    const result = await get(`/projects/${projectId}/auto-fix/config`);
    return (result.success ? result.data : null) as never;
  },
  saveAutoFixConfig: async (projectId: string, config: unknown) => {
    const result = await put(`/projects/${projectId}/auto-fix/config`, config);
    return result.success;
  },
  getAutoFixQueue: async (projectId: string) => {
    const result = await get(`/projects/${projectId}/auto-fix/queue`);
    return (result.success ? result.data ?? [] : []) as never;
  },
  checkAutoFixLabels: async () => [],
  checkNewIssues: async (projectId: string) => {
    // Backend's check-new endpoint imports + starts each new issue and
    // returns the list of started items. The hook expects an array of
    // ``{number, ...}``-shaped issues so it can also fire ``startAutoFix``
    // per-issue — that's redundant after this call (the agent is
    // already running) but harmless: startAutoFix is idempotent on the
    // spec side and ``agent_service`` rejects double-starts with a
    // "already running" ValueError that we swallow.
    const result = await post(`/projects/${projectId}/auto-fix/check-new`, {});
    const data = (result.success ? result.data : null) as { started?: Array<{ number: number }> } | null;
    return (data?.started ?? []) as never;
  },
  startAutoFix: (projectId: string, issueNumber: number) => {
    post(`/projects/${projectId}/auto-fix/${issueNumber}/start`, {});
  },
  onAutoFixProgress: (callback) =>
    registerCallback('auto_fix:progress',
      (payload: { projectId: string; issueNumber: number; phase: string; progress: number; message: string }) => {
        const { projectId, ...progressData } = payload;
        callback(projectId, progressData as never);
      }),
  onAutoFixComplete: (callback) =>
    registerCallback('auto_fix:complete',
      (payload: { projectId: string; issueNumber?: number }) => {
        callback(payload.projectId, payload as never);
      }),
  onAutoFixError: (callback) =>
    registerCallback('auto_fix:error',
      (payload: { projectId: string; issueNumber: number; error: string }) => {
        const { projectId, ...errorData } = payload;
        callback(projectId, errorData as never);
      }),
  listPRs: async (projectId) => {
    const result = await get(`/projects/${projectId}/github/prs`);
    return (result.success ? result.data : []) as never;
  },
  runPRReview: (projectId, prNumber) => {
    post(`/projects/${projectId}/github/prs/${prNumber}/review`, {});
  },
  cancelPRReview: async (projectId, prNumber) => {
    const result = await post(`/projects/${projectId}/github/prs/${prNumber}/cancel`, {});
    return result.success;
  },
  postPRReview: async (projectId, prNumber, selectedFindingIds) => {
    const result = await post(`/projects/${projectId}/github/prs/${prNumber}/post-review`, {
      selectedFindingIds: selectedFindingIds ?? null,
    });
    return result.success;
  },
  postPRComment: async (projectId, prNumber, body) => {
    const result = await post(`/projects/${projectId}/github/prs/${prNumber}/comment`, { body });
    return result.success;
  },
  approvePR: async (projectId, prNumber, body) => {
    const result = await post(`/projects/${projectId}/github/prs/${prNumber}/approve`, { body });
    return result.success;
  },
  mergePR: async (projectId, prNumber, mergeMethod) => {
    const result = await post(`/projects/${projectId}/github/prs/${prNumber}/merge`, {
      mergeMethod: mergeMethod ?? 'squash',
    });
    return result.success;
  },
  assignPR: async (projectId, prNumber, username) => {
    const result = await post(`/projects/${projectId}/github/prs/${prNumber}/assign`, { username });
    return result.success;
  },
  getPRReview: async (projectId, prNumber) => {
    const result = await get(`/projects/${projectId}/github/prs/${prNumber}/review`);
    const data = result.success ? result.data ?? null : null;
    return data as never;
  },
  deletePRReview: async (projectId, prNumber) => {
    const result = await del(`/projects/${projectId}/github/prs/${prNumber}/review`);
    return result.success;
  },
  checkNewCommits: async (projectId, prNumber) => {
    const result = await get(`/projects/${projectId}/github/prs/${prNumber}/new-commits`);
    return (result.success && result.data ? result.data : { hasNewCommits: false, newCommitCount: 0 }) as never;
  },
  runFollowupReview: (projectId, prNumber) => {
    post(`/projects/${projectId}/github/prs/${prNumber}/review`, { followup: true });
  },
  getPRLogs: async (projectId, prNumber) => {
    const result = await get(`/projects/${projectId}/github/prs/${prNumber}/logs`);
    return (result.success ? result.data ?? null : null) as never;
  },
  onPRReviewProgress: (callback) =>
    registerCallback('pr:review-progress',
      (payload: { projectId: string; prNumber: number; phase: string; progress: number; message: string }) => {
        const { projectId, ...progressData } = payload;
        callback(projectId, progressData as never);
      }),
  onPRReviewComplete: (callback) =>
    registerCallback('pr:review-complete',
      (payload: { projectId: string; prNumber: number; result: unknown }) => {
        const { projectId, prNumber, result } = payload;
        if (result && typeof result === 'object') {
          callback(projectId, result as never);
        } else {
          // Backend returned null result (review file not found) — synthesize minimal result
          callback(projectId, { prNumber, success: true, findings: [], summary: '', overallStatus: 'comment', reviewedAt: new Date().toISOString() } as never);
        }
      }),
  onPRReviewError: (callback) =>
    registerCallback('pr:review-error',
      (payload: { projectId: string; prNumber: number; error: string }) => {
        const { projectId, prNumber, error } = payload;
        callback(projectId, { prNumber, error } as never);
      }),
  batchAutoFix: () => {},
  getBatches: async () => [],
  onBatchProgress: () => () => {},
  onBatchComplete: () => () => {},
  onBatchError: () => () => {},
  analyzeIssuesPreview: () => {},
  approveBatches: async () => ({ success: true, batches: [] }),
  onAnalyzePreviewProgress: () => () => {},
  onAnalyzePreviewComplete: () => () => {},
  onAnalyzePreviewError: () => () => {},
};

/**
 * Web API implementation matching API interface
 */
export const webAPI: API & { _isWebMode: boolean } = {
  // Web mode marker - used by isWebMode() utility to detect web vs Electron mode
  _isWebMode: true,

  // ========== Project Operations ==========
  addProject: (projectPath: string) => post<Project>('/projects', { path: projectPath }),
  addProjectFromGitUrl: (gitUrl: string, branch?: string, name?: string) =>
    post<Project>('/projects', {
      gitUrl,
      ...(branch ? { branch } : {}),
      ...(name ? { name } : {}),
    }),
  removeProject: (projectId: string) => del(`/projects/${projectId}`),

  // Git credentials (#82 PR-C). API mounted at /api/git-credentials.
  // The api-client `get`/`post`/`del` helpers automatically prepend
  // /api so we pass paths starting at /git-credentials here.
  listGitCredentials: (orgId: string) =>
    get<import('../shared/types/ipc').GitCredentialSummary[]>(
      `/git-credentials?org_id=${encodeURIComponent(orgId)}`,
    ),
  createGitCredential: (body: import('../shared/types/ipc').CreateGitCredentialBody) =>
    post<import('../shared/types/ipc').GitCredentialSummary>(
      '/git-credentials',
      body as unknown as Record<string, unknown>,
    ),
  deleteGitCredential: (credentialId: string) =>
    del(`/git-credentials/${credentialId}`),

  // Scoped acw_ API keys (#154). API mounted at /api/keys.
  listApiKeys: () =>
    get<import('../shared/types/ipc').ApiKeySummary[]>('/keys'),
  createApiKey: (body: import('../shared/types/ipc').CreateApiKeyBody) =>
    post<import('../shared/types/ipc').CreateApiKeyResponse>(
      '/keys',
      body as unknown as Record<string, unknown>,
    ),
  revokeApiKey: (keyId: string) => del(`/keys/${keyId}`),
  getProjects: () => get<Project[]>('/projects'),
  updateProjectSettings: (projectId: string, settings: Partial<ProjectSettings>) =>
    patch(`/projects/${projectId}/settings`, settings),
  initializeProject: (projectId: string) => post(`/projects/${projectId}/initialize`),
  checkProjectVersion: (projectId: string) => get(`/projects/${projectId}/version`),

  // Tab State
  getTabState: () => get<TabState>('/settings/tab-state'),
  saveTabState: (tabState: TabState) => put('/settings/tab-state', tabState),

  // ========== Task Operations ==========
  getTasks: (projectId: string) => get<Task[]>(`/projects/${projectId}/tasks`),
  createTask: (projectId: string, title: string, description: string, metadata?: TaskMetadata) =>
    post<Task>(`/projects/${projectId}/tasks`, { title, description, metadata }),
  generateClarifications: (taskId: string) =>
    post<{ questions: Array<{ id: string; question: string; options: string[] }>; skip: boolean; skipReason: string }>(`/tasks/${taskId}/clarifications`),
  submitClarificationAnswers: (taskId: string, answers: Array<{ questionId: string; question: string; answer: string }>) =>
    post<Task>(`/tasks/${taskId}/clarifications/answers`, { answers }),
  deleteTask: (taskId: string) => del(`/tasks/${taskId}`),
  updateTask: (taskId: string, updates: { title?: string; description?: string; metadata?: Partial<TaskMetadata> }) =>
    patch<Task>(`/tasks/${taskId}`, updates),
  startTask: (taskId: string, options?: TaskStartOptions) => {
    // Prevent duplicate start requests
    if (pendingTaskStarts.has(taskId)) {
      log.warn(`Task ${taskId} start already in progress, ignoring duplicate request`);
      return;
    }
    log.info(`Starting task: ${taskId}`, options);
    pendingTaskStarts.add(taskId);
    post(`/tasks/${taskId}/start`, options ?? {})
      .then((result) => {
        if (!result.success) {
          log.error(`Failed to start task ${taskId}`, result.error);
        }
      })
      .finally(() => {
        // Clear pending state after a short delay to handle rapid clicks
        setTimeout(() => pendingTaskStarts.delete(taskId), 1000);
      });
  },
  stopTask: (taskId: string) => {
    log.info(`Stopping task: ${taskId}`);
    post(`/tasks/${taskId}/stop`).then((result) => {
      if (!result.success) {
        log.error(`Failed to stop task ${taskId}`, result.error);
      }
    }).catch((e) => log.error(`Failed to stop task ${taskId}`, e));
  },
  submitReview: (taskId: string, approved: boolean, feedback?: string) =>
    post(`/tasks/${taskId}/review`, { approved, feedback }),
  updateTaskStatus: (taskId: string, status: TaskStatus, options?: { force?: boolean }) =>
    patch(`/tasks/${taskId}/status`, { status, ...(options?.force && { force: true }) }),
  recoverStuckTask: (taskId: string, options?: TaskRecoveryOptions) =>
    post(`/tasks/${taskId}/recover`, options),
  checkTaskRunning: async (taskId: string) => {
    const result = await get<{ task_id: string; is_running: boolean }>(`/tasks/${taskId}/running`);
    if (result.success && result.data) {
      return { success: true, data: result.data.is_running };
    }
    return { success: false, data: false };
  },

  // ========== Workspace Management ==========
  getWorktreeStatus: (taskId: string) => get(`/tasks/${taskId}/worktree/status`),
  getWorktreeDiff: (taskId: string) => get(`/tasks/${taskId}/worktree/diff`),
  mergeWorktree: (taskId: string, options) => post(`/tasks/${taskId}/worktree/merge`, options),
  mergeWorktreePreview: (taskId: string) => get(`/tasks/${taskId}/worktree/merge-preview`),
  resolveWorktreeConflicts: (taskId: string, options: { useAI: boolean; strategy?: string }) =>
    post(`/tasks/${taskId}/worktree/resolve-conflicts`, options),
  resolveUncommittedConflicts: (taskId: string) =>
    post(`/tasks/${taskId}/worktree/resolve-uncommitted`),
  resolveGitMergeConflicts: (taskId: string) =>
    post(`/tasks/${taskId}/worktree/resolve-git-merge`),
  abortWorktreeMerge: (taskId: string) =>
    post(`/tasks/${taskId}/worktree/abort-merge`),
  discardWorktree: (taskId: string) => post(`/tasks/${taskId}/worktree/discard`),
  createPRFromTask: (taskId: string, options?: { title?: string; body?: string; draft?: boolean; baseBranch?: string; targetRepo?: string }) =>
    post(`/tasks/${taskId}/worktree/create-pr`, options || {}),
  getForkInfo: (projectPath: string) =>
    get(`/github/fork-info?project_path=${encodeURIComponent(projectPath)}`),
  listWorktrees: (projectId: string) => get(`/projects/${projectId}/worktrees`),
  worktreeOpenInIDE: (worktreePath: string, ide: string, customPath?: string) =>
    post('/tasks/worktree/open-in-ide', { worktreePath, ide, customPath }),
  worktreeOpenInTerminal: (worktreePath: string, terminal: string, customPath?: string) =>
    post('/tasks/worktree/open-in-terminal', { worktreePath, terminal, customPath }),
  worktreeDetectTools: () => post('/tasks/worktree/detect-tools'),

  // Task archive
  archiveTasks: (projectId: string, taskIds: string[], version?: string) =>
    post(`/projects/${projectId}/tasks/archive`, { taskIds, version }),
  unarchiveTasks: (projectId: string, taskIds: string[]) =>
    post(`/projects/${projectId}/tasks/unarchive`, { taskIds }),

  // ========== Event Listeners ==========
  // Note: WebSocket events come as {taskId, ...data} but callbacks expect (taskId, data)
  // Event types:
  //   - task:progress: Plan/implementation updates (rarely used)
  //   - task:update: Execution progress updates (primary mechanism from AgentService._emit_progress)
  //   - task:status: Status changes for kanban column movement
  onTaskProgress: (callback) => {
    // Listens for plan updates (e.g., ImplementationPlan changes)
    return registerCallback('task:progress', (payload: { taskId: string; [key: string]: unknown }) => {
      const { taskId, ...rest } = payload;
      log.debug(`[WS Event] task:progress received - taskId: ${taskId}`, rest);
      callback(taskId, rest as never);
    });
  },
  onTaskError: (callback) => {
    return registerCallback('task:error', (payload: { taskId: string; error: string }) => {
      log.debug(`[WS Event] task:error received - taskId: ${payload.taskId}, error: ${payload.error.substring(0, 100)}...`);
      callback(payload.taskId, payload.error);
    });
  },
  onTaskLog: (callback) => {
    return registerCallback('task:log', (payload: { taskId: string; log: string }) => {
      // Use trace-level logging for logs to avoid console flood
      if (window.DEBUG) {
        const logPreview = payload.log.substring(0, 50).replace(/\n/g, '\\n');
        console.debug(`[WS Event] task:log received - taskId: ${payload.taskId}, log: ${logPreview}...`);
      }
      callback(payload.taskId, payload.log);
    });
  },
  onTaskStatusChange: (callback) => {
    return registerCallback('task:status', (payload: { taskId: string; status: string; reviewReason?: string }) => {
      log.debug(`task:status received: taskId=${payload.taskId}, status=${payload.status}, reviewReason=${payload.reviewReason}`);
      callback(payload.taskId, payload.status as never, payload.reviewReason);
    });
  },
  onTaskProfileSwitch: (callback) => {
    return registerCallback('task:profile-switch', (payload: { taskId: string; [key: string]: unknown }) => {
      const { taskId, ...rest } = payload;
      callback(taskId, rest as never);
    });
  },
  onTaskExecutionProgress: (callback) => {
    // Listens for execution progress updates from AgentService
    // Backend emits task:update with executionProgress field
    return registerCallback('task:update', (payload: { taskId: string; executionProgress?: unknown; [key: string]: unknown }) => {
      const { taskId, executionProgress } = payload;
      if (executionProgress) {
        log.debug(`[WS Event] task:update (executionProgress) - taskId: ${taskId}`, executionProgress);
        callback(taskId, executionProgress as never);
      }
    });
  },
  onTaskUpdate: (callback) => {
    // General task update handler - receives full payload including executionProgress, subtasks, etc.
    return registerCallback('task:update', (payload: { taskId: string; [key: string]: unknown }) => {
      log.debug(`[WS Event] task:update - taskId: ${payload.taskId}`, {
        hasExecutionProgress: !!payload.executionProgress,
        hasSubtasks: !!(payload.subtasks),
        phase: (payload.executionProgress as { phase?: string })?.phase,
      });
      callback(payload);
    });
  },
  onTaskSubtaskUpdate: (callback) => {
    // Individual subtask status change handler for granular real-time updates
    // Emitted when a single subtask's status changes (e.g., pending -> in_progress -> completed)
    return registerCallback('task:subtask-update', (payload: { taskId: string; subtaskId: string; status: string; previousStatus?: string }) => {
      log.debug(`[WS Event] task:subtask-update - taskId: ${payload.taskId}, subtaskId: ${payload.subtaskId}, status: ${payload.previousStatus || 'N/A'} -> ${payload.status}`);
      callback(payload.taskId, payload.subtaskId, payload.status, payload.previousStatus);
    });
  },

  // ========== Terminal Operations ==========
  createTerminal: async (options: TerminalCreateOptions) => {
    const result = await post('/terminals', options);
    // Set up WebSocket subscription for this terminal
    // Use the ID from the response (backend may generate one) or fall back to options.id
    const terminalId = (result.data as { id?: string })?.id || options.id;
    if (result.success && terminalId) {
      setupTerminalWebSocket(terminalId);
    }
    return result;
  },
  destroyTerminal: async (id: string) => {
    cleanupTerminalWebSocket(id);
    return del(`/terminals/${id}`);
  },
  sendTerminalInput: (id: string, data: string) => {
    // Ensure WebSocket is set up for this terminal
    setupTerminalWebSocket(id);
    terminalWs.send(id, data);
  },
  resizeTerminal: (id: string, cols: number, rows: number) => {
    post(`/terminals/${id}/resize`, { cols, rows }).catch((e) => log.error(`Failed to resize terminal ${id}`, e));
  },
  invokeClaudeInTerminal: (id: string, cwd?: string) => {
    post(`/terminals/${id}/invoke-claude`, { cwd }).catch((e) => log.error(`Failed to invoke Claude in terminal ${id}`, e));
  },
  generateTerminalName: (command: string, cwd?: string) =>
    post('/terminals/generate-name', { command, cwd }),

  // Terminal session management
  getTerminalSessions: (projectPath: string) =>
    get<TerminalSession[]>(`/terminals/sessions?project=${encodeURIComponent(projectPath)}`),
  restoreTerminalSession: async (session: TerminalSession, cols?: number, rows?: number) => {
    const result = await post<TerminalRestoreResult>('/terminals/restore', { session, cols, rows });
    // Set up WebSocket subscription for this terminal
    if (result.success && session.id) {
      setupTerminalWebSocket(session.id);
    }
    return result;
  },
  clearTerminalSessions: (projectPath: string) =>
    del(`/terminals/sessions?project=${encodeURIComponent(projectPath)}`),
  resumeClaudeInTerminal: (id: string, sessionId?: string) => {
    post(`/terminals/${id}/resume-claude`, { sessionId });
  },
  getTerminalSessionDates: (projectPath?: string) =>
    get(`/terminals/session-dates${projectPath ? `?project=${encodeURIComponent(projectPath)}` : ''}`),
  getTerminalSessionsForDate: (date: string, projectPath: string) =>
    get(`/terminals/sessions/${date}?project=${encodeURIComponent(projectPath)}`),
  restoreTerminalSessionsFromDate: (date: string, projectPath: string, cols?: number, rows?: number) =>
    post('/terminals/restore-date', { date, projectPath, cols, rows }),
  saveTerminalBuffer: async (terminalId: string, serialized: string) => {
    await post(`/terminals/${terminalId}/buffer`, { serialized });
  },
  checkTerminalPtyAlive: (terminalId: string) => get(`/terminals/${terminalId}/alive`),

  // Terminal worktree operations
  createTerminalWorktree: async (request: CreateTerminalWorktreeRequest) => {
    const result = await post('/terminals/worktrees', request);
    return result as never; // Type coercion for compatibility
  },
  listTerminalWorktrees: (projectPath: string) =>
    get(`/terminals/worktrees?project=${encodeURIComponent(projectPath)}`),
  removeTerminalWorktree: (projectPath: string, name: string, deleteBranch?: boolean) =>
    del(`/terminals/worktrees/${name}?project=${encodeURIComponent(projectPath)}&deleteBranch=${deleteBranch ?? false}`),

  // Terminal event listeners - use per-terminal WebSocket routing
  onTerminalOutput: (callback: (id: string, data: string) => void) => {
    terminalOutputCallbacks.add(callback);
    return () => terminalOutputCallbacks.delete(callback);
  },
  onTerminalExit: (callback: (id: string, exitCode: number) => void) => {
    terminalExitCallbacks.add(callback);
    return () => terminalExitCallbacks.delete(callback);
  },
  onTerminalTitleChange: (callback: (id: string, title: string) => void) => {
    terminalTitleCallbacks.add(callback);
    return () => terminalTitleCallbacks.delete(callback);
  },
  onTerminalClaudeSession: (callback: (id: string, sessionId: string) => void) => {
    terminalClaudeSessionCallbacks.add(callback);
    return () => terminalClaudeSessionCallbacks.delete(callback);
  },
  onTerminalRateLimit: (callback) => {
    terminalRateLimitCallbacks.add(callback);
    return () => { terminalRateLimitCallbacks.delete(callback); };
  },
  onTerminalOAuthToken: (callback) => {
    terminalOAuthTokenCallbacks.add(callback);
    return () => { terminalOAuthTokenCallbacks.delete(callback); };
  },

  // ========== Claude Profile Management ==========
  getClaudeProfiles: () => get('/settings/claude-profiles'),
  saveClaudeProfile: (profile: ClaudeProfile) => post('/settings/claude-profiles', profile),
  deleteClaudeProfile: (profileId: string) => del(`/settings/claude-profiles/${profileId}`),
  renameClaudeProfile: (profileId: string, newName: string) =>
    patch(`/settings/claude-profiles/${profileId}`, { name: newName }),
  setActiveClaudeProfile: (profileId: string) =>
    post('/settings/claude-profiles/active', { profileId }),
  switchClaudeProfile: (terminalId: string, profileId: string) =>
    post(`/terminals/${terminalId}/switch-profile`, { profileId }),
  initializeClaudeProfile: (profileId: string) =>
    post(`/settings/claude-profiles/${profileId}/initialize`),
  startClaudeProfileOAuth: (profileId: string) =>
    post(`/settings/claude-profiles/${profileId}/start-oauth`),
  completeClaudeProfileOAuth: (profileId: string, code: string) =>
    post(`/settings/claude-profiles/${profileId}/complete-oauth`, { code }),
  setClaudeProfileToken: (profileId: string, token: string, email?: string) =>
    post(`/settings/claude-profiles/${profileId}/token`, { token, email }),
  getAutoSwitchSettings: () => get<ClaudeAutoSwitchSettings>('/settings/auto-switch'),
  updateAutoSwitchSettings: (settings: Partial<ClaudeAutoSwitchSettings>) =>
    patch('/settings/auto-switch', settings),
  fetchClaudeUsage: (terminalId: string) => post(`/terminals/${terminalId}/fetch-usage`),
  getBestAvailableProfile: (excludeProfileId?: string) =>
    get(`/settings/claude-profiles/best${excludeProfileId ? `?exclude=${excludeProfileId}` : ''}`),
  onSDKRateLimit: (callback) => registerCallback('sdk:rate-limit', callback),
  retryWithProfile: (request: RetryWithProfileRequest) => post('/settings/retry-with-profile', request),

  // ========== CLI Account Management (Codex & Gemini) ==========
  detectCLIAccounts: () => get('/settings/cli-accounts/detect'),
  getCLIAccountStatus: (cli: 'codex' | 'gemini') => get(`/settings/cli-accounts/${cli}/status`),
  importCLICredentials: (cli: 'codex' | 'gemini') => post(`/settings/cli-accounts/${cli}/import`),
  setCLIApiKey: (cli: 'codex' | 'gemini', apiKey: string) => post(`/settings/cli-accounts/${cli}/api-key`, { api_key: apiKey }),
  startCLILogin: (cli: 'codex' | 'gemini') => post(`/settings/cli-accounts/${cli}/start-login`),
  startCLILoginTerminal: (cli: 'codex' | 'gemini') => post(`/settings/cli-accounts/${cli}/start-login-terminal`),
  removeCLIAccount: (cli: 'codex' | 'gemini') => del(`/settings/cli-accounts/${cli}`),
  installCLI: (cli: 'codex' | 'gemini') => post(`/settings/cli-accounts/${cli}/install`),
  onCLIAccountAuth: (callback: (info: { cli: string; success: boolean }) => void) => {
    return registerCallback('cli-account-auth', callback);
  },

  // Usage monitoring
  requestUsageUpdate: () => post('/settings/usage-update'),
  onUsageUpdated: (callback) => registerCallback('usage:updated', callback),
  onProactiveSwapNotification: (callback) => registerCallback('usage:proactive-swap', callback),

  // ========== App Settings ==========
  getSettings: () => get<AppSettings>('/settings'),
  saveSettings: (settings: Partial<AppSettings>) => put('/settings', settings),
  // API Profiles
  getAPIProfiles: () => get('/settings/api-profiles'),
  saveAPIProfile: (profile) => post('/settings/api-profiles', profile),
  updateAPIProfile: (profile) => put(`/settings/api-profiles/${profile.id}`, profile),
  deleteAPIProfile: (profileId: string) => del(`/settings/api-profiles/${profileId}`),
  setActiveAPIProfile: (profileId: string | null) =>
    post('/settings/api-profiles/active', { profileId }),
  testConnection: (baseUrl: string, apiKey: string, signal?: AbortSignal) =>
    post('/settings/api-profiles/test', { baseUrl, apiKey }, signal),
  discoverModels: (baseUrl: string, apiKey: string, signal?: AbortSignal) =>
    post('/settings/api-profiles/discover-models', { baseUrl, apiKey }, signal),

  // ========== Dialog Operations (Web-adapted) ==========
  selectDirectory: async () => {
    // Web can't access filesystem directly, return null to indicate not supported
    console.warn('[WebAPI] selectDirectory not available in web mode');
    return null;
  },
  createProjectFolder: async () => ({
    success: false,
    error: 'Not available in web mode',
  }),
  getDefaultProjectLocation: async () => null,

  // App info
  getAppVersion: async () => '1.0.0-web',

  // ========== Context Operations ==========
  getProjectContext: (projectId: string) => get(`/projects/${projectId}/context`),
  refreshProjectIndex: (projectId: string) => post(`/projects/${projectId}/context/refresh`),
  getMemoryStatus: (projectId: string) => get(`/projects/${projectId}/memory/status`),
  searchMemories: (projectId: string, query: string) =>
    get(`/projects/${projectId}/memory/search?q=${encodeURIComponent(query)}`),
  getRecentMemories: (projectId: string, limit?: number) =>
    get(`/projects/${projectId}/memory/recent${limit ? `?limit=${limit}` : ''}`),

  // Environment configuration
  getProjectEnv: (projectId: string) => get<ProjectEnvConfig>(`/projects/${projectId}/env`),
  updateProjectEnv: (projectId: string, config: Partial<ProjectEnvConfig>) =>
    patch(`/projects/${projectId}/env`, config),
  checkClaudeAuth: (projectId: string) => get(`/projects/${projectId}/claude-auth`),
  invokeClaudeSetup: (projectId: string) => post(`/projects/${projectId}/claude-setup`),

  // Memory infrastructure
  getMemoryInfrastructureStatus: (dbPath?: string) =>
    get(`/memory/infrastructure${dbPath ? `?dbPath=${encodeURIComponent(dbPath)}` : ''}`),
  listMemoryDatabases: (dbPath?: string) =>
    get(`/memory/databases${dbPath ? `?dbPath=${encodeURIComponent(dbPath)}` : ''}`),
  testMemoryConnection: (dbPath?: string, database?: string) =>
    post('/memory/test-connection', { dbPath, database }),

  // Graphiti validation
  validateLLMApiKey: (provider: string, apiKey: string) =>
    post('/memory/validate-api-key', { provider, apiKey }),
  testGraphitiConnection: (config) => post('/memory/test-graphiti', config),

  // ========== GitHub Integration ==========
  getGitHubRepositories: (projectId: string) => get(`/projects/${projectId}/github/repositories`),
  getGitHubIssues: (projectId: string, state?: 'open' | 'closed' | 'all') =>
    get(`/projects/${projectId}/github/issues${state ? `?state=${state}` : ''}`),
  getGitHubIssue: (projectId: string, issueNumber: number) =>
    get(`/projects/${projectId}/github/issues/${issueNumber}`),
  checkGitHubConnection: (projectId: string) => get(`/projects/${projectId}/github/status`),
  investigateGitHubIssue: (projectId: string, issueNumber: number, selectedCommentIds?: number[]) =>
    post(`/projects/${projectId}/github/issues/${issueNumber}/investigate`, { selectedCommentIds }),
  getIssueComments: (projectId: string, issueNumber: number) =>
    get(`/projects/${projectId}/github/issues/${issueNumber}/comments`),
  importGitHubIssues: (projectId: string, issueNumbers: number[]) =>
    post(`/projects/${projectId}/github/import`, { issueNumbers }),
  closeGitHubIssue: (projectId: string, issueNumber: number) =>
    post(`/projects/${projectId}/github/issues/${issueNumber}/close`),
  createGitHubRelease: (projectId: string, version: string, releaseNotes: string, options) =>
    post(`/projects/${projectId}/github/releases`, { version, releaseNotes, ...options }),

  // GitHub OAuth
  checkGitHubCli: () => get('/github/cli/check'),
  installGitHubCli: () => post('/github/cli/install'),
  checkGitHubAuth: () => get('/github/auth/check'),
  checkGitHubAuthStatus: () => get('/github/auth/status'),
  autoDetectGitHub: (projectId?: string) =>
    get(`/github/auto-detect${projectId ? `?projectId=${encodeURIComponent(projectId)}` : ''}`),
  startGitHubAuth: () => post('/github/auth/start'),
  getGitHubToken: () => get('/github/token'),
  persistGitHubToken: (projectId: string) =>
    post('/github/persist-token', { projectId }),
  getGitHubUser: () => get('/github/user'),
  listGitHubUserRepos: () => get('/github/repos'),
  detectGitHubRepo: (projectPath: string) =>
    get(`/github/detect-repo?path=${encodeURIComponent(projectPath)}`),
  getGitHubBranches: (repo: string) =>
    get(`/github/branches?repo=${encodeURIComponent(repo)}`),
  createGitHubRepo: (repoName: string, options) =>
    post('/github/repos', { repoName, ...options }),
  addGitRemote: (projectPath: string, repoFullName: string) =>
    post('/github/remote', { projectPath, repoFullName }),
  listGitHubOrgs: () => get('/github/orgs'),
  onGitHubAuthDeviceCode: (callback) => registerCallback('github:device-code', callback),
  onGitHubInvestigationProgress: (callback) => registerCallback('github:investigation-progress', callback),
  onGitHubInvestigationComplete: (callback) => registerCallback('github:investigation-complete', callback),
  onGitHubInvestigationError: (callback) => registerCallback('github:investigation-error', callback),

  // ========== Release Operations ==========
  getReleaseableVersions: (projectId: string) => get(`/projects/${projectId}/releases/versions`),
  runReleasePreflightCheck: (projectId: string, version: string) =>
    post(`/projects/${projectId}/releases/preflight`, { version }),
  createRelease: (request: CreateReleaseRequest) =>
    post(`/projects/${request.projectId}/releases`, request),
  onReleaseProgress: (callback) => registerCallback('release:progress', callback),
  onReleaseComplete: (callback) => registerCallback('release:complete', callback),
  onReleaseError: (callback) => registerCallback('release:error', callback),

  // ========== AI Factory Source Updates ==========
  checkAutoBuildSourceUpdate: () => get('/updates/source/check'),
  downloadAutoBuildSourceUpdate: () => { post('/updates/source/download'); },
  getAutoBuildSourceVersion: () => get('/updates/source/version'),
  onAutoBuildSourceUpdateProgress: (callback) => registerCallback('updates:source-progress', callback),

  // Electron app updates (not applicable in web)
  checkAppUpdate: async () => ({ success: true, data: null }),
  downloadAppUpdate: async () => ({ success: false, error: 'Not available in web mode' }),
  installAppUpdate: () => { console.warn('[WebAPI] installAppUpdate not available in web mode'); },
  onAppUpdateAvailable: () => () => {},
  onAppUpdateDownloaded: () => () => {},
  onAppUpdateProgress: () => () => {},

  // Shell operations
  openExternal: async (url: string) => {
    window.open(url, '_blank');
  },
  openTerminal: async () => ({ success: false, error: 'Not available in web mode' }),

  // Source env
  getSourceEnv: () => get('/settings/source-env'),
  updateSourceEnv: (config) => patch('/settings/source-env', config),
  checkSourceToken: () => get('/settings/source-token-check'),

  // ========== Changelog Operations ==========
  getChangelogDoneTasks: (projectId: string, tasks) =>
    post(`/projects/${projectId}/changelog/done-tasks`, { tasks }),
  loadTaskSpecs: (projectId: string, taskIds: string[]) =>
    post(`/projects/${projectId}/changelog/specs`, { taskIds }),
  generateChangelog: (request: ChangelogGenerationRequest) => {
    post(`/projects/${request.projectId}/changelog/generate`, request);
  },
  saveChangelog: (request: ChangelogSaveRequest) =>
    post(`/projects/${request.projectId}/changelog/save`, request),
  readExistingChangelog: (projectId: string) => get(`/projects/${projectId}/changelog`),
  suggestChangelogVersion: (projectId: string, taskIds: string[]) =>
    post(`/projects/${projectId}/changelog/suggest-version`, { taskIds }),
  suggestChangelogVersionFromCommits: (projectId: string, commits) =>
    post(`/projects/${projectId}/changelog/suggest-version-commits`, { commits }),
  getChangelogBranches: (projectId: string) => get(`/projects/${projectId}/changelog/branches`),
  getChangelogTags: (projectId: string) => get(`/projects/${projectId}/changelog/tags`),
  getChangelogCommitsPreview: (projectId: string, options: GitHistoryOptions | BranchDiffOptions, mode) =>
    post(`/projects/${projectId}/changelog/commits-preview`, { options, mode }),
  saveChangelogImage: (projectId: string, imageData: string, filename: string) =>
    post(`/projects/${projectId}/changelog/images`, { imageData, filename }),
  readLocalImage: (projectPath: string, relativePath: string) =>
    get(`/files/image?path=${encodeURIComponent(projectPath)}&file=${encodeURIComponent(relativePath)}`),
  onChangelogGenerationProgress: (callback) =>
    registerCallback('changelog:progress',
      (payload: { projectId: string; phase: string; progress: number; message: string }) => {
        const { projectId, ...progressData } = payload;
        callback(projectId, progressData as never);
      }),
  onChangelogGenerationComplete: (callback) =>
    registerCallback('changelog:complete',
      (payload: { projectId: string; success: boolean; changelog: string; version: string; tasksIncluded: number }) => {
        const { projectId, ...result } = payload;
        callback(projectId, result as never);
      }),
  onChangelogGenerationError: (callback) =>
    registerCallback('changelog:error',
      (payload: { projectId: string; error: string }) => {
        const { projectId, error } = payload;
        callback(projectId, error);
      }),

  // ========== Insights Operations ==========
  getInsightsSession: (projectId: string) => get(`/projects/${projectId}/insights`),
  detectInsightsProviders: (projectId: string) => get(`/projects/${projectId}/insights/providers`),
  sendInsightsMessage: (projectId: string, message: string, modelConfig?: InsightsModelConfig) => {
    post(`/projects/${projectId}/insights/message`, { message, modelConfig });
  },
  stopInsightsMessage: (projectId: string) =>
    post(`/projects/${projectId}/insights/stop`),
  clearInsightsSession: (projectId: string) => del(`/projects/${projectId}/insights`),
  createTaskFromInsights: (projectId: string, title: string, description: string, metadata?: TaskMetadata) =>
    post(`/projects/${projectId}/insights/create-task`, { title, description, metadata }),
  generateTaskFromChat: (projectId: string, modelConfig?: InsightsModelConfig) =>
    post(`/projects/${projectId}/insights/generate-task`, { modelConfig }),
  listInsightsSessions: (projectId: string) => get(`/projects/${projectId}/insights/sessions`),
  newInsightsSession: (projectId: string) => post(`/projects/${projectId}/insights/sessions`),
  switchInsightsSession: (projectId: string, sessionId: string) =>
    post(`/projects/${projectId}/insights/sessions/${sessionId}/switch`),
  deleteInsightsSession: (projectId: string, sessionId: string) =>
    del(`/projects/${projectId}/insights/sessions/${sessionId}`),
  renameInsightsSession: (projectId: string, sessionId: string, newTitle: string) =>
    patch(`/projects/${projectId}/insights/sessions/${sessionId}`, { title: newTitle }),
  updateInsightsModelConfig: (projectId: string, sessionId: string, modelConfig: InsightsModelConfig) =>
    patch(`/projects/${projectId}/insights/sessions/${sessionId}/model`, modelConfig),
  onInsightsStreamChunk: (callback) => {
    return registerCallback('insights:chunk', (payload: { projectId: string; [key: string]: unknown }) => {
      const { projectId, ...chunk } = payload;
      callback(projectId, chunk as never);
    });
  },
  onInsightsStatus: (callback) => {
    return registerCallback('insights:status', (payload: { projectId: string; status: string }) => {
      callback(payload.projectId, { phase: payload.status, message: '' } as never);
    });
  },
  onInsightsError: (callback) => {
    return registerCallback('insights:error', (payload: { projectId: string; error: string }) => {
      callback(payload.projectId, payload.error);
    });
  },

  // ========== Task Logs ==========
  getTaskLogs: async (projectId: string, specId: string): Promise<IPCResult<TaskLogs | null>> => {
    console.log('[API] getTaskLogs called:', { projectId, specId });
    console.log('[API] Fetching URL:', `/projects/${projectId}/tasks/${specId}/logs`);
    const result = await get<TaskLogs | null>(`/projects/${projectId}/tasks/${specId}/logs`);
    console.log('[API] getTaskLogs raw result:', result);
    console.log('[API] getTaskLogs result.data:', result.data);
    console.log('[API] getTaskLogs result.data type:', typeof result.data);
    if (result.data && typeof result.data === 'object') {
      console.log('[API] getTaskLogs result.data keys:', Object.keys(result.data));
    }
    return result;
  },
  watchTaskLogs: (projectId: string, specId: string) =>
    post(`/projects/${projectId}/tasks/${specId}/logs/watch`),
  unwatchTaskLogs: (projectId: string, specId: string) =>
    post(`/projects/${projectId}/tasks/${specId}/logs/unwatch`),
  onTaskLogsChanged: (callback) => registerCallback('task-logs:changed', (payload: { specId: string; logs: unknown }) => {
    log.debug(`[WS Event] task-logs:changed received - specId: ${payload.specId}`, payload.logs);
    callback(payload.specId, payload.logs as never);
  }),
  onTaskLogsStream: (callback) => registerCallback('task-logs:stream', (payload: { specId: string; chunk: unknown }) => {
    log.debug(`[WS Event] task-logs:stream received - specId: ${payload.specId}`);
    callback(payload.specId, payload.chunk as never);
  }),

  // ========== File Explorer ==========
  listDirectory: (dirPath: string) => get(`/files/list?path=${encodeURIComponent(dirPath)}`),
  readFile: (filePath: string) => get(`/files/read?path=${encodeURIComponent(filePath)}`),
  writeFile: (filePath: string, content: string) =>
    post(`/files/write?path=${encodeURIComponent(filePath)}`, { content }),

  // ========== Project Discovery ==========
  discoverProjects: (basePath: string, maxDepth?: number) =>
    get(`/files/discover?base_path=${encodeURIComponent(basePath)}${maxDepth ? `&max_depth=${maxDepth}` : ''}`),

  // ========== Git Operations ==========
  getGitBranches: (projectPath: string) =>
    get(`/git/branches?path=${encodeURIComponent(projectPath)}`),
  getCurrentGitBranch: (projectPath: string) =>
    get(`/git/current-branch?path=${encodeURIComponent(projectPath)}`),
  detectMainBranch: (projectPath: string) =>
    get(`/git/main-branch?path=${encodeURIComponent(projectPath)}`),
  checkGitStatus: (projectPath: string) =>
    get(`/git/status?path=${encodeURIComponent(projectPath)}`),
  initializeGit: (projectPath: string) =>
    post('/git/init', { path: projectPath }),

  // ========== Ollama Operations ==========
  checkOllamaStatus: (baseUrl) =>
    get(`/ollama/status${baseUrl ? `?baseUrl=${encodeURIComponent(baseUrl)}` : ''}`),
  checkOllamaInstalled: () => get('/ollama/installed'),
  installOllama: () => post('/ollama/install'),
  listOllamaModels: (baseUrl) =>
    get(`/ollama/models${baseUrl ? `?baseUrl=${encodeURIComponent(baseUrl)}` : ''}`),
  listOllamaEmbeddingModels: (baseUrl) =>
    get(`/ollama/embedding-models${baseUrl ? `?baseUrl=${encodeURIComponent(baseUrl)}` : ''}`),
  pullOllamaModel: (modelName: string, baseUrl) =>
    post('/ollama/pull', { modelName, baseUrl }),
  onDownloadProgress: (callback) => registerCallback('ollama:download-progress', callback),

  // GitHub API (nested)
  github: githubAPI,

  // ========== Claude Code CLI ==========
  checkClaudeCodeVersion: () => get('/claude-code/version'),
  installClaudeCode: () => post('/claude-code/install'),

  // ========== Auth Status ==========
  getAuthStatus: () => get('/settings/auth-status'),
  checkClaudeCredentialsExist: () => get('/settings/claude-credentials-exist'),
  importClaudeCredentials: () => post('/settings/import-claude-credentials'),

  // ========== Debug Operations ==========
  getDebugInfo: async () => ({
    systemInfo: { appVersion: '1.0.0-web', platform: 'web', isPackaged: 'false' },
    recentErrors: [],
    logsPath: '/api/logs',
    debugReport: '[Web Mode] Debug report not available',
  }),
  openLogsFolder: async () => ({ success: false, error: 'Not available in web mode' }),
  copyDebugInfo: async () => ({ success: false, error: 'Not available in web mode' }),
  getRecentErrors: async () => [],
  listLogFiles: async () => [],

  // MCP Health Check & Detection
  checkMcpHealth: (server: CustomMcpServer) => post('/mcp/health', server),
  testMcpConnection: (server: CustomMcpServer) => post('/mcp/test-connection', server),
  detectMcpServices: () => get('/mcp/detect'),
};

/**
 * Initialize web API by setting window.API
 */
export function initWebAPI(): void {
  log.info('Initializing web API adapter');
  (window as Window & { API: API }).API = webAPI;
  (window as Window & { DEBUG: boolean }).DEBUG = import.meta.env.DEV;

  // Set up global WebSocket event forwarding
  setupEventBroadcast();
  log.debug('Web API initialization complete');
}

/**
 * Connect to a global events WebSocket for server-pushed events
 */
function setupEventBroadcast(): void {
  log.debug('Setting up WebSocket event broadcast');
  // Subscribe to global events channel
  wsManager.subscribe('/ws/events', (data: unknown) => {
    const event = data as { type: string; payload: unknown };
    if (event && event.type) {
      // Log task-related events with more detail for debugging live updates
      if (event.type.startsWith('task:') || event.type.startsWith('task-logs:')) {
        const payload = event.payload as { taskId?: string; [key: string]: unknown };
        log.debug(`[WS Broadcast] ${event.type} - taskId: ${payload?.taskId || 'N/A'}`, {
          payloadKeys: payload ? Object.keys(payload) : [],
        });
      } else {
        log.debug(`[WS Broadcast] ${event.type}`);
      }
      emitEvent(event.type, event.payload);
    }
  });

  // Self-heal on reconnect: if the socket drops (browser sleep, network blip,
  // server restart) we miss the events broadcast during the gap. Re-fetch the
  // task list whenever the socket *re*-connects so the kanban board converges
  // to actual server state. Stores are loaded lazily to avoid an import cycle
  // between api-adapter ↔ task-store.
  let initialConnect = true;
  wsManager.onConnect('/ws/events', () => {
    if (initialConnect) { initialConnect = false; return; }
    log.debug('[WS] /ws/events reconnected — refetching tasks');
    void Promise.all([
      import('../stores/project-store'),
      import('../stores/task-store'),
    ]).then(([projectMod, taskMod]) => {
      const projectId = projectMod.useProjectStore.getState().selectedProjectId;
      if (projectId) {
        void taskMod.loadTasks(projectId);
      }
    }).catch((err) => {
      log.error('[WS reconnect] Failed to refetch tasks:', err);
    });
  });

  // Belt-and-braces: when the tab becomes visible again, also refetch. Some
  // browsers (Chrome on mobile, Safari) heavily throttle background WS without
  // firing onclose, so a reconnect may never happen even though events were
  // dropped. visibilitychange catches that path.
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState !== 'visible') return;
      void Promise.all([
        import('../stores/project-store'),
        import('../stores/task-store'),
      ]).then(([projectMod, taskMod]) => {
        const projectId = projectMod.useProjectStore.getState().selectedProjectId;
        if (projectId) {
          log.debug('[WS] tab visible — refetching tasks');
          void taskMod.loadTasks(projectId);
        }
      }).catch((err) => {
        log.error('[visibilitychange] Failed to refetch tasks:', err);
      });
    });
  }
}
