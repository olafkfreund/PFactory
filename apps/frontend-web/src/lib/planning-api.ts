/**
 * Typed API client for the PFactory Planning Portal endpoints.
 *
 * Endpoints:
 *   GET  /api/plan/sessions                      — list sessions
 *   POST /api/plan/sessions/ingest-text          — ingest text
 *   POST /api/plan/sessions/ingest               — ingest file (multipart)
 *   GET  /api/plan/sessions/{id}                 — get session
 *   POST /api/plan/sessions/{id}/process         — process session
 *   POST /api/plan/sessions/{id}/approve         — approve session
 *   POST /api/plan/sessions/{id}/reject          — reject session
 *   POST /api/plan/sessions/{id}/emit            — emit session
 *   GET  /api/plan/meta/registry                 — registry entries
 *   GET  /api/plan/meta/templates                — templates
 *   GET  /api/plan/meta/providers                — providers
 *   GET  /api/plan/meta/adapters                 — adapters
 *
 * Auth: uses ``getAuthHeaders`` from ./auth so the existing token
 * management flows in transparently. Tests inject a custom fetch
 * via the ``fetchFn`` option.
 */

import { getAuthHeaders } from './auth';
import type {
  PlanSession,
  SessionSummary,
  RegistryEntry,
  TemplateEntry,
  CategoryEntry,
} from '../shared/types/plan';

const API_BASE = (import.meta.env?.VITE_API_BASE_URL as string | undefined) ?? '/api';
export const PLAN_PREFIX = `${API_BASE}/plan`;

// ── Error class ───────────────────────────────────────────────────────

export class PlanApiError extends Error {
  readonly status: number;
  readonly endpoint: string;
  constructor(status: number, endpoint: string, message: string) {
    super(message);
    this.name = 'PlanApiError';
    this.status = status;
    this.endpoint = endpoint;
  }
}

// ── Internal helpers ──────────────────────────────────────────────────

type FetchLike = typeof fetch;

export interface FetchOptions {
  signal?: AbortSignal;
  fetchFn?: FetchLike;
}

async function _request<T>(
  endpoint: string,
  options: FetchOptions = {},
): Promise<T> {
  const { signal, fetchFn = fetch } = options;
  const response = await fetchFn(endpoint, {
    method: 'GET',
    headers: { ...getAuthHeaders() },
    signal,
  });

  if (!response.ok) {
    let detail = '';
    try {
      const data = (await response.json()) as unknown;
      if (data && typeof data === 'object' && 'detail' in data) {
        const d = (data as Record<string, unknown>).detail;
        if (typeof d === 'string') detail = d;
      }
    } catch {
      // Non-JSON body
    }
    throw new PlanApiError(
      response.status,
      endpoint,
      detail || `${response.status} ${response.statusText}`,
    );
  }

  return (await response.json()) as T;
}

async function _post<T>(
  endpoint: string,
  body: unknown,
  options: FetchOptions = {},
): Promise<T> {
  const { signal, fetchFn = fetch } = options;
  const response = await fetchFn(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    let detail = '';
    try {
      const data = (await response.json()) as unknown;
      if (data && typeof data === 'object' && 'detail' in data) {
        const d = (data as Record<string, unknown>).detail;
        if (typeof d === 'string') detail = d;
      }
    } catch {
      // Non-JSON body
    }
    throw new PlanApiError(
      response.status,
      endpoint,
      detail || `${response.status} ${response.statusText}`,
    );
  }

  return (await response.json()) as T;
}

async function _postMultipart<T>(
  endpoint: string,
  formData: FormData,
  options: FetchOptions = {},
): Promise<T> {
  const { signal, fetchFn = fetch } = options;
  // Do NOT set Content-Type — the browser must set it with the boundary
  const response = await fetchFn(endpoint, {
    method: 'POST',
    headers: { ...getAuthHeaders() },
    body: formData,
    signal,
  });

  if (!response.ok) {
    let detail = '';
    try {
      const data = (await response.json()) as unknown;
      if (data && typeof data === 'object' && 'detail' in data) {
        const d = (data as Record<string, unknown>).detail;
        if (typeof d === 'string') detail = d;
      }
    } catch {
      // Non-JSON body
    }
    throw new PlanApiError(
      response.status,
      endpoint,
      detail || `${response.status} ${response.statusText}`,
    );
  }

  return (await response.json()) as T;
}

// ── Sessions ──────────────────────────────────────────────────────────

export interface SessionListResponse {
  sessions: SessionSummary[];
}

/** GET /api/plan/sessions — list all sessions. */
export function listSessions(
  options: FetchOptions = {},
): Promise<SessionListResponse> {
  return _request<SessionListResponse>(`${PLAN_PREFIX}/sessions`, options);
}

export interface IngestTextRequest {
  text: string;
  title?: string;
  channel?: string;
  category?: string;
  template?: string;
}

/** POST /api/plan/sessions/ingest-text — ingest plain text. */
export function ingestText(
  body: IngestTextRequest,
  options: FetchOptions = {},
): Promise<PlanSession> {
  return _post<PlanSession>(`${PLAN_PREFIX}/sessions/ingest-text`, body, options);
}

export interface IngestFileOptions extends FetchOptions {
  title?: string;
  category?: string;
  template?: string;
}

/** POST /api/plan/sessions/ingest — ingest file (multipart). */
export function ingestFile(
  file: File,
  options: IngestFileOptions = {},
): Promise<PlanSession> {
  const { title, category, template, ...fetchOptions } = options;
  const form = new FormData();
  form.append('file', file);
  if (title) form.append('title', title);
  if (category) form.append('category', category);
  if (template) form.append('template', template);
  return _postMultipart<PlanSession>(`${PLAN_PREFIX}/sessions/ingest`, form, fetchOptions);
}

