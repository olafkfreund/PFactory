/**
 * Model and agent profile constants
 * Claude models, thinking levels, memory backends, and agent profiles
 */

import type { AgentProfile, PhaseModelConfig, FeatureModelConfig, FeatureThinkingConfig } from '../types/settings';
import { apiRequest } from '../../lib/api-client';

// ============================================
// Available Models
// ============================================

export const AVAILABLE_MODELS = [
  { value: 'opus', label: 'Claude Opus 4.7' },
  { value: 'sonnet', label: 'Claude Sonnet 4.6' },
  { value: 'haiku', label: 'Claude Haiku 4.5' }
] as const;

// Models available for all phases (Claude + alternative providers)
// The provider is inferred from the model ID on the backend, so no separate
// provider setting is needed per phase.
export const ALL_AVAILABLE_MODELS = [
  { value: 'opus', label: 'Claude Opus 4.7' },
  { value: 'sonnet', label: 'Claude Sonnet 4.6' },
  { value: 'haiku', label: 'Claude Haiku 4.5' },
  { value: 'gpt-5.5', label: 'GPT-5.5' },
  { value: 'gpt-5.4', label: 'GPT-5.4' },
  { value: 'gpt-5.4-mini', label: 'GPT-5.4 Mini' },
  { value: 'gpt-5.4-nano', label: 'GPT-5.4 Nano' },
  { value: 'gpt-5.3-codex', label: 'Codex — GPT-5.3' },
  { value: 'gemini-3.1-pro-preview', label: 'Gemini 3.1 Pro (Preview)' },
  { value: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro' },
  { value: 'gemini-3.5-flash', label: 'Gemini 3.5 Flash' },
  { value: 'gemini-3.1-flash-lite', label: 'Gemini 3.1 Flash-Lite' },
  { value: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash' },
] as const;

// Backward compatibility alias
export const QA_AVAILABLE_MODELS = ALL_AVAILABLE_MODELS;

// Dynamically fetch installed Ollama chat models for phase dropdowns
export async function fetchOllamaModels(): Promise<{ value: string; label: string }[]> {
  try {
    const result = await apiRequest<{ models: { name: string }[] }>('/settings/ollama/models');
    if (result.success && result.data?.models) {
      return result.data.models.map(m => ({
        value: `ollama:${m.name}`,
        label: `Ollama — ${m.name}`,
      }));
    }
  } catch { /* Ollama not running — no models */ }
  return [];
}

// Fetch the user's saved OpenAI-compatible endpoints (LM Studio, vLLM, etc.)
// and surface each endpoint's default_model as a picker entry.
// Embedding models are skipped — they can't drive generative/code tasks.
const EMBEDDING_PATTERNS = [
  /embed/i,
  /^text-embedding/i,
  /-embedding(-|$)/i,
  /nomic-embed/i,
  /bge-/i,
  /e5-/i,
  /gte-/i,
];

export function isEmbeddingModel(modelName: string): boolean {
  return EMBEDDING_PATTERNS.some((re) => re.test(modelName));
}

interface OpenAIEndpointSummary {
  id: string;
  label: string;
  default_model: string;
}

export async function fetchOpenAIEndpointModels(): Promise<
  { value: string; label: string }[]
> {
  try {
    const result = await apiRequest<OpenAIEndpointSummary[]>('/llm-endpoints');
    if (result.success && result.data) {
      return result.data
        .filter((e) => e.default_model && !isEmbeddingModel(e.default_model))
        .map((e) => ({
          // Backend reads "openai-compatible:<label>:<model>" — see
          // apps/backend/phase_config.get_provider_extra_kwargs().
          value: `openai-compatible:${e.label}:${e.default_model}`,
          label: `${e.label} — ${e.default_model}`,
        }));
    }
  } catch { /* No endpoints configured */ }
  return [];
}

// Backward compatibility alias
export const fetchOllamaQAModels = fetchOllamaModels;

// Dynamically fetch models from any OpenAI-compatible server (LM Studio, vLLM, LocalAI, etc.)
export async function fetchOpenAICompatibleModels(baseUrl?: string): Promise<{ value: string; label: string }[]> {
  try {
    const query = baseUrl ? `?baseUrl=${encodeURIComponent(baseUrl)}` : '';
    const result = await apiRequest<{ models: { name: string }[] }>(`/settings/openai-compat/models${query}`);
    if (result.success && result.data?.models) {
      return result.data.models.map(m => ({
        value: `openai_compat:${m.name}`,
        label: `OpenAI Compat — ${m.name}`,
      }));
    }
  } catch { /* OpenAI-compatible server not running — no models */ }
  return [];
}

// Maps model shorthand to actual Claude model IDs
export const MODEL_ID_MAP: Record<string, string> = {
  opus: 'claude-opus-4-7',
  sonnet: 'claude-sonnet-4-6',
  haiku: 'claude-haiku-4-5-20251001'
} as const;

// Maps thinking levels to budget tokens (null = no extended thinking)
export const THINKING_BUDGET_MAP: Record<string, number | null> = {
  none: null,
  low: 1024,
  medium: 4096,
  high: 16384,
  max: 65536
} as const;

// ============================================
// Thinking Levels
// ============================================

// Thinking levels for Claude model (budget token allocation)
export const THINKING_LEVELS = [
  { value: 'none', label: 'None', description: 'No extended thinking' },
  { value: 'low', label: 'Low', description: 'Brief consideration' },
  { value: 'medium', label: 'Medium', description: 'Moderate analysis' },
  { value: 'high', label: 'High', description: 'Deep thinking' }
] as const;

// ============================================
// Agent Profiles
// ============================================

// Default phase model configuration for Auto profile
// Uses a high-capability model across all phases for maximum quality
export const DEFAULT_PHASE_MODELS: PhaseModelConfig = {
  spec: 'opus',       // Best quality for spec creation
  planning: 'opus',   // Complex architecture decisions benefit from highest-capability model
  coding: 'opus',     // Highest quality implementation
  qa: 'opus',         // Thorough QA review
  qa_fixer: 'sonnet'  // Efficient QA fixing
};

// Default phase thinking configuration for Auto profile
export const DEFAULT_PHASE_THINKING: import('../types/settings').PhaseThinkingConfig = {
  spec: 'high',       // Deep thinking for comprehensive spec creation
  planning: 'high',   // High thinking for planning complex features
  coding: 'low',      // Faster coding iterations
  qa: 'low',          // Efficient QA review
  qa_fixer: 'low'     // Efficient QA fixing
};

// ============================================
// Feature Settings (Non-Pipeline Features)
// ============================================

// Default feature model configuration (for insights, github, utility)
export const DEFAULT_FEATURE_MODELS: FeatureModelConfig = {
  insights: 'sonnet',     // Fast, responsive chat
  githubIssues: 'opus',   // Issue triage and analysis benefits from Opus
  githubPrs: 'opus',      // PR review benefits from thorough Opus analysis
  utility: 'haiku'        // Fast utility operations (commit messages, merge resolution)
};

// Default feature thinking configuration
export const DEFAULT_FEATURE_THINKING: FeatureThinkingConfig = {
  insights: 'medium',     // Balanced thinking for chat
  githubIssues: 'medium', // Moderate thinking for issue analysis
  githubPrs: 'medium',    // Moderate thinking for PR review
  utility: 'low'          // Fast thinking for utility operations
};

// Feature labels for UI display
export const FEATURE_LABELS: Record<keyof FeatureModelConfig, { label: string; description: string }> = {
  insights: { label: 'Insights Chat', description: 'Ask questions about your codebase' },
  githubIssues: { label: 'GitHub Issues', description: 'Automated issue triage and labeling' },
  githubPrs: { label: 'GitHub PR Review', description: 'AI-powered pull request reviews' },
  utility: { label: 'Utility', description: 'Commit messages and merge conflict resolution' }
};

// Default agent profiles for preset model/thinking configurations
export const DEFAULT_AGENT_PROFILES: AgentProfile[] = [
  {
    id: 'auto',
    name: 'Auto (Optimized)',
    description: 'Optimized phase-by-phase model selection with extended thinking',
    model: 'opus',  // Fallback/default model
    thinkingLevel: 'high',
    icon: 'Sparkles',
    isAutoProfile: true,
    phaseModels: DEFAULT_PHASE_MODELS,
    phaseThinking: DEFAULT_PHASE_THINKING
  },
  {
    id: 'complex',
    name: 'Complex Tasks',
    description: 'For intricate, multi-step implementations requiring deep analysis',
    model: 'opus',
    thinkingLevel: 'high',
    icon: 'Brain'
  },
  {
    id: 'balanced',
    name: 'Balanced',
    description: 'Good balance of speed and quality for most tasks',
    model: 'sonnet',
    thinkingLevel: 'medium',
    icon: 'Scale'
  },
  {
    id: 'quick',
    name: 'Quick Edits',
    description: 'Fast iterations for simple changes and quick fixes',
    model: 'haiku',
    thinkingLevel: 'low',
    icon: 'Zap'
  },
  {
    id: 'custom',
    name: 'Custom',
    description: 'Choose your own model from any provider and configure settings manually',
    model: 'sonnet',  // Default — user overrides this
    thinkingLevel: 'medium',
    icon: 'Settings',
    isCustomProfile: true
  }
];

// ============================================
// Memory Backends
// ============================================

export const MEMORY_BACKENDS = [
  { value: 'file', label: 'File-based (default)' },
  { value: 'graphiti', label: 'Graphiti (LadybugDB)' }
] as const;

// ============================================
// Provider Models (static fallbacks per provider)
// ============================================

import type { InsightsProvider } from '../types/insights';

export const PROVIDER_MODELS: Record<string, { id: string; label: string }[]> = {
  claude: [
    { id: 'opus', label: 'Claude Opus 4.7' },
    { id: 'sonnet', label: 'Claude Sonnet 4.6' },
    { id: 'haiku', label: 'Claude Haiku 4.5' },
  ],
  codex: [
    { id: 'gpt-5.5', label: 'GPT-5.5' },
    { id: 'gpt-5.4', label: 'GPT-5.4' },
    { id: 'gpt-5.4-mini', label: 'GPT-5.4 Mini' },
    { id: 'gpt-5.4-nano', label: 'GPT-5.4 Nano' },
    { id: 'gpt-5.3-codex', label: 'GPT-5.3 Codex' },
  ],
  gemini: [
    { id: 'gemini-3.1-pro-preview', label: 'Gemini 3.1 Pro (Preview)' },
    { id: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro' },
    { id: 'gemini-3.5-flash', label: 'Gemini 3.5 Flash' },
    { id: 'gemini-3.1-flash-lite', label: 'Gemini 3.1 Flash-Lite' },
    { id: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash' },
  ],
  ollama: [],         // Dynamic — populated from detection
  lmstudio: [],       // Dynamic
  localai: [],        // Dynamic
  vllm: [],           // Dynamic
  jan: [],            // Dynamic
  openai_compat: [],  // Dynamic — populated from any OpenAI-compatible server
};

export const PROVIDER_INFO: Record<InsightsProvider, { displayName: string; icon: string }> = {
  claude: { displayName: 'Claude', icon: 'sparkles' },
  codex: { displayName: 'Codex (OpenAI)', icon: 'openai' },
  gemini: { displayName: 'Gemini (Google)', icon: 'gemini' },
  ollama: { displayName: 'Ollama', icon: 'ollama' },
  lmstudio: { displayName: 'LM Studio', icon: 'lmstudio' },
  localai: { displayName: 'LocalAI', icon: 'localai' },
  vllm: { displayName: 'vLLM', icon: 'vllm' },
  jan: { displayName: 'Jan', icon: 'jan' },
  openai_compat: { displayName: 'OpenAI Compatible', icon: 'openai_compat' },
};
