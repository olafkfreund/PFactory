/**
 * Typed API client for the PFactory portal backend endpoints
 * (Task 9 / #10).
 *
 * Endpoints covered:
 *   GET /api/pfactory/tasks                         — list
 *   GET /api/pfactory/tasks/{spec_id}                — detail
 *   GET /api/pfactory/tasks/{spec_id}/verdicts.json
 *   GET /api/pfactory/tasks/{spec_id}/triage-report.json
 *   GET /api/pfactory/tasks/{spec_id}/triage-report.md
 *   GET /api/pfactory/tasks/{spec_id}/test-plan.json
 *   GET /api/pfactory/tasks/{spec_id}/pr-comment-body.md
 *
 * Auth: uses ``getAuthHeaders`` from ./auth so the existing token
 * management flows in transparently. Tests inject a custom fetch
 * via the ``fetchFn`` option.
 */

import { getAuthHeaders } from './auth';

const API_BASE = (import.meta.env?.VITE_API_BASE_URL as string | undefined) ?? '/api';
const PFACTORY_PREFIX = `${API_BASE}/pfactory/tasks`;

// ─── Response shapes (mirror apps/web-server/server/routes/pfactory_tasks.py)

export interface PFactoryTaskSummary {
  task_id: string;
  project_id: string;
  spec_id: string;
  status: string | null;
  phase: string | null;
  updated_at: string;
}

export interface PFactoryTaskListResponse {
  tasks: PFactoryTaskSummary[];
  count: number;
}

export interface PFactoryArtefactMeta {
  path: string;
  exists: boolean;
}

export type PFactoryArtefactKey =
  | 'test_plan'
  | 'verdicts'
  | 'triage_report_json'
  | 'triage_report_md'
  | 'pr_comment_body';

export interface PFactoryTaskDetail {
  task_id: string;
  project_id: string;
  spec_id: string;
  status_json: Record<string, unknown>;
  artefacts: Record<PFactoryArtefactKey, PFactoryArtefactMeta>;
}

// ─── Evidence shapes ──────────────────────────────────────────────────

/** Map of artifact key → URL or list of URLs, as returned by the layout helper. */
export type EvidenceUrls = Record<string, string | string[]>;

// Subset of the verdicts.json schema the frontend actually reads. The
// full schema is the Evaluator's contract from Task 7 commit 5.
export interface PFactoryVerdict {
  test_id: string;
  test_file: string;
  verdict: 'accept' | 'reject' | 'flag';
  reasons: string[];
  signals_summary?: {
    coverage_delta_pct?: number;
    coverage_new_lines?: number;
    stability?: string;
    mutation?: string;
    lint_promotion?: string;
  };
  semantic_relevance?: 'high' | 'medium' | 'low';
  semantic_notes?: string;
  /** Evidence artifact URLs — populated by Task 16 evidence capture. */
  evidence_urls?: EvidenceUrls;
}

export interface PFactoryVerdictsDocument {
  evaluator_version: string;
  mode: string;
  generated_at: string;
  verdicts: PFactoryVerdict[];
}

// ─── Error shape ──────────────────────────────────────────────────────

export class PFactoryApiError extends Error {
  readonly status: number;
  readonly endpoint: string;
  constructor(status: number, endpoint: string, message: string) {
    super(message);
    this.name = 'PFactoryApiError';
    this.status = status;
    this.endpoint = endpoint;
  }
}

// ─── Internal helpers ─────────────────────────────────────────────────

type FetchLike = typeof fetch;

interface FetchOptions {
  signal?: AbortSignal;
  fetchFn?: FetchLike;
}

async function _request<T>(
  endpoint: string,
  parse: 'json' | 'text',
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
      // Try parsing the body as JSON to extract FastAPI's { detail }
      const data = (await response.json()) as unknown;
      if (data && typeof data === 'object' && 'detail' in data) {
        const d = (data as Record<string, unknown>).detail;
        if (typeof d === 'string') detail = d;
      }
    } catch {
      // Body wasn't JSON — fall through with empty detail
    }
    throw new PFactoryApiError(
      response.status,
      endpoint,
      detail || `${response.status} ${response.statusText}`,
    );
  }

  if (parse === 'json') {
    return (await response.json()) as T;
  }
  return (await response.text()) as unknown as T;
}

