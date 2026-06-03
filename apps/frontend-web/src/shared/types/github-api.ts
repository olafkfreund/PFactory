/**
 * GitHub API types for web UI
 * Extracted from preload/api/modules/github-api.ts
 */

import type {
  GitHubRepository,
  GitHubIssue,
  GitHubSyncStatus,
  GitHubImportResult,
  GitHubInvestigationStatus,
  GitHubInvestigationResult,
  IPCResult,
} from './index';

export type IpcListenerCleanup = () => void;

/**
 * Auto-fix configuration
 */
export interface AutoFixConfig {
  enabled: boolean;
  labels: string[];
  requireHumanApproval: boolean;
  botToken?: string;
  model: string;
  thinkingLevel: string;
}

/**
 * Auto-fix queue item
 */
export interface AutoFixQueueItem {
  issueNumber: number;
  repo: string;
  status: 'pending' | 'analyzing' | 'creating_spec' | 'building' | 'qa_review' | 'pr_created' | 'completed' | 'failed';
  specId?: string;
  prNumber?: number;
  error?: string;
  createdAt: string;
  updatedAt: string;
}

/**
 * Auto-fix progress status
 */
export interface AutoFixProgress {
  phase: 'checking' | 'fetching' | 'analyzing' | 'batching' | 'creating_spec' | 'building' | 'qa_review' | 'creating_pr' | 'complete';
  issueNumber: number;
  progress: number;
  message: string;
}

/**
 * Issue batch for grouped fixing
 */
export interface IssueBatch {
  batchId: string;
  repo: string;
  primaryIssue: number;
  issues: Array<{
    issueNumber: number;
    title: string;
    similarityToPrimary: number;
  }>;
  commonThemes: string[];
  status: 'pending' | 'analyzing' | 'creating_spec' | 'building' | 'qa_review' | 'pr_created' | 'completed' | 'failed';
  specId?: string;
  prNumber?: number;
  error?: string;
  createdAt: string;
  updatedAt: string;
}

/**
 * Batch progress status
 */
export interface BatchProgress {
  phase: 'analyzing' | 'batching' | 'creating_specs' | 'complete';
  progress: number;
  message: string;
  totalIssues: number;
  batchCount: number;
}

/**
 * Analyze preview progress (proactive workflow)
 */
export interface AnalyzePreviewProgress {
  phase: 'analyzing' | 'complete';
  progress: number;
  message: string;
}

/**
 * Proposed batch from analyze-preview
 */
export interface ProposedBatch {
  primaryIssue: number;
  issues: Array<{
    issueNumber: number;
    title: string;
    labels: string[];
    similarityToPrimary: number;
  }>;
  issueCount: number;
  commonThemes: string[];
  validated: boolean;
  confidence: number;
  reasoning: string;
  theme: string;
}

/**
 * Analyze preview result (proactive batch workflow)
 */
export interface AnalyzePreviewResult {
  success: boolean;
  totalIssues: number;
  analyzedIssues: number;
  alreadyBatched: number;
  proposedBatches: ProposedBatch[];
  singleIssues: Array<{
    issueNumber: number;
    title: string;
    labels: string[];
  }>;
  message: string;
  error?: string;
}

/**
 * PR data from GitHub API
 */
export interface PRData {
  number: number;
  title: string;
  body: string;
  state: string;
  author: { login: string };
  headRefName: string;
  baseRefName: string;
  additions: number;
  deletions: number;
  changedFiles: number;
  assignees: Array<{ login: string }>;
  files: Array<{
    path: string;
    additions: number;
    deletions: number;
    status: string;
  }>;
  createdAt: string;
  updatedAt: string;
  htmlUrl: string;
}

/**
 * PR review finding
 */
export interface PRReviewFinding {
  id: string;
  severity: 'critical' | 'high' | 'medium' | 'low';
  category: 'security' | 'quality' | 'style' | 'test' | 'docs' | 'pattern' | 'performance';
  title: string;
  description: string;
  file: string;
  line: number;
  endLine?: number;
  suggestedFix?: string;
  fixable: boolean;
}

/**
 * PR review result
 */
export interface PRReviewResult {
  prNumber: number;
  repo: string;
  success: boolean;
  findings: PRReviewFinding[];
  summary: string;
  overallStatus: 'approve' | 'request_changes' | 'comment';
  reviewId?: number;
  reviewedAt: string;
  error?: string;
  reviewedCommitSha?: string;
  reviewedFileBlobs?: Record<string, string>;
  isFollowupReview?: boolean;
  previousReviewId?: number;
  resolvedFindings?: string[];
  unresolvedFindings?: string[];
  newFindingsSinceLastReview?: string[];
  hasPostedFindings?: boolean;
  postedFindingIds?: string[];
  postedAt?: string;
}

