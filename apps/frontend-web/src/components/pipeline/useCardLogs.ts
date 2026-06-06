/**
 * useCardLogs — lightweight hook for in-card live log streaming.
 *
 * Reuses EXACTLY the same transport as useTaskDetail:
 *   • window.API.getTaskLogs         — initial HTTP fetch
 *   • window.API.watchTaskLogs       — file-watcher registration
 *   • window.API.unwatchTaskLogs     — teardown
 *   • window.API.onTaskLogsChanged   — file-change push events
 *   • window.API.onTaskLogsStream    — WebSocket chunk stream
 *
 * Design constraints:
 *   • Lazy: subscription is only started when `enabled === true`.
 *     When the drawer collapses (enabled becomes false) the effect cleanup
 *     calls unwatchTaskLogs + unsubscribes both listeners — zero subscriptions
 *     while the drawer is closed.
 *   • Capped: total buffered log lines are capped at MAX_LINES (200)
 *     so a long-running task with many cards open does not exhaust RAM.
 *   • Per-task: keyed on specId so each card is independent.
 */

import { useState, useEffect, useRef } from 'react';
import { useProjectStore } from '../../stores/project-store';
import type { TaskLogs, TaskLogStreamChunk, TaskLogEntry, TaskPhaseLog, TaskLogPhase } from '../../shared/types';

/** Maximum lines held in the card buffer. Oldest entries are dropped. */
const MAX_LINES = 200;

/** All phases in stable order. */
const ALL_PHASES = ['planning', 'coding', 'validation'] as const;

export interface UseCardLogsResult {
  /** Latest buffered TaskLogs (null = not yet loaded or no data). */
  phaseLogs: TaskLogs | null;
  /** True while the initial HTTP fetch is in flight. */
  isLoading: boolean;
  /** Flat array of the last MAX_LINES log entries across all phases, newest at end. */
  lines: TaskLogEntry[];
}