/** GET /api/plan/sessions/{id} — get a single session. */
export function getSession(
  sessionId: string,
  options: FetchOptions = {},
): Promise<PlanSession> {
  return _request<PlanSession>(`${PLAN_PREFIX}/sessions/${sessionId}`, options);
}

/** POST /api/plan/sessions/{id}/process — run the pipeline. */
export function processSession(
  sessionId: string,
  options: FetchOptions = {},
): Promise<PlanSession> {
  return _post<PlanSession>(`${PLAN_PREFIX}/sessions/${sessionId}/process`, {}, options);
}

export interface ApproveRequest {
  approver: string;
  feedback?: string;
}

/** POST /api/plan/sessions/{id}/approve — approve the session. */
export function approveSession(
  sessionId: string,
  body: ApproveRequest,
  options: FetchOptions = {},
): Promise<PlanSession> {
  return _post<PlanSession>(`${PLAN_PREFIX}/sessions/${sessionId}/approve`, body, options);
}

export interface RejectRequest {
  approver: string;
  feedback: string;
}

/** POST /api/plan/sessions/{id}/reject — reject the session. */
export function rejectSession(
  sessionId: string,
  body: RejectRequest,
  options: FetchOptions = {},
): Promise<PlanSession> {
  return _post<PlanSession>(`${PLAN_PREFIX}/sessions/${sessionId}/reject`, body, options);
}

export interface EmitRequest {
  repo: string;
  dry_run?: boolean;
}

/** POST /api/plan/sessions/{id}/emit — emit the epic to a repo. */
export function emitSession(
  sessionId: string,
  body: EmitRequest,
  options: FetchOptions = {},
): Promise<PlanSession> {
  return _post<PlanSession>(`${PLAN_PREFIX}/sessions/${sessionId}/emit`, body, options);
}

export interface WaiveChecksRequest {
  check_ids: string[];
  reason: string;
  waived_by: string;
}

/** POST /api/plan/sessions/{id}/waive — waive one or more hard readiness checks. */
export function waiveChecks(
  sessionId: string,
  body: WaiveChecksRequest,
  options: FetchOptions = {},
): Promise<PlanSession> {
  return _post<PlanSession>(`${PLAN_PREFIX}/sessions/${sessionId}/waive`, body, options);
}

/** POST /api/plan/sessions/{id}/process — update plan fields then re-process. */
export interface UpdateAndProcessRequest {
  title?: string;
  description?: string;
  criteria?: Array<{ id: string; text: string }>;
}

export function updateAndProcess(
  sessionId: string,
  updates: UpdateAndProcessRequest,
  options: FetchOptions = {},
): Promise<PlanSession> {
  // The backend processes immediately; we POST the updated fields then re-run.
  // Send as a custom body that the backend can merge. Since the backend exposes
  // only /process (no PATCH), we chain two calls: a hypothetical PATCH that
  // updates the plan, then a /process. For the MVP we expose this as a single
  // helper that calls /process with the update fields embedded.
  return _post<PlanSession>(
    `${PLAN_PREFIX}/sessions/${sessionId}/process`,
    updates,
    options,
  );
}

/** POST /api/plan/sessions/{id}/suggestions/apply — accept suggestions (#701). */
export interface ApplySuggestionsRequest {
  accepted: Array<{ id: string; replacement?: string }>;
  reprocess?: boolean;
}

export function applySuggestions(
  sessionId: string,
  body: ApplySuggestionsRequest,
  options: FetchOptions = {},
): Promise<PlanSession> {
  return _post<PlanSession>(
    `${PLAN_PREFIX}/sessions/${sessionId}/suggestions/apply`,
    body,
    options,
  );
}

// ── Meta ──────────────────────────────────────────────────────────────

export interface RegistryResponse {
  entries: RegistryEntry[];
}

/** GET /api/plan/meta/registry — list registry entries. */
export function getRegistry(
  options: FetchOptions = {},
): Promise<RegistryResponse> {
  return _request<RegistryResponse>(`${PLAN_PREFIX}/meta/registry`, options);
}

export interface TemplatesResponse {
  templates: TemplateEntry[];
}

/** GET /api/plan/meta/templates — list templates. */
export function getTemplates(
  options: FetchOptions = {},
): Promise<TemplatesResponse> {
  return _request<TemplatesResponse>(`${PLAN_PREFIX}/meta/templates`, options);
}

export interface CategoriesResponse {
  categories: CategoryEntry[];
}

/** GET /api/plan/meta/categories — list intake categories (+ plan-types/templates). */
export function getCategories(
  options: FetchOptions = {},
): Promise<CategoriesResponse> {
  return _request<CategoriesResponse>(`${PLAN_PREFIX}/meta/categories`, options);
}

export interface ProvidersResponse {
  providers: string[];
}

/** GET /api/plan/meta/providers — list providers. */
export function getProviders(
  options: FetchOptions = {},
): Promise<ProvidersResponse> {
  return _request<ProvidersResponse>(`${PLAN_PREFIX}/meta/providers`, options);
}

export interface AdaptersResponse {
  infra_adapters: string[];
  knowledge_connectors: string[];
}

/** GET /api/plan/meta/adapters — list adapters. */
export function getAdapters(
  options: FetchOptions = {},
): Promise<AdaptersResponse> {
  return _request<AdaptersResponse>(`${PLAN_PREFIX}/meta/adapters`, options);
}

// ── Internal exports for tests ────────────────────────────────────────

export const _internalForTests = {
  PLAN_PREFIX,
};
