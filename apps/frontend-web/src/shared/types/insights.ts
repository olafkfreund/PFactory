/**
 * Insights types
 */

import type { TaskMetadata } from './task';

// ============================================
// Insights Chat Types
// ============================================

import type { ThinkingLevel } from './settings';

// Supported LLM providers
export type InsightsProvider = 'claude' | 'codex' | 'gemini' | 'ollama'
  | 'lmstudio' | 'localai' | 'vllm' | 'jan' | 'openai_compat';

// Model configuration for insights sessions
export interface InsightsModelConfig {
  provider: InsightsProvider;    // LLM provider (default: 'claude')
  profileId: string;             // 'complex' | 'balanced' | 'quick' | 'custom'
  model: string;                 // Model ID (e.g. 'opus', 'llama3:8b', 'gpt-4o')
  thinkingLevel?: ThinkingLevel; // Only applicable for Claude
}

// Provider info returned from detection endpoint
export interface InsightsProviderInfo {
  provider: InsightsProvider;
  available: boolean;
  displayName: string;
  icon: string;
  authMethod: string | null;
  models: { id: string; label: string }[];
}

export type InsightsChatRole = 'user' | 'assistant';

// Tool usage record for showing what tools the AI used
export interface InsightsToolUsage {
  name: string;
  input?: string;
  timestamp: Date;
}

export interface InsightsChatMessage {
  id: string;
  role: InsightsChatRole;
  content: string;
  timestamp: Date;
  // For assistant messages that suggest task creation
  suggestedTask?: {
    title: string;
    description: string;
    metadata?: TaskMetadata;
  };
  // Tools used during this response (assistant messages only)
  toolsUsed?: InsightsToolUsage[];
  // Provider info (for showing badges on non-Claude messages)
  provider?: InsightsProvider;
  providerModel?: string;
}

export interface InsightsSession {
  id: string;
  projectId: string;
  title?: string; // Auto-generated from first message or user-set
  messages: InsightsChatMessage[];
  modelConfig?: InsightsModelConfig; // Per-session model configuration
  createdAt: Date;
  updatedAt: Date;
}

// Summary of a session for the history list (without full messages)
export interface InsightsSessionSummary {
  id: string;
  projectId: string;
  title: string;
  messageCount: number;
  modelConfig?: InsightsModelConfig; // For displaying model indicator in sidebar
  createdAt: Date;
  updatedAt: Date;
}

export interface InsightsChatStatus {
  phase: 'idle' | 'thinking' | 'streaming' | 'complete' | 'error';
  message?: string;
  error?: string;
}

export interface InsightsStreamMetrics {
  inputTokens?: number;
  outputTokens: number;
  tokensPerSecond: number;
  elapsedSeconds: number;
  estimated: boolean;       // true = char-based estimate, false = exact (e.g. Ollama)
}

export interface InsightsStreamChunk {
  type: 'text' | 'task_suggestion' | 'tool_start' | 'tool_end' | 'done' | 'error';
  content?: string;
  suggestedTask?: {
    title: string;
    description: string;
    metadata?: TaskMetadata;
  };
  tool?: {
    name: string;
    input?: string;  // Brief description of what's being searched/read
  };
  error?: string;
  metrics?: InsightsStreamMetrics;
}
