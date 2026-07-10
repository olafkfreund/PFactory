/**
 * Application configuration constants
 * Default settings, file paths, and project structure
 */

// ============================================
// UI Scale Constants
// ============================================

export const UI_SCALE_MIN = 75;
export const UI_SCALE_MAX = 200;
export const UI_SCALE_DEFAULT = 125;
export const UI_SCALE_STEP = 5;

// ============================================
// Default App Settings
// ============================================

export const DEFAULT_APP_SETTINGS = {
  theme: 'system' as const,
  colorTheme: 'gruvbox' as const,
  defaultModel: 'opus',
  agentFramework: 'pfactory',
  autoBuildPath: undefined as string | undefined,
  autoUpdateAutoBuild: true,
  autoNameTerminals: true,
  onboardingCompleted: false,
  notifications: {
    onTaskComplete: true,
    onTaskFailed: true,
    onReviewNeeded: true,
    sound: false,
    emailEnabled: false
  },
  // Global API keys (used as defaults for all projects)
  globalClaudeOAuthToken: undefined as string | undefined,
  globalOpenAIApiKey: undefined as string | undefined,
  // Selected agent profile - defaults to 'auto' for per-phase optimized model selection
  selectedAgentProfile: 'auto',
  // Changelog preferences (persisted between sessions)
  changelogFormat: 'keep-a-changelog' as const,
  changelogAudience: 'user-facing' as const,
  changelogEmojiLevel: 'none' as const,
  // UI Scale (default 125%)
  uiScale: UI_SCALE_DEFAULT,
  // Beta updates opt-in (receive pre-release versions)
  betaUpdates: false,
  // Language preference (default to English)
  language: 'en' as const
};

// ============================================
// Default Project Settings
// ============================================

export const DEFAULT_PROJECT_SETTINGS = {
  model: 'opus',
  memoryBackend: 'file' as const,
  notifications: {
    onTaskComplete: true,
    onTaskFailed: true,
    onReviewNeeded: true,
    sound: false,
    emailEnabled: false
  },
  // Graphiti MCP server for agent-accessible knowledge graph (enabled by default)
  graphitiMcpEnabled: true,
  graphitiMcpUrl: 'http://localhost:3114/mcp/',
  // Include CLAUDE.md instructions in agent context (enabled by default)
  useClaudeMd: true
};

// ============================================
// Auto Build File Paths
// ============================================

// File paths relative to project
// IMPORTANT: All paths use .pfactory/ (the installed instance), NOT pfactory/ (source code)
export const AUTO_BUILD_PATHS = {
  SPECS_DIR: '.pfactory/specs',
  IMPLEMENTATION_PLAN: 'test_plan.json',
  SPEC_FILE: 'spec.md',
  QA_REPORT: 'qa_report.md',
  BUILD_PROGRESS: 'build-progress.txt',
  CONTEXT: 'context.json',
  REQUIREMENTS: 'requirements.json',
  PROJECT_INDEX: '.pfactory/project_index.json',
  GRAPHITI_STATE: '.graphiti_state.json'
} as const;

/**
 * Get the specs directory path.
 * All specs go to .pfactory/specs/ (the project's data directory).
 */
export function getSpecsDir(autoBuildPath: string | undefined): string {
  const basePath = autoBuildPath || '.pfactory';
  return `${basePath}/specs`;
}