/**
 * Result of checking for new commits since last review
 */
export interface NewCommitsCheck {
  hasNewCommits: boolean;
  newCommitCount: number;
  lastReviewedCommit?: string;
  currentHeadCommit?: string;
  hasCommitsAfterPosting?: boolean;
}

/**
 * Review progress status
 */
export interface PRReviewProgress {
  phase: 'starting' | 'fetching' | 'analyzing' | 'generating' | 'posting' | 'complete';
  prNumber: number;
  progress: number;
  message: string;
}

/**
 * PR review log entry type
 */
export type PRLogEntryType = 'text' | 'tool_start' | 'tool_end' | 'phase_start' | 'phase_end' | 'error' | 'success' | 'info';

/**
 * PR review log phase
 */
export type PRLogPhase = 'context' | 'analysis' | 'synthesis';

/**
 * Single log entry in PR review
 */
export interface PRLogEntry {
  timestamp: string;
  type: PRLogEntryType;
  content: string;
  phase: PRLogPhase;
  source?: string;
  detail?: string;
  collapsed?: boolean;
}

/**
 * Phase log containing entries
 */
export interface PRPhaseLog {
  phase: PRLogPhase;
  status: 'pending' | 'active' | 'completed' | 'failed';
  started_at: string | null;
  completed_at: string | null;
  entries: PRLogEntry[];
}

/**
 * Complete PR review logs
 */
export interface PRLogs {
  pr_number: number;
  repo: string;
  created_at: string;
  updated_at: string;
  is_followup: boolean;
  phases: {
    context: PRPhaseLog;
    analysis: PRPhaseLog;
    synthesis: PRPhaseLog;
  };
}

/**
 * GitHub Integration API interface
 */
