/**
 * Zustand store for the Planning Portal.
 *
 * Manages:
 *  - sessions list (SessionSummary[])
 *  - currently selected/active session (PlanSession | null)
 *  - loading and error state
 *  - actions that call planning-api.ts
 *
 * Injectable fetchFn threaded through all API calls for testability.
 */

import { create } from 'zustand';
import type { PlanSession, SessionSummary } from '../shared/types/plan';
import {
  listSessions,
  getSession,
  ingestText,
  ingestFile,
  processSession,
  updateAndProcess,
  applySuggestions,
  type ApplySuggestionsRequest,
  type UpdateAndProcessRequest,
  approveSession,
  rejectSession,
  emitSession,
  type FetchOptions,
  type IngestTextRequest,
  type ApproveRequest,
  type RejectRequest,
  type EmitRequest,
} from '../lib/planning-api';

// ── State interface ────────────────────────────────────────────────────

interface PlanState {
  // Data
  sessions: SessionSummary[];
  currentSession: PlanSession | null;

  // UI state
  loading: boolean;
  sessionLoading: boolean;
  error: string | null;

  // Injectable fetch for testing
  fetchFn?: typeof fetch;

  // Actions
  setFetchFn: (fn: typeof fetch) => void;
  loadSessions: () => Promise<void>;
  refreshSessions: () => Promise<void>;
  selectSession: (sessionId: string) => Promise<void>;
  clearCurrentSession: () => void;
  ingestTextPlan: (body: IngestTextRequest) => Promise<PlanSession>;
  ingestFilePlan: (
    file: File,
    title?: string,
    category?: string,
    template?: string,
  ) => Promise<PlanSession>;
  /** Re-run the pipeline, optionally applying human edits first (#692). */
  processCurrentSession: (updates?: UpdateAndProcessRequest) => Promise<void>;
  /** Apply accepted suggestions and re-run the pipeline (#701). */
  applyAcceptedSuggestions: (body: ApplySuggestionsRequest) => Promise<void>;
  approveCurrentSession: (body: ApproveRequest) => Promise<void>;
  rejectCurrentSession: (body: RejectRequest) => Promise<void>;
  emitCurrentSession: (body: EmitRequest) => Promise<void>;
  refreshCurrentSession: () => Promise<void>;
  clearError: () => void;
}

// ── Store ──────────────────────────────────────────────────────────────

