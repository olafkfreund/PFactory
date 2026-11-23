/**
 * PlanBoard — kanban of planning sessions rendered with the hub-owned
 * shared factory-ui PipelineStrip, WorkColumn and WorkCard components (#3876).
 *
 * Stages (spec R4): Backlog (including processing) → Review → Approved.
 * Approved / emitted / discarded items collapse to a single 36px row by default;
 * failed / rejected / gates-failed items surface their reason ("needs attention")
 * plus Re-run (outline) and Dismiss (ghost).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { formatRelativeTime } from '../../lib/utils';
import { usePlanStore } from '../../stores/plan-store';
import { usePipelineStripSlot } from '../../contexts/PipelineStripContext';
import { SessionDetail } from './SessionDetail';
import {
  PipelineStrip,
  type PipelineStage,
  type PipelineStripLabels,
  type PipelineStripProps,
  type StageTone,
} from '../factory-ui/PipelineStrip';
import {
  WorkCard,
  type WorkAction,
  type WorkCardLabels,
  type WorkPhase,
  type WorkTone,
} from '../factory-ui/WorkCard';
import { WorkColumn, type WorkColumnItem } from '../factory-ui/WorkColumn';
import type { SessionSummary, BoardColumn } from '../../shared/types/plan';

export type VisibleColumn = 'backlog' | 'human_review' | 'done';
const COLUMNS: VisibleColumn[] = ['backlog', 'human_review', 'done'];

const STAGES: { key: VisibleColumn; label: string; service: string; tone: StageTone }[] = [
  { key: 'backlog', label: 'Backlog', service: 'Ingest · Process', tone: 'plan' },
  { key: 'human_review', label: 'Review', service: 'Gates · Approval', tone: 'warning' },
  { key: 'done', label: 'Approved', service: 'Approved · Emitted', tone: 'success' },
];

export function isDismissed(s: SessionSummary): boolean {
  return (s.status as string) === 'discarded';
}

export function isDone(s: SessionSummary): boolean {
  return (
    s.board_state === 'done' ||
    s.status === 'approved' ||
    s.status === 'emitted' ||
    isDismissed(s) ||
    (s.status as string).endsWith('_empty')
  );
}

export function isFailed(s: SessionSummary): boolean {
  if (isDismissed(s)) return false;
  return s.status === 'failed' || s.status === 'rejected' || s.gates_passed === false;
}

export function isRunning(s: SessionSummary): boolean {
  return (
    s.status === 'processing' ||
    s.status === 'reviewing' ||
    s.board_state === 'in_progress' ||
    s.board_state === 'ai_review'
  );
}

// Project a session onto one of the three visible columns: Backlog, Review, Approved.
export function columnFor(s: SessionSummary): VisibleColumn {
  const raw: BoardColumn =
    s.board_state ??
    (isDone(s)
      ? 'done'
      : s.status === 'rejected' || s.status === 'processed' || s.status === 'failed'
        ? 'human_review'
        : 'backlog');
  if (raw === 'done') return 'done';
  if (raw === 'human_review') return 'human_review';
  return 'backlog'; // backlog + transient in_progress / ai_review
}

export function toneFor(s: SessionSummary): WorkTone {
  if (isFailed(s)) return 'danger';
  if ((s.status as string).endsWith('_empty')) return 'warning';
  if (isDone(s)) return isDismissed(s) ? 'neutral' : 'success';
  if (isRunning(s)) return 'running';
  if (columnFor(s) === 'human_review') return 'warning';
  return 'neutral';
}

export function phaseStrip(s: SessionSummary): WorkPhase[] {
  const col = columnFor(s);
  const failed = isFailed(s);
  const running = isRunning(s);
  const done = isDone(s) && !failed;
  return [
    {
      key: 'backlog',
      label: 'Backlog',
      state: col === 'backlog' ? (failed ? 'failed' : running ? 'active' : 'pending') : 'done',
    },
    {
      key: 'human_review',
      label: 'Review',
      state:
        col === 'done'
          ? 'done'
          : col === 'human_review'
            ? (failed ? 'failed' : 'active')
            : 'pending',
    },
    {
      key: 'done',
      label: 'Approved',
      state: done ? 'done' : 'pending',
    },
  ];
}

interface Props {
  sessions: SessionSummary[];
  loading?: boolean;
  error?: string | null;
  sessionsLoaded?: boolean;
  selectedId: string | null;
  onSelect: (sessionId: string) => void;
  onCloseDetail?: () => void;
  onNewPlan?: () => void;
  viewMode?: 'board' | 'list';
  projectId?: string;
  fetchFn?: typeof fetch;
  onRevealBoard?: () => void;
}

export function PlanBoard({
  sessions,
  loading = false,
  error = null,
  sessionsLoaded = true,
  selectedId,
  onSelect,
  onCloseDetail,
  onNewPlan,
  viewMode = 'board',
  projectId,
  fetchFn,
  onRevealBoard,
}: Props) {
  const { t } = useTranslation(['tasks', 'navigation']);
  const stripSlot = usePipelineStripSlot();
  const storeFetchFn = usePlanStore((state) => state.fetchFn);
  const effectiveFetchFn = fetchFn ?? storeFetchFn;

  const [selectedStage, setSelectedStage] = useState<VisibleColumn | null>(null);
  const [failureOnly, setFailureOnly] = useState(false);
  const busy = usePlanStore((state) => state.actionBusy);
  const actionError = usePlanStore((state) => state.actionError);
  const [alarm, setAlarm] = useState<{ key: string; event: string | number } | undefined>(undefined);
  const prevFailedRef = useRef<Set<string> | null>(null);

  const mergedSessions = sessions;

  const failedSessions = useMemo(() => mergedSessions.filter(isFailed), [mergedSessions]);

  useEffect(() => {
    const nextFailed = new Set<string>();
    let latestAlarm: { key: string; event: string } | undefined;
    for (const s of failedSessions) {
      nextFailed.add(s.session_id);
      if (prevFailedRef.current && !prevFailedRef.current.has(s.session_id)) {
        latestAlarm = {
          key: columnFor(s),
          event: `${s.session_id}:${s.status}:${String(s.gates_passed)}`,
        };
      }
    }
    prevFailedRef.current = nextFailed;
    if (latestAlarm) setAlarm(latestAlarm);
  }, [failedSessions]);

  const buckets = useMemo(() => {
    const map: Record<VisibleColumn, SessionSummary[]> = {
      backlog: [],
      human_review: [],
      done: [],
    };
    for (const s of mergedSessions) {
      map[columnFor(s)].push(s);
    }
    return map;
  }, [mergedSessions]);

  const failedByStage = useMemo(() => {
    const map: Record<VisibleColumn, SessionSummary[]> = {
      backlog: [],
      human_review: [],
      done: [],
    };
    for (const s of failedSessions) {
      map[columnFor(s)].push(s);
    }
    return map;
  }, [failedSessions]);

  useEffect(() => {
    if (effectiveFetchFn) usePlanStore.getState().setFetchFn(effectiveFetchFn);
  }, [effectiveFetchFn]);

  const actOn = (session: SessionSummary, action: 'process' | 'rerun' | 'dismiss') =>
    usePlanStore.getState().actOnSession(session.session_id, action);

  const stages: PipelineStage[] = useMemo(
    () =>
      STAGES.map((s) => ({
        key: s.key,
        label: s.label,
        service: s.service,
        tone: s.tone,
        count: buckets[s.key].length,
        failed: failedByStage[s.key].length,
      })),
    [buckets, failedByStage]
  );

  const handleSelectStage = useCallback((key: string | null) => {
    setSelectedStage(key as VisibleColumn | null);
    setFailureOnly(false);
    if (selectedId && key && mergedSessions.find((s) => s.session_id === selectedId && columnFor(s) !== key)) onCloseDetail?.();
    onRevealBoard?.();
  }, [mergedSessions, onCloseDetail, onRevealBoard, selectedId]);

  const handleSelectFailed = useCallback((key: string) => {
    setSelectedStage(key as VisibleColumn);
    setFailureOnly(true);
    if (selectedId && !mergedSessions.some((s) => s.session_id === selectedId && columnFor(s) === key && isFailed(s))) onCloseDetail?.();
    onRevealBoard?.();
  }, [mergedSessions, onCloseDetail, onRevealBoard, selectedId]);

  useEffect(() => {
    if (!selectedId || !selectedStage) return;
    const selected = mergedSessions.find((s) => s.session_id === selectedId);
    if (selected && (columnFor(selected) !== selectedStage || (failureOnly && !isFailed(selected)))) {
      setSelectedStage(null);
      setFailureOnly(false);
    }
  }, [selectedId, mergedSessions, selectedStage, failureOnly]);

  const stripProps: PipelineStripProps = useMemo(
    () => ({
      stages,
      activeStage: selectedStage,
      onSelectStage: handleSelectStage,
      onSelectFailed: handleSelectFailed,
      alarm,
    }),
    [stages, selectedStage, handleSelectStage, handleSelectFailed, alarm]
  );

  useEffect(() => {
    if (!stripSlot) return;
    stripSlot.setStrip(stripProps);
  }, [stripSlot, stripProps]);

  useEffect(() => {
    if (!stripSlot) return;
    return () => stripSlot.setStrip(undefined);
  }, [stripSlot]);

  const pipelineLabels: PipelineStripLabels = useMemo(
    () => ({
      pipeline: t('navigation:shell.pipeline', 'Pipeline stages'),
      clearFilter: t('navigation:shell.clearFilter', 'Clear filter'),
      failures: (count: number) => t('tasks:pipeline.failed', { count, defaultValue: '{{count}} failed' }),
      filterFailures: (stage) =>
        t('navigation:shell.filterFailures', {
          stage: stage.label,
          count: stage.failed,
          defaultValue: 'Filter {{stage}} failures ({{count}})',
        }),
    }),
    [t]
  );

  const workCardLabels: WorkCardLabels = useMemo(
    () => ({
      expand: (title: string) => `Expand ${title}`,
      collapse: (title: string) => `Collapse ${title}`,
      phaseStates: {
        done: 'done',
        active: 'active',
        failed: 'failed',
        pending: 'pending',
      },
    }),
    []
  );

  const visibleStages = useMemo(
    () => (selectedStage ? STAGES.filter((s) => s.key === selectedStage) : STAGES),
    [selectedStage]
  );

  const filteredListSessions = useMemo(() => {
    const base = failureOnly ? failedSessions : mergedSessions;
    return selectedStage ? base.filter((s) => columnFor(s) === selectedStage) : base;
  }, [failureOnly, failedSessions, mergedSessions, selectedStage]);

  const emptyText = (col: VisibleColumn): string => {
    if (col === 'backlog') return 'Nothing queued';
    if (col === 'human_review') return 'Nothing in review';
    return 'Nothing approved';
  };

  const toColumnItem = (s: SessionSummary): WorkColumnItem => {
    const failed = isFailed(s);
    const done = isDone(s) && !failed;
    const tone = toneFor(s);
    const isSelected = selectedId === s.session_id;
    const subtitleParts = [
      s.project_id ? t('tasks:pipeline.project', { id: s.project_id }) : t('tasks:pipeline.unassignedProject'),
      ...(s.plan_type ? [s.plan_type] : []),
      ...(s.children > 0 ? [`${String(s.children)} issues`] : []),
    ];
    const subtitle = subtitleParts.length > 0 ? subtitleParts.join(' · ') : undefined;
    const reason = failed
      ? (s.gates_passed === false ? 'needs attention' : s.status)
      : undefined;
    const badges = [
      s.session_id,
      ...(s.plan_type ? [s.plan_type] : []),
      ...(s.children > 0 ? [`${String(s.children)} issues`] : []),
      ...(s.gates_passed === true ? ['gates ok'] : []),
    ];

    const actions: WorkAction[] = failed
      ? [
          {
            id: 'rerun',
            label: t('tasks:pipeline.rerun', 'Re-run'),
            variant: 'outline',
            disabled: busy !== null,
            busy: busy === s.session_id,
            onClick: () => {
              void actOn(s, 'rerun');
            },
          },
          {
            id: 'dismiss',
            label: t('tasks:pipeline.dismiss', 'Dismiss'),
            variant: 'ghost',
            disabled: busy !== null,
            busy: busy === s.session_id,
            onClick: () => {
              void actOn(s, 'dismiss');
            },
          },
        ]
      : s.status === 'ingested'
        ? [
            {
              id: 'process',
              label: 'Process',
              variant: 'primary',
              disabled: busy !== null,
              busy: busy === s.session_id,
              onClick: () => {
                void actOn(s, 'process');
              },
            },
          ]
        : [];

    const boardDetailOpen = viewMode === 'board' && isSelected;

    return {
      id: s.session_id,
      projectId: s.project_id ?? projectId ?? '',
      collapsedByDefault: done && !boardDetailOpen,
      render: ({ expanded, onToggle }) => (
        <div
          data-testid="plan-card"
          data-session-id={s.session_id}
          data-selected={isSelected ? 'true' : undefined}
          className={isSelected ? 'ring-1 ring-primary rounded-lg' : undefined}
        >
          <WorkCard
            labels={workCardLabels}
            title={`${s.project_id ?? t('tasks:pipeline.unassigned')} · ${s.title || s.session_id}`}
            subtitle={subtitle}
            status={s.status}
            tone={tone}
            age={formatRelativeTime(s.created_at) || '—'}
            reason={reason}
            phases={phaseStrip(s)}
            badges={badges}
            actions={actions}
            collapsedByDefault={done && !boardDetailOpen}
            expanded={expanded || boardDetailOpen}
            onToggle={() => {
              if (boardDetailOpen) onCloseDetail?.();
              onToggle();
            }}
            onOpen={() => onSelect(s.session_id)}
          >
            {boardDetailOpen && (
              <div className="mt-3 border-t border-border pt-3">
                <SessionDetail
                  sessionId={s.session_id}
                  onBack={() => onCloseDetail?.()}
                />
              </div>
            )}
          </WorkCard>
        </div>
      ),
    };
  };

  return (
    <div className="flex flex-col gap-4" data-testid="plan-board">
      <p className="text-xs text-muted-foreground">{t('tasks:pipeline.fleetScope')}</p>
      {!stripSlot && <PipelineStrip {...stripProps} labels={pipelineLabels} />}

      {actionError && (
        <p role="alert" className="text-sm text-destructive">
          {actionError === 'dismissUnavailable' ? t('tasks:pipeline.dismissUnavailable') : actionError}
        </p>
      )}

      {loading ? (
        <div role="status" className="f-state">{t('tasks:pipeline.loadingPlans', 'Loading plans…')}</div>
      ) : error ? (
        <div role="alert" className="f-state">
          <span>{error}</span>
          <button type="button" className="f-btn" onClick={() => void usePlanStore.getState().loadSessions()}>
            {t('tasks:pipeline.retry', 'Retry')}
          </button>
        </div>
      ) : sessionsLoaded && mergedSessions.length === 0 ? (
        <div className="f-state f-state--empty" data-testid="plan-empty-state">
          <span className="f-state__text">{t('tasks:pipeline.emptyPlans', 'No plans yet')}</span>
          {onNewPlan && (
            <div className="f-state__actions">
              <button
                type="button"
                className="f-btn f-btn--primary"
                onClick={onNewPlan}
              >
                {t('tasks:pipeline.newPlan', 'New plan')}
              </button>
            </div>
          )}
        </div>
      ) : viewMode === 'list' ? (
        <div
          className={selectedId ? 'grid grid-cols-1 lg:grid-cols-2 gap-4 items-start' : 'flex flex-col'}
          data-testid="plan-list-view"
        >
          <WorkColumn
            title={
              selectedStage
                ? (STAGES.find((s) => s.key === selectedStage)?.label ?? t('tasks:pipeline.plansColumn', 'Plans'))
                : t('tasks:pipeline.plansColumn', 'Plans')
            }
            tone={selectedStage ? STAGES.find((s) => s.key === selectedStage)?.tone : 'plan'}
            projectId={undefined}
            emptyLabel={selectedStage ? emptyText(selectedStage) : t('tasks:pipeline.emptyPlans', 'No plans yet')}
            showMoreLabel={(count) =>
              t('tasks:pipeline.showMore', { count, defaultValue: `Show ${count} more` })
            }
            items={filteredListSessions.map(toColumnItem)}
          />
          {selectedId && (
            <div className="rounded-lg border border-border bg-card p-4" data-testid="plan-list-detail-pane">
              <SessionDetail
                sessionId={selectedId}
                onBack={() => onCloseDetail?.()}
              />
            </div>
          )}
        </div>
      ) : (
        <div className="f-board">
          {visibleStages.map((stage) => {
            const colSessions = failureOnly
              ? failedByStage[stage.key]
              : buckets[stage.key];

            return (
              <WorkColumn
                key={stage.key}
                title={stage.label}
                tone={stage.tone}
                projectId={undefined}
                emptyLabel={emptyText(stage.key)}
                showMoreLabel={(count) =>
                  t('tasks:pipeline.showMore', { count, defaultValue: `Show ${count} more` })
                }
                items={colSessions.map(toColumnItem)}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}
