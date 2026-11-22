import { afterEach, expect, it, vi } from 'vitest';
import type { Task } from '../../shared/types';
import { loadTasks, useTaskStore } from '../task-store';

afterEach(() => vi.unstubAllGlobals());

it('ignores a superseded project task response', async () => {
  let resolveOld!: (value: { success: true; data: Task[] }) => void;
  let resolveNew!: (value: { success: true; data: Task[] }) => void;
  const oldResponse = new Promise<{ success: true; data: Task[] }>((resolve) => { resolveOld = resolve; });
  const newResponse = new Promise<{ success: true; data: Task[] }>((resolve) => { resolveNew = resolve; });
  vi.stubGlobal('API', { getTasks: vi.fn().mockReturnValueOnce(oldResponse).mockReturnValueOnce(newResponse) });

  const oldLoad = loadTasks('old');
  useTaskStore.getState().clearTasks();
  const newLoad = loadTasks('new');
  resolveOld({ success: true, data: [{ id: 'old-task' } as Task] });
  await oldLoad;
  expect(useTaskStore.getState().tasks).toEqual([]);
  expect(useTaskStore.getState().isLoading).toBe(true);

  resolveNew({ success: true, data: [{ id: 'new-task' } as Task] });
  await newLoad;
  expect(useTaskStore.getState().tasks.map((task) => task.id)).toEqual(['new-task']);
  expect(useTaskStore.getState().isLoading).toBe(false);
});

it('clearTasks invalidates an in-flight load without a replacement', async () => {
  let resolve!: (value: { success: true; data: Task[] }) => void;
  vi.stubGlobal('API', { getTasks: () => new Promise((done) => { resolve = done; }) });
  const load = loadTasks('old');
  useTaskStore.getState().clearTasks();
  resolve({ success: true, data: [{ id: 'stale' } as Task] });
  await load;
  expect(useTaskStore.getState().tasks).toEqual([]);
  expect(useTaskStore.getState().isLoading).toBe(false);
});
