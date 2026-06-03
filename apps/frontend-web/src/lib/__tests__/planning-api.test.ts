/**
 * @vitest-environment jsdom
 *
 * Tests for the Planning Portal API client (planning-api.ts).
 *
 * Covers every endpoint: listSessions, ingestText, ingestFile,
 * getSession, processSession, approveSession (+ 409), rejectSession,
 * emitSession, getRegistry, getTemplates, getProviders, getAdapters.
 *
 * Uses the fetchFn injection pattern — no MSW or fetch-mock.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  PlanApiError,
  _internalForTests,
  listSessions,
  ingestText,
  ingestFile,
  getSession,
  processSession,
  approveSession,
  rejectSession,
  emitSession,
  getRegistry,
  getTemplates,
  getProviders,
  getAdapters,
} from '../planning-api';

// Stub auth — getAuthHeaders reads localStorage (JSDOM-backed in vitest).
beforeEach(() => {
  localStorage.setItem('pfactory-token', 'test-token-plan');
});

// ── Helper: fake fetch ───────────────────────────────────────────────

function makeFetch(opts: {
  ok?: boolean;
  status?: number;
  statusText?: string;
  jsonBody?: unknown;
}): typeof fetch {
  const { ok = true, status = 200, statusText = 'OK', jsonBody } = opts;
  return vi.fn().mockImplementation(async () => ({
    ok,
    status,
    statusText,
    json: async () => {
      if (jsonBody !== undefined) return jsonBody;
      throw new Error('no json body');
    },
    text: async () => '',
  })) as unknown as typeof fetch;
}

// ── URL prefix ────────────────────────────────────────────────────────

describe('PLAN_PREFIX', () => {
  it('uses /api/plan by default', () => {
    expect(_internalForTests.PLAN_PREFIX).toBe('/api/plan');
  });
});

// ── listSessions ──────────────────────────────────────────────────────

describe('listSessions', () => {
  it('GETs /api/plan/sessions and returns the sessions array', async () => {
    const fetchFn = makeFetch({ jsonBody: { sessions: [] } });
    const result = await listSessions({ fetchFn });
    expect(result).toEqual({ sessions: [] });
    expect(fetchFn).toHaveBeenCalledWith(
      '/api/plan/sessions',
      expect.objectContaining({ method: 'GET' }),
    );
  });

  it('passes Authorization header', async () => {
    const fetchFn = makeFetch({ jsonBody: { sessions: [] } });
    await listSessions({ fetchFn });
    const [, init] = (fetchFn as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)['Authorization']).toContain('Bearer');
  });

  it('throws PlanApiError on non-2xx', async () => {
    const fetchFn = makeFetch({ ok: false, status: 500, statusText: 'Internal Server Error', jsonBody: { detail: 'boom' } });
    await expect(listSessions({ fetchFn })).rejects.toBeInstanceOf(PlanApiError);
  });

  it('includes the detail from JSON body in the error message', async () => {
    const fetchFn = makeFetch({ ok: false, status: 503, statusText: 'Service Unavailable', jsonBody: { detail: 'db gone' } });
    const err = await listSessions({ fetchFn }).catch((e) => e) as PlanApiError;
    expect(err.message).toBe('db gone');
    expect(err.status).toBe(503);
  });
});

// ── ingestText ────────────────────────────────────────────────────────

describe('ingestText', () => {
  it('POSTs to /api/plan/sessions/ingest-text with body', async () => {
    const body = { title: 'My plan', text: 'Plan content here', channel: 'web' };
    const mockSession = { session_id: 's1', status: 'ingested' };
    const fetchFn = makeFetch({ jsonBody: mockSession });
    const result = await ingestText(body, { fetchFn });
    expect(result).toEqual(mockSession);
    const [url, init] = (fetchFn as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/plan/sessions/ingest-text');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual(body);
  });
});

// ── ingestFile ────────────────────────────────────────────────────────

describe('ingestFile', () => {
  it('POSTs multipart to /api/plan/sessions/ingest', async () => {
    const file = new File(['content'], 'test.md', { type: 'text/markdown' });
    const mockSession = { session_id: 's2', status: 'ingested' };
    const fetchFn = makeFetch({ jsonBody: mockSession });
    const result = await ingestFile(file, { fetchFn });
    expect(result).toEqual(mockSession);
    const [url, init] = (fetchFn as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/plan/sessions/ingest');
    expect(init.method).toBe('POST');
    expect(init.body).toBeInstanceOf(FormData);
    // Should NOT set Content-Type manually (let browser set boundary)
    const headers = init.headers as Record<string, string>;
    expect(headers['Content-Type']).toBeUndefined();
  });
});

// ── getSession ────────────────────────────────────────────────────────

describe('getSession', () => {
  it('GETs /api/plan/sessions/{id}', async () => {
    const mockSession = { session_id: 'abc123', status: 'processed' };
    const fetchFn = makeFetch({ jsonBody: mockSession });
    const result = await getSession('abc123', { fetchFn });
    expect(result).toEqual(mockSession);
    const [url] = (fetchFn as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toBe('/api/plan/sessions/abc123');
  });
});

// ── processSession ────────────────────────────────────────────────────

describe('processSession', () => {
  it('POSTs to /api/plan/sessions/{id}/process', async () => {
    const mockSession = { session_id: 'abc123', status: 'processed' };
    const fetchFn = makeFetch({ jsonBody: mockSession });
    await processSession('abc123', { fetchFn });
    const [url, init] = (fetchFn as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/plan/sessions/abc123/process');
    expect(init.method).toBe('POST');
  });
});

// ── approveSession ────────────────────────────────────────────────────

describe('approveSession', () => {
  it('POSTs to /api/plan/sessions/{id}/approve with body', async () => {
    const mockSession = { session_id: 's1', status: 'approved' };
    const fetchFn = makeFetch({ jsonBody: mockSession });
    await approveSession('s1', { approver: 'alice' }, { fetchFn });
    const [url, init] = (fetchFn as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/plan/sessions/s1/approve');
    expect(JSON.parse(init.body as string)).toEqual({ approver: 'alice' });
  });

  it('throws PlanApiError with status 409 when gates not passed', async () => {
    const fetchFn = makeFetch({
      ok: false,
      status: 409,
      statusText: 'Conflict',
      jsonBody: { detail: 'gates not passed' },
    });
    const err = await approveSession('s1', { approver: 'bob' }, { fetchFn }).catch((e) => e) as PlanApiError;
    expect(err).toBeInstanceOf(PlanApiError);
    expect(err.status).toBe(409);
    expect(err.message).toBe('gates not passed');
  });
});

// ── rejectSession ─────────────────────────────────────────────────────

describe('rejectSession', () => {
  it('POSTs to /api/plan/sessions/{id}/reject with body', async () => {
    const mockSession = { session_id: 's1', status: 'rejected' };
    const fetchFn = makeFetch({ jsonBody: mockSession });
    await rejectSession('s1', { approver: 'alice', feedback: 'not ready' }, { fetchFn });
    const [url, init] = (fetchFn as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/plan/sessions/s1/reject');
    expect(JSON.parse(init.body as string)).toEqual({ approver: 'alice', feedback: 'not ready' });
  });
});

// ── emitSession ───────────────────────────────────────────────────────

describe('emitSession', () => {
  it('POSTs to /api/plan/sessions/{id}/emit with repo + dry_run', async () => {
    const mockSession = { session_id: 's1', status: 'emitted' };
    const fetchFn = makeFetch({ jsonBody: mockSession });
    await emitSession('s1', { repo: 'github.com/org/repo', dry_run: true }, { fetchFn });
    const [url, init] = (fetchFn as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/plan/sessions/s1/emit');
    expect(JSON.parse(init.body as string)).toEqual({ repo: 'github.com/org/repo', dry_run: true });
  });
});

// ── getRegistry ───────────────────────────────────────────────────────

describe('getRegistry', () => {
  it('GETs /api/plan/meta/registry', async () => {
    const fetchFn = makeFetch({ jsonBody: { entries: [] } });
    const result = await getRegistry({ fetchFn });
    expect(result).toEqual({ entries: [] });
    const [url] = (fetchFn as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toBe('/api/plan/meta/registry');
  });
});

// ── getTemplates ──────────────────────────────────────────────────────

describe('getTemplates', () => {
  it('GETs /api/plan/meta/templates', async () => {
    const fetchFn = makeFetch({ jsonBody: { templates: [] } });
    const result = await getTemplates({ fetchFn });
    expect(result).toEqual({ templates: [] });
    const [url] = (fetchFn as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toBe('/api/plan/meta/templates');
  });
});

// ── getProviders ──────────────────────────────────────────────────────

describe('getProviders', () => {
  it('GETs /api/plan/meta/providers', async () => {
    const fetchFn = makeFetch({ jsonBody: { providers: ['openai', 'anthropic'] } });
    const result = await getProviders({ fetchFn });
    expect(result.providers).toEqual(['openai', 'anthropic']);
    const [url] = (fetchFn as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toBe('/api/plan/meta/providers');
  });
});

// ── getAdapters ───────────────────────────────────────────────────────

describe('getAdapters', () => {
  it('GETs /api/plan/meta/adapters', async () => {
    const fetchFn = makeFetch({
      jsonBody: { infra_adapters: ['github'], knowledge_connectors: ['confluence'] },
    });
    const result = await getAdapters({ fetchFn });
    expect(result.infra_adapters).toEqual(['github']);
    expect(result.knowledge_connectors).toEqual(['confluence']);
    const [url] = (fetchFn as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toBe('/api/plan/meta/adapters');
  });
});
