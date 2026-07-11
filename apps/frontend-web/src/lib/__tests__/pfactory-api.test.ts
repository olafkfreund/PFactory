/**
 * Tests for the PFactory portal API client — Task 10 (#11) commit 1.
 *
 * The client wraps fetch() and adds auth headers + typed parsing. We
 * inject a fake fetch via the ``fetchFn`` option so the tests verify
 * URL construction, content-type handling, and error paths without
 * hitting any real backend.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import {
  PFactoryApiError,
  _internalForTests,
  getPrCommentBody,
  getTaskDetail,
  getTestPlan,
  getTriageReportJson,
  getTriageReportMarkdown,
  getVerdicts,
  listTasks,
} from '../pfactory-api';

// Stub auth — getAuthHeaders is called by the client; localStorage is
// JSDOM-backed in vitest.
beforeEach(() => {
  localStorage.setItem('pfactory-token', 'test-token-abc');
});

// ─── Helper: fake fetch that returns the given response shape ───────

function makeFetch(opts: {
  ok?: boolean;
  status?: number;
  statusText?: string;
  jsonBody?: unknown;
  textBody?: string;
}): typeof fetch {
  const {
    ok = true, status = 200, statusText = 'OK',
    jsonBody, textBody,
  } = opts;
  return vi.fn().mockImplementation(async () => {
    return {
      ok,
      status,
      statusText,
      json: async () => {
        if (jsonBody !== undefined) return jsonBody;
        throw new Error('no json body');
      },
      text: async () => textBody ?? '',
    };
  }) as unknown as typeof fetch;
}

// ─── URL construction ─────────────────────────────────────────────────

describe('URL prefix', () => {
  it('uses /api/pfactory/tasks by default', () => {
    expect(_internalForTests.PFACTORY_PREFIX).toBe('/api/pfactory/tasks');
  });
});

// ─── listTasks ────────────────────────────────────────────────────────

describe('listTasks', () => {
  it('GETs the prefix and returns the parsed JSON', async () => {
    const fetchFn = makeFetch({
      jsonBody: { tasks: [], count: 0 },
    });
    const result = await listTasks({ fetchFn });
    expect(result).toEqual({ tasks: [], count: 0 });
    expect(fetchFn).toHaveBeenCalledWith(
      '/api/pfactory/tasks',
      expect.objectContaining({ method: 'GET' }),
    );
  });

  it('passes through Authorization header', async () => {
    const fetchFn = makeFetch({ jsonBody: { tasks: [], count: 0 } });
    await listTasks({ fetchFn });
    const callArgs = (fetchFn as ReturnType<typeof vi.fn>).mock.calls[0];
    const headers = callArgs[1].headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer test-token-abc');
  });

  it('parses a populated list response', async () => {
    const payload = {
      tasks: [
        {
          task_id: '042-x', project_id: 'demo', spec_id: '042-x',
          status: 'triaged', phase: 'triager_complete',
          updated_at: '2026-05-28T10:00:00+00:00',
        },
      ],
      count: 1,
    };
    const fetchFn = makeFetch({ jsonBody: payload });
    const result = await listTasks({ fetchFn });
    expect(result.count).toBe(1);
    expect(result.tasks[0].spec_id).toBe('042-x');
  });

  it('throws PFactoryApiError on non-2xx response', async () => {
    const fetchFn = makeFetch({
      ok: false, status: 500, statusText: 'Internal Server Error',
      jsonBody: { detail: 'database down' },
    });
    await expect(listTasks({ fetchFn })).rejects.toMatchObject({
      name: 'PFactoryApiError',
      status: 500,
      message: 'database down',
    });
  });
});

// ─── getTaskDetail ────────────────────────────────────────────────────

describe('getTaskDetail', () => {
  it('GETs /{spec_id}', async () => {
    const fetchFn = makeFetch({
      jsonBody: {
        task_id: '042-x', project_id: 'demo', spec_id: '042-x',
        status_json: {}, artefacts: {},
      },
    });
    await getTaskDetail('042-x', { fetchFn });
    expect(fetchFn).toHaveBeenCalledWith(
      '/api/pfactory/tasks/042-x',
      expect.any(Object),
    );
  });

  it('rejects malformed spec_id client-side without calling fetch', async () => {
    const fetchFn = makeFetch({ jsonBody: {} });
    await expect(
      getTaskDetail('../../etc/passwd', { fetchFn }),
    ).rejects.toMatchObject({
      name: 'PFactoryApiError',
      status: 400,
    });
    // fetch was NOT called
    expect((fetchFn as ReturnType<typeof vi.fn>).mock.calls.length).toBe(0);
  });

  it('passes through 404 from server', async () => {
    const fetchFn = makeFetch({
      ok: false, status: 404, statusText: 'Not Found',
      jsonBody: { detail: 'task not found: nonexistent' },
    });
    await expect(
      getTaskDetail('nonexistent', { fetchFn }),
    ).rejects.toMatchObject({ status: 404 });
  });
});

// ─── Artefact endpoints (JSON) ────────────────────────────────────────

describe('getVerdicts', () => {
  it('GETs /{spec_id}/verdicts.json and returns parsed JSON', async () => {
    const verdicts = {
      evaluator_version: 'task7-commit5',
      mode: 'initial',
      generated_at: '2026-05-28T00:00:00+00:00',
      verdicts: [
        {
          test_id: 'st0',
          test_file: 'tests/test_0.py',
          verdict: 'accept',
          reasons: ['ok'],
        },
      ],
    };
    const fetchFn = makeFetch({ jsonBody: verdicts });
    const result = await getVerdicts('042-x', { fetchFn });
    expect(result.verdicts[0].test_id).toBe('st0');
    expect(fetchFn).toHaveBeenCalledWith(
      '/api/pfactory/tasks/042-x/verdicts.json',
      expect.any(Object),
    );
  });
});

describe('getTriageReportJson', () => {
  it('GETs the triage-report.json path', async () => {
    const fetchFn = makeFetch({ jsonBody: { triager_version: 'x' } });
    await getTriageReportJson('042-x', { fetchFn });
    expect(fetchFn).toHaveBeenCalledWith(
      '/api/pfactory/tasks/042-x/triage-report.json',
      expect.any(Object),
    );
  });
});

describe('getTestPlan', () => {
  it('GETs the test-plan.json path', async () => {
    const fetchFn = makeFetch({ jsonBody: { feature: 'x', phases: [] } });
    await getTestPlan('042-x', { fetchFn });
    expect(fetchFn).toHaveBeenCalledWith(
      '/api/pfactory/tasks/042-x/test-plan.json',
      expect.any(Object),
    );
  });
});

// ─── Artefact endpoints (Markdown text) ───────────────────────────────

describe('getTriageReportMarkdown', () => {
  it('GETs the triage-report.md path and returns text', async () => {
    const md = '# Triage Report\n\nAll good.\n';
    const fetchFn = makeFetch({ textBody: md });
    const result = await getTriageReportMarkdown('042-x', { fetchFn });
    expect(result).toBe(md);
    expect(fetchFn).toHaveBeenCalledWith(
      '/api/pfactory/tasks/042-x/triage-report.md',
      expect.any(Object),
    );
  });
});

describe('getPrCommentBody', () => {
  it('GETs the pr-comment-body.md path and returns text', async () => {
    const md = '# PR Comment\n';
    const fetchFn = makeFetch({ textBody: md });
    const result = await getPrCommentBody('042-x', { fetchFn });
    expect(result).toBe(md);
    expect(fetchFn).toHaveBeenCalledWith(
      '/api/pfactory/tasks/042-x/pr-comment-body.md',
      expect.any(Object),
    );
  });

  it('passes through 404 when the body file is missing', async () => {
    const fetchFn = makeFetch({
      ok: false, status: 404, statusText: 'Not Found',
      jsonBody: { detail: 'artefact not found: findings/pr_comment_body.md' },
    });
    await expect(
      getPrCommentBody('042-x', { fetchFn }),
    ).rejects.toMatchObject({ status: 404 });
  });
});

// ─── Cross-cutting: all artefact getters reject bad spec_id ──────────

describe.each([
  ['getVerdicts', getVerdicts],
  ['getTriageReportJson', getTriageReportJson],
  ['getTriageReportMarkdown', getTriageReportMarkdown],
  ['getTestPlan', getTestPlan],
  ['getPrCommentBody', getPrCommentBody],
] as const)('%s rejects bad spec_id client-side', (_name, fn) => {
  it('throws 400 without calling fetch', async () => {
    const fetchFn = makeFetch({ jsonBody: {} });
    await expect(
      (fn as (specId: string, opts: { fetchFn: unknown }) => Promise<unknown>)('../escape', {
        fetchFn,
      }),
    ).rejects.toMatchObject({
      status: 400,
    });
    expect((fetchFn as ReturnType<typeof vi.fn>).mock.calls.length).toBe(0);
  });
});

// ─── Error fallback when body isn't JSON ─────────────────────────────

describe('error handling', () => {
  it('falls back to status text when error body is not JSON', async () => {
    const fetchFn = vi.fn().mockImplementation(async () => ({
      ok: false,
      status: 502,
      statusText: 'Bad Gateway',
      json: async () => { throw new Error('not json'); },
      text: async () => 'plain text body',
    })) as unknown as typeof fetch;

    await expect(listTasks({ fetchFn })).rejects.toMatchObject({
      status: 502,
      message: '502 Bad Gateway',
    });
  });

  it('PFactoryApiError exposes endpoint + status', async () => {
    const fetchFn = makeFetch({
      ok: false, status: 404, statusText: 'NF',
      jsonBody: { detail: 'gone' },
    });
    try {
      await getTaskDetail('exists', { fetchFn });
      throw new Error('expected throw');
    } catch (e) {
      expect(e).toBeInstanceOf(PFactoryApiError);
      const err = e as PFactoryApiError;
      expect(err.status).toBe(404);
      expect(err.endpoint).toBe('/api/pfactory/tasks/exists');
    }
  });
});

// ─── Validator unit ───────────────────────────────────────────────────

describe('_validateSpecId', () => {
  const { _validateSpecId } = _internalForTests;

  it.each(['042-x', 'simple', 'a.b_c-1', 'A1B2'])(
    'accepts valid spec_id %s',
    (id) => {
      expect(() => _validateSpecId(id)).not.toThrow();
    },
  );

  it.each(['', '../x', 'a/b', 'has space', 'has\nnewline'])(
    'rejects invalid spec_id %s',
    (id) => {
      expect(() => _validateSpecId(id)).toThrow(PFactoryApiError);
    },
  );
});