export const usePlanStore = create<PlanState>((set, get) => ({
  sessions: [],
  currentSession: null,
  loading: false,
  sessionLoading: false,
  error: null,
  fetchFn: undefined,

  setFetchFn(fn) {
    set({ fetchFn: fn });
  },

  clearError() {
    set({ error: null });
  },

  async loadSessions() {
    const opts: FetchOptions = { fetchFn: get().fetchFn };
    set({ loading: true, error: null });
    try {
      const result = await listSessions(opts);
      set({ sessions: result.sessions, loading: false });
    } catch (err) {
      set({
        loading: false,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  },

  // Quiet background refresh — does NOT toggle `loading` (so the auto-refresh
  // poll + WS reconnect/visibility self-heal never flash a spinner) and keeps
  // the last-good list on a transient error rather than blanking the board.
  async refreshSessions() {
    const opts: FetchOptions = { fetchFn: get().fetchFn };
    try {
      const result = await listSessions(opts);
      set({ sessions: result.sessions });
    } catch {
      // Swallow: a momentary blip shouldn't clear what the user is looking at.
    }
  },

  async selectSession(sessionId) {
    const opts: FetchOptions = { fetchFn: get().fetchFn };
    set({ sessionLoading: true, error: null });
    try {
      const session = await getSession(sessionId, opts);
      set({ currentSession: session, sessionLoading: false });
    } catch (err) {
      set({
        sessionLoading: false,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  },

  clearCurrentSession() {
    set({ currentSession: null, error: null });
  },

  async ingestTextPlan(body) {
    const opts: FetchOptions = { fetchFn: get().fetchFn };
    set({ loading: true, error: null });
    try {
      const session = await ingestText(body, opts);
      // Add a summary entry to the sessions list immediately
      set((state) => ({
        loading: false,
        currentSession: session,
        sessions: [
          {
            session_id: session.session_id,
            title: session.plan.title,
            status: session.status,
            target_kind: session.plan.target_kind,
            plan_type: session.plan.plan_type,
            children: 0,
            gates_passed: null,
            created_at: session.created_at,
          },
          ...state.sessions,
        ],
      }));
      return session;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ loading: false, error: message });
      throw err;
    }
  },

  async ingestFilePlan(file, title, category, template) {
    const { fetchFn } = get();
    set({ loading: true, error: null });
    try {
      const session = await ingestFile(file, { title, category, template, fetchFn });
      set((state) => ({
        loading: false,
        currentSession: session,
        sessions: [
          {
            session_id: session.session_id,
            title: session.plan.title,
            status: session.status,
            target_kind: session.plan.target_kind,
            plan_type: session.plan.plan_type,
            children: 0,
            gates_passed: null,
            created_at: session.created_at,
          },
          ...state.sessions,
        ],
      }));
      return session;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ loading: false, error: message });
      throw err;
    }
  },

  async applyAcceptedSuggestions(body: ApplySuggestionsRequest) {
    const { currentSession, fetchFn } = get();
    if (!currentSession) return;
    set({ sessionLoading: true, error: null });
    try {
      const updated = await applySuggestions(currentSession.session_id, body, { fetchFn });
      set({ sessionLoading: false, currentSession: updated });
    } catch (err) {
      set({
        sessionLoading: false,
        error: err instanceof Error ? err.message : String(err),
      });
      throw err;
    }
  },

  async processCurrentSession(updates?: UpdateAndProcessRequest) {
    const { currentSession, fetchFn } = get();
    if (!currentSession) return;
    const opts: FetchOptions = { fetchFn };
    set({ sessionLoading: true, error: null });
    try {
      const updated = updates
        ? await updateAndProcess(currentSession.session_id, updates, opts)
        : await processSession(currentSession.session_id, opts);
      set((state) => ({
        sessionLoading: false,
        currentSession: updated,
        sessions: state.sessions.map((s) =>
          s.session_id === updated.session_id
            ? {
                ...s,
                status: updated.status,
                gates_passed: updated.review?.gates_passed ?? null,
                children: updated.epic?.children.length ?? s.children,
              }
            : s,
        ),
      }));
    } catch (err) {
      set({
        sessionLoading: false,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  },

  async approveCurrentSession(body) {
    const { currentSession, fetchFn } = get();
    if (!currentSession) return;
    const opts: FetchOptions = { fetchFn };
    set({ sessionLoading: true, error: null });
    try {
      const updated = await approveSession(currentSession.session_id, body, opts);
      set((state) => ({
        sessionLoading: false,
        currentSession: updated,
        sessions: state.sessions.map((s) =>
          s.session_id === updated.session_id ? { ...s, status: updated.status } : s,
        ),
      }));
    } catch (err) {
      set({
        sessionLoading: false,
        error: err instanceof Error ? err.message : String(err),
      });
      throw err;
    }
  },

  async rejectCurrentSession(body) {
    const { currentSession, fetchFn } = get();
    if (!currentSession) return;
    const opts: FetchOptions = { fetchFn };
    set({ sessionLoading: true, error: null });
    try {
      const updated = await rejectSession(currentSession.session_id, body, opts);
      set((state) => ({
        sessionLoading: false,
        currentSession: updated,
        sessions: state.sessions.map((s) =>
          s.session_id === updated.session_id ? { ...s, status: updated.status } : s,
        ),
      }));
    } catch (err) {
      set({
        sessionLoading: false,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  },

  async emitCurrentSession(body) {
    const { currentSession, fetchFn } = get();
    if (!currentSession) return;
    const opts: FetchOptions = { fetchFn };
    set({ sessionLoading: true, error: null });
    try {
      const updated = await emitSession(currentSession.session_id, body, opts);
      set((state) => ({
        sessionLoading: false,
        currentSession: updated,
        sessions: state.sessions.map((s) =>
          s.session_id === updated.session_id ? { ...s, status: updated.status } : s,
        ),
      }));
    } catch (err) {
      set({
        sessionLoading: false,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  },

  async refreshCurrentSession() {
    const { currentSession } = get();
    if (!currentSession) return;
    await get().selectSession(currentSession.session_id);
  },
}));