async function _post<T>(endpoint: string, body: unknown, options: FetchOptions = {}): Promise<T> {
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
    } catch { /* non-JSON body */ }
    throw new PFactoryApiError(response.status, endpoint, detail || `${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

// ─── Merge / dismiss (the human review gate) ──────────────────────────

export interface MergeRequest {
  dry_run?: boolean;
  target_branch?: string | null;
  repo_dir?: string | null;
  include_flagged?: boolean;
}

export interface MergeResult {
  ok: boolean;
  dry_run: boolean;
  branch: string;
  files: string[];
  committed_paths: string[];
  commit_sha: string;
  argv: string[][];
  error: string;
}

/** POST /{spec_id}/merge — commit accepted tests (dry-run by default). */
export async function mergeAcceptedTests(
  specId: string, body: MergeRequest = {}, options: FetchOptions = {},
): Promise<MergeResult> {
  _validateSpecId(specId);
  return _post<MergeResult>(`${PFACTORY_PREFIX}/${specId}/merge`, { dry_run: true, ...body }, options);
}

/** POST /{spec_id}/dismiss — mark the run dismissed. */
export async function dismissRun(
  specId: string, options: FetchOptions = {},
): Promise<{ ok: boolean; dismissed: boolean; dismissed_at: string }> {
  _validateSpecId(specId);
  return _post(`${PFACTORY_PREFIX}/${specId}/dismiss`, {}, options);
}

// ─── Spec-id validation (mirrors backend) ─────────────────────────────

const _SPEC_ID_RE = /^[A-Za-z0-9._-]+$/;

function _validateSpecId(specId: string): void {
  if (!specId || !_SPEC_ID_RE.test(specId)) {
    throw new PFactoryApiError(
      400, `(client validation: ${specId})`,
      `invalid spec_id: ${specId}`,
    );
  }
}

// ─── Public endpoints ─────────────────────────────────────────────────

/**
 * GET /api/pfactory/tasks — list every PFactory workspace.
 *
 * Sorted by ``updated_at`` descending (newest first). Empty if the
 * workspace root doesn't exist yet.
 */
export function listTasks(
  options: FetchOptions = {},
): Promise<PFactoryTaskListResponse> {
  return _request<PFactoryTaskListResponse>(PFACTORY_PREFIX, 'json', options);
}

/**
 * GET /api/pfactory/tasks/{spec_id} — full status + artefact meta.
 *
 * Throws PFactoryApiError(400) on malformed spec_id (validated
 * client-side too — no network call when it's malformed),
 * PFactoryApiError(404) if no matching spec.
 */
export async function getTaskDetail(
  specId: string,
  options: FetchOptions = {},
): Promise<PFactoryTaskDetail> {
  _validateSpecId(specId);
  return _request<PFactoryTaskDetail>(
    `${PFACTORY_PREFIX}/${specId}`, 'json', options,
  );
}

/**
 * GET /api/pfactory/tasks/{spec_id}/verdicts.json — Evaluator output.
 *
 * The response is a typed VerdictsDocument. 404 when the spec hasn't
 * reached the Evaluator stage yet (the frontend uses this to grey
 * out the verdict tab).
 */
export async function getVerdicts(
  specId: string,
  options: FetchOptions = {},
): Promise<PFactoryVerdictsDocument> {
  _validateSpecId(specId);
  return _request<PFactoryVerdictsDocument>(
    `${PFACTORY_PREFIX}/${specId}/verdicts.json`, 'json', options,
  );
}

/**
 * GET /api/pfactory/tasks/{spec_id}/triage-report.json — Triager output.
 */
export async function getTriageReportJson(
  specId: string,
  options: FetchOptions = {},
): Promise<Record<string, unknown>> {
  _validateSpecId(specId);
  return _request<Record<string, unknown>>(
    `${PFACTORY_PREFIX}/${specId}/triage-report.json`, 'json', options,
  );
}

/**
 * GET /api/pfactory/tasks/{spec_id}/triage-report.md — Triager MD.
 *
 * Returned as a string so a Markdown viewer component can render it.
 */
export async function getTriageReportMarkdown(
  specId: string,
  options: FetchOptions = {},
): Promise<string> {
  _validateSpecId(specId);
  return _request<string>(
    `${PFACTORY_PREFIX}/${specId}/triage-report.md`, 'text', options,
  );
}

/**
 * GET /api/pfactory/tasks/{spec_id}/test-plan.json — Planner output.
 */
export async function getTestPlan(
  specId: string,
  options: FetchOptions = {},
): Promise<Record<string, unknown>> {
  _validateSpecId(specId);
  return _request<Record<string, unknown>>(
    `${PFACTORY_PREFIX}/${specId}/test-plan.json`, 'json', options,
  );
}

/**
 * GET /api/pfactory/tasks/{spec_id}/pr-comment-body.md — fallback PR
 * comment body for when the Triager skipped a real gh pr comment
 * (no PR number in source.json).
 */
export async function getPrCommentBody(
  specId: string,
  options: FetchOptions = {},
): Promise<string> {
  _validateSpecId(specId);
  return _request<string>(
    `${PFACTORY_PREFIX}/${specId}/pr-comment-body.md`, 'text', options,
  );
}

/**
 * Build a portal URL for an evidence artifact.
 *
 * Does NOT make a network request — returns the URL string so the caller
 * can embed it in an ``<img src=...>``, ``<video src=...>``, or anchor tag.
 *
 * The artifact path may include a subdirectory prefix, e.g.
 * ``"screenshots/0001.png"``, ``"video.webm"``, ``"trace.zip"``,
 * ``"network.har"``.
 */
export function evidenceArtifactUrl(
  specId: string,
  testId: string,
  artifact: string,
): string {
  return `${PFACTORY_PREFIX}/${specId}/evidence/${testId}/${artifact}`;
}

// ─── Internal exports for tests ───────────────────────────────────────

export const _internalForTests = {
  PFACTORY_PREFIX,
  _validateSpecId,
  evidenceArtifactUrl,
};
