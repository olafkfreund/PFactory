import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useTaskDetail } from '../useTaskDetail';

// Stable reference, like the real store: a fresh object per render would re-run the effects.
const project = { id: 'p1' };
vi.mock('../../../../stores/project-store', () => ({
  useProjectStore: (sel: (s: unknown) => unknown) => sel({ getSelectedProject: () => project }),
}));
vi.mock('../../../../stores/task-store', () => ({
  checkTaskRunning: vi.fn(),
  isIncompleteHumanReview: () => false,
  getTaskProgress: () => ({ progress: 0 }),
}));

type Resolver = (v: unknown) => void;
const pending: Record<string, Resolver> = {};

const task = (specId: string) =>
  ({ id: specId, specId, status: 'backlog', subtasks: [] }) as never;

beforeEach(() => {
  for (const k of Object.keys(pending)) delete pending[k];
  (window as unknown as { API: unknown }).API = {
    getTaskLogs: (_p: string, specId: string) => new Promise((r) => (pending[specId] = r)),
    watchTaskLogs: vi.fn(),
    unwatchTaskLogs: vi.fn(),
    onTaskLogsChanged: () => () => {},
    onTaskLogsStream: () => () => {},
    onTaskUpdate: () => () => {},
    getWorktreeStatus: vi.fn(),
    getWorktreeDiff: vi.fn(),
  };
});

describe('useTaskDetail log loading', () => {
  it('ignores a late response for the previous task', async () => {
    const { result, rerender } = renderHook(({ t }) => useTaskDetail({ task: t }), {
      initialProps: { t: task('A') },
    });
    rerender({ t: task('B') });
    await act(async () => {
      pending.B({ success: true, data: { phases: {}, id: 'B' } });
    });
    await act(async () => {
      pending.A({ success: true, data: { phases: {}, id: 'A' } }); // late
    });
    expect((result.current.phaseLogs as unknown as { id: string }).id).toBe('B');
  });

  it('clears the previous task logs when the task changes', async () => {
    const { result, rerender } = renderHook(({ t }) => useTaskDetail({ task: t }), {
      initialProps: { t: task('A') },
    });
    await act(async () => {
      pending.A({ success: true, data: { phases: { coding: { status: 'active' } }, id: 'A' } });
    });
    expect(result.current.phaseLogs).not.toBeNull();
    expect(result.current.expandedPhases.size).toBe(1);
    rerender({ t: task('B') });
    expect(result.current.phaseLogs).toBeNull();
    expect(result.current.expandedPhases.size).toBe(0);
  });
});