export function useCardLogs(specId: string, enabled: boolean): UseCardLogsResult {
  const selectedProject = useProjectStore((state) => state.getSelectedProject());

  const [phaseLogs, setPhaseLogs] = useState<TaskLogs | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  // Track the projectId used to subscribe so teardown uses the correct id.
  const projectIdRef = useRef<string | null>(null);

  useEffect(() => {
    // Only subscribe when the drawer is open AND we have a project.
    if (!enabled || !selectedProject) {
      setPhaseLogs(null);
      setIsLoading(false);
      return;
    }

    const projectId = selectedProject.id;
    projectIdRef.current = projectId;

    let cancelled = false;

    // ── Initial load ──────────────────────────────────────────────────────────
    setIsLoading(true);
    window.API.getTaskLogs(projectId, specId).then((result) => {
      if (cancelled) return;
      if (result.success && result.data) {
        setPhaseLogs(capPhaseLogs(result.data));
      }
      setIsLoading(false);
    }).catch(() => {
      if (!cancelled) setIsLoading(false);
    });

    // ── File-watcher subscription (low-frequency "whole-log" pushes) ──────────
    window.API.watchTaskLogs(projectId, specId);

    const unsubChanged = window.API.onTaskLogsChanged((incomingSpecId, logs) => {
      if (cancelled || incomingSpecId !== specId) return;
      setPhaseLogs(capPhaseLogs(logs));
    });

    // ── WebSocket stream (high-frequency per-entry chunks) ────────────────────
    const unsubStream = window.API.onTaskLogsStream((incomingSpecId, chunk: TaskLogStreamChunk) => {
      if (cancelled || incomingSpecId !== specId) return;

      const phase = (chunk.phase ?? 'coding') as TaskLogPhase;
      const entry: TaskLogEntry = {
        timestamp: chunk.timestamp ?? new Date().toISOString(),
        type: chunk.type ?? 'text',
        content: chunk.content ?? '',
        phase,
        tool_name: chunk.tool?.name,
        tool_input: chunk.tool?.input,
        subtask_id: chunk.subtask_id,
      };

      setPhaseLogs((prev) => appendEntry(prev, specId, phase, entry));
    });

    // ── Teardown — called when drawer collapses or component unmounts ─────────
    return () => {
      cancelled = true;
      unsubChanged();
      unsubStream();
      // Unwatch after unsubscribing listeners — no dangling server-side watchers.
      window.API.unwatchTaskLogs(projectId, specId);
      projectIdRef.current = null;
    };
  // selectedProject reference changes when store updates; use .id to avoid
  // re-subscribing on unrelated project store writes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, specId, selectedProject?.id]);

  const lines = flattenLines(phaseLogs);

  return { phaseLogs, isLoading, lines };
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

/** Flatten all phase entries into a single chronological array, capped at MAX_LINES. */
function flattenLines(logs: TaskLogs | null): TaskLogEntry[] {
  if (!logs?.phases) return [];

  const all: TaskLogEntry[] = [];
  for (const phase of ALL_PHASES) {
    const phaseLog = logs.phases[phase];
    if (phaseLog?.entries) {
      all.push(...phaseLog.entries);
    }
  }

  // Sort by timestamp ascending; fall back to insertion order if equal.
  all.sort((a, b) => {
    try {
      return new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime();
    } catch {
      return 0;
    }
  });

  // Cap: keep the LAST MAX_LINES entries (newest visible at bottom of drawer).
  return all.length > MAX_LINES ? all.slice(all.length - MAX_LINES) : all;
}

/**
 * Append a new entry to the matching phase in phaseLogs, enforcing the cap
 * by trimming the oldest entries if needed.
 */
function appendEntry(
  prev: TaskLogs | null,
  specId: string,
  phase: TaskLogPhase,
  entry: TaskLogEntry
): TaskLogs {
  const createEmptyPhaseLog = (p: TaskLogPhase): TaskPhaseLog => ({
    phase: p,
    status: 'pending',
    started_at: null,
    completed_at: null,
    entries: [],
  });

  const base: TaskLogs = prev ?? {
    spec_id: specId,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    phases: {
      planning: createEmptyPhaseLog('planning'),
      coding: createEmptyPhaseLog('coding'),
      validation: createEmptyPhaseLog('validation'),
    },
  };

  const phases = base.phases ?? {
    planning: createEmptyPhaseLog('planning'),
    coding: createEmptyPhaseLog('coding'),
    validation: createEmptyPhaseLog('validation'),
  };

  const phaseLog: TaskPhaseLog = phases[phase] ?? createEmptyPhaseLog(phase);
  let updatedEntries = [...phaseLog.entries, entry];

  // Per-phase cap.
  if (updatedEntries.length > MAX_LINES) {
    updatedEntries = updatedEntries.slice(updatedEntries.length - MAX_LINES);
  }

  const newStatus = phaseLog.status === 'pending' && entry.type !== 'phase_end'
    ? 'active'
    : phaseLog.status;

  return {
    ...base,
    phases: {
      ...phases,
      [phase]: {
        ...phaseLog,
        status: entry.type === 'phase_end' ? 'completed' : newStatus,
        entries: updatedEntries,
      },
    },
    updated_at: new Date().toISOString(),
  };
}

/**
 * Cap each phase's entry list to MAX_LINES after a full-log push via
 * onTaskLogsChanged, preventing a single push from bypassing the cap.
 */
function capPhaseLogs(logs: TaskLogs): TaskLogs {
  if (!logs.phases) return logs;

  const cappedPhases = { ...logs.phases };
  for (const phase of ALL_PHASES) {
    const phaseLog = cappedPhases[phase];
    if (phaseLog && phaseLog.entries.length > MAX_LINES) {
      cappedPhases[phase] = {
        ...phaseLog,
        entries: phaseLog.entries.slice(phaseLog.entries.length - MAX_LINES),
      };
    }
  }

  return { ...logs, phases: cappedPhases };
}
