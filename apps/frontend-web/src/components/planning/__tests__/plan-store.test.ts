/**
 * @vitest-environment jsdom
 *
 * Tests for the plan-store Zustand store.
 *
 * Verifies: loadSessions, selectSession, ingestTextPlan,
 * processCurrentSession, approveCurrentSession, rejectCurrentSession,
 * emitCurrentSession.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { usePlanStore } from '../../../stores/plan-store';
import type { PlanSession, SessionSummary } from '../../../shared/types/plan';

beforeEach(() => {
  localStorage.setItem('pfactory-token', 'test-token');
  usePlanStore.setState({
    sessions: [],
    currentSession: null,
    loading: false,
    sessionLoading: false,
    error: null,
    fetchFn: undefined,
  });
});

// ── Helper ────────────────────────────────────────────────────────────

function makeFetch(jsonBody: unknown, ok = true, status = 200): typeof fetch {
  return vi.fn().mockResolvedValue({
    ok,
    status,
    statusText: ok ? 'OK' : 'Error',
    json: async () => jsonBody,
    text: async () => '',
  }) as unknown as typeof fetch;
}

function makeSession(overrides: Partial<PlanSession> = {}): PlanSession {
  return {
    session_id: 's1',
    status: 'ingested',
    plan: {
      plan_id: 'p1',
      title: 'Default',
      description: '',
      source_format: 'text',
      source_channel: null,
      criteria: [],
      target_kind: 'software',
      plan_type: 'feature',
      enrichment: { infra: [], knowledge: [] },
      content_hash: 'hash',
      ingested_at: '2026-06-01T00:00:00Z',
    },
    epic: null,
    artifacts: [],
    review: null,
    emit_result: null,
    created_at: '2026-06-01T00:00:00Z',
    ...overrides,
  };
}

// ── loadSessions ──────────────────────────────────────────────────────

describe('loadSessions', () => {
  it('populates sessions on success', async () => {
    const summary: SessionSummary = {
      session_id: 's1',
      title: 'My plan',
      status: 'processed',
      target_kind: 'software',
      plan_type: 'feature',
      children: 3,
      gates_passed: true,
      created_at: '2026-06-01T00:00:00Z',
    };
    usePlanStore.setState({ fetchFn: makeFetch({ sessions: [summary] }) });
    await usePlanStore.getState().loadSessions();
    expect(usePlanStore.getState().sessions).toHaveLength(1);
    expect(usePlanStore.getState().sessions[0].session_id).toBe('s1');
  });

  it('sets error on fetch failure', async () => {
    usePlanStore.setState({ fetchFn: makeFetch({ detail: 'oops' }, false, 500) });
    await usePlanStore.getState().loadSessions();
    expect(usePlanStore.getState().error).toBe('oops');
    expect(usePlanStore.getState().sessions).toHaveLength(0);
  });

  it('clears error on subsequent successful load', async () => {
    usePlanStore.setState({
      error: 'previous error',
      fetchFn: makeFetch({ sessions: [] }),
    });
    await usePlanStore.getState().loadSessions();
    expect(usePlanStore.getState().error).toBeNull();
  });
});

// ── selectSession ─────────────────────────────────────────────────────

describe('selectSession', () => {
  it('sets currentSession on success', async () => {
    const session = makeSession({ session_id: 'sess-42', status: 'processed' });
    usePlanStore.setState({ fetchFn: makeFetch(session) });
    await usePlanStore.getState().selectSession('sess-42');
    expect(usePlanStore.getState().currentSession?.session_id).toBe('sess-42');
  });

  it('sets error on 404', async () => {
    usePlanStore.setState({ fetchFn: makeFetch({ detail: 'not found' }, false, 404) });
    await usePlanStore.getState().selectSession('missing');
    expect(usePlanStore.getState().error).toBe('not found');
    expect(usePlanStore.getState().currentSession).toBeNull();
  });
});

// ── ingestTextPlan ────────────────────────────────────────────────────

describe('ingestTextPlan', () => {
  it('sets currentSession and prepends to sessions list', async () => {
    const session = makeSession({ session_id: 'new-sess', status: 'ingested' });
    usePlanStore.setState({ fetchFn: makeFetch(session) });
    const result = await usePlanStore.getState().ingestTextPlan({ text: 'Hello' });
    expect(result.session_id).toBe('new-sess');
    expect(usePlanStore.getState().currentSession?.session_id).toBe('new-sess');
    expect(usePlanStore.getState().sessions[0].session_id).toBe('new-sess');
  });

  it('throws and sets error on API failure', async () => {
    usePlanStore.setState({ fetchFn: makeFetch({ detail: 'bad input' }, false, 422) });
    await expect(
      usePlanStore.getState().ingestTextPlan({ text: 'x' })
    ).rejects.toThrow('bad input');
    expect(usePlanStore.getState().error).toBe('bad input');
  });
});

// ── processCurrentSession ─────────────────────────────────────────────

describe('processCurrentSession', () => {
  it('updates currentSession and syncs sessions list status', async () => {
    const processed = makeSession({ session_id: 's1', status: 'processed' });
    usePlanStore.setState({
      currentSession: makeSession({ session_id: 's1', status: 'ingested' }),
      sessions: [{
        session_id: 's1',
        title: 'Test',
        status: 'ingested',
        target_kind: 'software',
        plan_type: 'feature',
        children: 0,
        gates_passed: null,
        created_at: '2026-06-01T00:00:00Z',
      }],
      fetchFn: makeFetch(processed),
    });
    await usePlanStore.getState().processCurrentSession();
    expect(usePlanStore.getState().currentSession?.status).toBe('processed');
    expect(usePlanStore.getState().sessions[0].status).toBe('processed');
  });

  it('does nothing if no currentSession', async () => {
    const fetchFn = makeFetch({});
    usePlanStore.setState({ currentSession: null, fetchFn });
    await usePlanStore.getState().processCurrentSession();
    expect(fetchFn).not.toHaveBeenCalled();
  });
});

// ── approveCurrentSession ─────────────────────────────────────────────

describe('approveCurrentSession', () => {
  it('updates status to approved', async () => {
    const approved = makeSession({ session_id: 's1', status: 'approved' });
    usePlanStore.setState({
      currentSession: makeSession({ session_id: 's1', status: 'processed' }),
      sessions: [{
        session_id: 's1', title: 'T', status: 'processed',
        target_kind: 'software', plan_type: 'feature',
        children: 0, gates_passed: true, created_at: '2026-06-01T00:00:00Z',
      }],
      fetchFn: makeFetch(approved),
    });
    await usePlanStore.getState().approveCurrentSession({ approver: 'alice' });
    expect(usePlanStore.getState().currentSession?.status).toBe('approved');
    expect(usePlanStore.getState().sessions[0].status).toBe('approved');
  });
});

// ── rejectCurrentSession ──────────────────────────────────────────────

describe('rejectCurrentSession', () => {
  it('updates status to rejected', async () => {
    const rejected = makeSession({ session_id: 's1', status: 'rejected' });
    usePlanStore.setState({
      currentSession: makeSession({ session_id: 's1', status: 'processed' }),
      sessions: [],
      fetchFn: makeFetch(rejected),
    });
    await usePlanStore.getState().rejectCurrentSession({ approver: 'bob', feedback: 'not ok' });
    expect(usePlanStore.getState().currentSession?.status).toBe('rejected');
  });
});

// ── emitCurrentSession ────────────────────────────────────────────────

describe('emitCurrentSession', () => {
  it('updates status to emitted and stores emit_result', async () => {
    const emitted = makeSession({
      session_id: 's1',
      status: 'emitted',
      emit_result: {
        dry_run: false,
        repo: 'github.com/org/repo',
        planned: { epic: 'EPIC-1', children: ['CHILD-1', 'CHILD-2'] },
        errors: [],
      },
    });
    usePlanStore.setState({
      currentSession: makeSession({ session_id: 's1', status: 'approved' }),
      sessions: [],
      fetchFn: makeFetch(emitted),
    });
    await usePlanStore.getState().emitCurrentSession({ repo: 'github.com/org/repo', dry_run: false });
    expect(usePlanStore.getState().currentSession?.status).toBe('emitted');
    expect(usePlanStore.getState().currentSession?.emit_result?.planned.children).toHaveLength(2);
  });
});

// ── clearCurrentSession ───────────────────────────────────────────────

describe('clearCurrentSession', () => {
  it('clears currentSession and error', () => {
    usePlanStore.setState({
      currentSession: makeSession(),
      error: 'some error',
    });
    usePlanStore.getState().clearCurrentSession();
    expect(usePlanStore.getState().currentSession).toBeNull();
    expect(usePlanStore.getState().error).toBeNull();
  });
});