export interface GitHubAPI {
  getGitHubRepositories: (projectId: string) => Promise<IPCResult<GitHubRepository[]>>;
  getGitHubIssues: (projectId: string, state?: 'open' | 'closed' | 'all') => Promise<IPCResult<GitHubIssue[]>>;
  getGitHubIssue: (projectId: string, issueNumber: number) => Promise<IPCResult<GitHubIssue>>;
  getIssueComments: (projectId: string, issueNumber: number) => Promise<IPCResult<unknown[]>>;
  checkGitHubConnection: (projectId: string) => Promise<IPCResult<GitHubSyncStatus>>;
  investigateGitHubIssue: (projectId: string, issueNumber: number, selectedCommentIds?: number[]) => Promise<IPCResult> | void;
  importGitHubIssues: (projectId: string, issueNumbers: number[]) => Promise<IPCResult<GitHubImportResult>>;
  createGitHubRelease: (
    projectId: string,
    version: string,
    releaseNotes: string,
    options?: { draft?: boolean; prerelease?: boolean }
  ) => Promise<IPCResult<{ url: string }>>;
  suggestReleaseVersion: (projectId: string) => Promise<IPCResult<{ suggestedVersion: string; currentVersion: string; bumpType: 'major' | 'minor' | 'patch'; commitCount: number; reason: string }>>;
  checkGitHubCli: () => Promise<IPCResult<{ installed: boolean; version?: string }>>;
  installGitHubCli: () => Promise<IPCResult<{ message: string; version: string; steps_completed: string[] }>>;
  checkGitHubAuth: () => Promise<IPCResult<{ authenticated: boolean; username?: string }>>;
  checkGitHubAuthStatus: () => Promise<IPCResult<{ complete: boolean; success?: boolean; error?: string }>>;
  autoDetectGitHub: (projectId?: string) => Promise<IPCResult<{ authenticated: boolean; username?: string; tokenPersisted?: boolean; reason?: string }>>;
  startGitHubAuth: () => Promise<IPCResult<{ success: boolean; message?: string; deviceCode?: string; authUrl?: string; awaiting?: boolean }>>;
  getGitHubToken: () => Promise<IPCResult<{ hasToken: boolean }>>;
  persistGitHubToken: (projectId: string) => Promise<IPCResult<{ tokenPersisted: boolean }>>;
  getGitHubUser: () => Promise<IPCResult<{ username: string; name?: string }>>;
  listGitHubUserRepos: () => Promise<IPCResult<{ repos: Array<{ fullName: string; description: string | null; isPrivate: boolean }> }>>;
  onGitHubAuthDeviceCode: (callback: (data: { deviceCode: string; authUrl: string; browserOpened: boolean }) => void) => IpcListenerCleanup;
  detectGitHubRepo: (projectPath: string) => Promise<IPCResult<string>>;
  getGitHubBranches: (repo: string) => Promise<IPCResult<string[]>>;
  createGitHubRepo: (repoName: string, options: { description?: string; isPrivate?: boolean; projectPath: string; owner?: string }) => Promise<IPCResult<{ fullName: string; url: string }>>;
  addGitRemote: (projectPath: string, repoFullName: string) => Promise<IPCResult<{ remoteUrl: string }>>;
  listGitHubOrgs: () => Promise<IPCResult<{ orgs: Array<{ login: string; avatarUrl?: string }> }>>;
  onGitHubInvestigationProgress: (callback: (projectId: string, status: GitHubInvestigationStatus) => void) => IpcListenerCleanup;
  onGitHubInvestigationComplete: (callback: (projectId: string, result: GitHubInvestigationResult) => void) => IpcListenerCleanup;
  onGitHubInvestigationError: (callback: (projectId: string, error: string) => void) => IpcListenerCleanup;
  getAutoFixConfig: (projectId: string) => Promise<AutoFixConfig | null>;
  saveAutoFixConfig: (projectId: string, config: AutoFixConfig) => Promise<boolean>;
  getAutoFixQueue: (projectId: string) => Promise<AutoFixQueueItem[]>;
  checkAutoFixLabels: (projectId: string) => Promise<number[]>;
  checkNewIssues: (projectId: string) => Promise<Array<{number: number}>>;
  startAutoFix: (projectId: string, issueNumber: number) => void;
  batchAutoFix: (projectId: string, issueNumbers?: number[]) => void;
  getBatches: (projectId: string) => Promise<IssueBatch[]>;
  onAutoFixProgress: (callback: (projectId: string, progress: AutoFixProgress) => void) => IpcListenerCleanup;
  onAutoFixComplete: (callback: (projectId: string, result: AutoFixQueueItem) => void) => IpcListenerCleanup;
  onAutoFixError: (callback: (projectId: string, error: { issueNumber: number; error: string }) => void) => IpcListenerCleanup;
  onBatchProgress: (callback: (projectId: string, progress: BatchProgress) => void) => IpcListenerCleanup;
  onBatchComplete: (callback: (projectId: string, batches: IssueBatch[]) => void) => IpcListenerCleanup;
  onBatchError: (callback: (projectId: string, error: { error: string }) => void) => IpcListenerCleanup;
  analyzeIssuesPreview: (projectId: string, issueNumbers?: number[], maxIssues?: number) => void;
  approveBatches: (projectId: string, approvedBatches: ProposedBatch[]) => Promise<{ success: boolean; batches?: IssueBatch[]; error?: string }>;
  onAnalyzePreviewProgress: (callback: (projectId: string, progress: AnalyzePreviewProgress) => void) => IpcListenerCleanup;
  onAnalyzePreviewComplete: (callback: (projectId: string, result: AnalyzePreviewResult) => void) => IpcListenerCleanup;
  onAnalyzePreviewError: (callback: (projectId: string, error: { error: string }) => void) => IpcListenerCleanup;
  closeGitHubIssue: (projectId: string, issueNumber: number) => Promise<IPCResult>;
  listPRs: (projectId: string) => Promise<PRData[]>;
  runPRReview: (projectId: string, prNumber: number) => void;
  cancelPRReview: (projectId: string, prNumber: number) => Promise<boolean>;
  postPRReview: (projectId: string, prNumber: number, selectedFindingIds?: string[]) => Promise<boolean>;
  deletePRReview: (projectId: string, prNumber: number) => Promise<boolean>;
  postPRComment: (projectId: string, prNumber: number, body: string) => Promise<boolean>;
  approvePR: (projectId: string, prNumber: number, body: string) => Promise<boolean>;
  mergePR: (projectId: string, prNumber: number, mergeMethod?: 'merge' | 'squash' | 'rebase') => Promise<boolean>;
  assignPR: (projectId: string, prNumber: number, username: string) => Promise<boolean>;
  getPRReview: (projectId: string, prNumber: number) => Promise<PRReviewResult | null>;
  checkNewCommits: (projectId: string, prNumber: number) => Promise<NewCommitsCheck>;
  runFollowupReview: (projectId: string, prNumber: number) => void;
  getPRLogs: (projectId: string, prNumber: number) => Promise<PRLogs | null>;
  onPRReviewProgress: (callback: (projectId: string, progress: PRReviewProgress) => void) => IpcListenerCleanup;
  onPRReviewComplete: (callback: (projectId: string, result: PRReviewResult) => void) => IpcListenerCleanup;
  onPRReviewError: (callback: (projectId: string, error: { prNumber: number; error: string }) => void) => IpcListenerCleanup;
}
