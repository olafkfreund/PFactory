import { Fragment, useState } from 'react';
import type { KeyboardEvent } from 'react';

export type StageTone =
  | 'plan' | 'build' | 'code' | 'test' | 'cockpit' | 'meet'
  | 'neutral' | 'running' | 'info' | 'success' | 'warning' | 'danger';

export interface PipelineStage {
  key: string;
  label: string;
  service: string;
  count: number;
  failed: number;
  tone: StageTone;
}

export interface PipelineStripProps {
  stages: PipelineStage[];
  activeStage?: string | null | undefined;
  onSelectStage(key: string | null): void;
  onSelectFailed?: ((key: string) => void) | undefined;
  alarm?: { key: string; event: string | number } | undefined;
}

export interface PipelineStripLabels {
  pipeline: string;
  clearFilter: string;
  failures(count: number): string;
  filterFailures(stage: PipelineStage): string;
}

export function PipelineStrip({
  stages, activeStage, onSelectStage, onSelectFailed, alarm, labels,
}: PipelineStripProps & { labels: PipelineStripLabels }) {
  const [focused, setFocused] = useState('');
  const controls = stages.flatMap(({ key, failed }) =>
    failed > 0 ? [`stage:${key}`, `failure:${key}`] : [`stage:${key}`],
  );
  if (activeStage != null) controls.push('clear');
  const tabStop = controls.includes(focused) ? focused : controls[0];
  const control = (key: string) => ({
    tabIndex: key === tabStop ? 0 : -1,
    onFocus: () => setFocused(key),
  });

  const handleKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const buttons = Array.from(event.currentTarget.querySelectorAll('button'));
    const index = buttons.findIndex((button) => button === event.target);
    if (index < 0) return;

    const ordered = getComputedStyle(event.currentTarget).direction === 'rtl' ? -1 : 1;
    let next: number;
    switch (event.key) {
      case 'ArrowRight': next = index + ordered; break;
      case 'ArrowLeft': next = index - ordered; break;
      case 'Home': next = 0; break;
      case 'End': next = buttons.length - 1; break;
      default: return;
    }
    event.preventDefault();
    buttons[(next + buttons.length) % buttons.length]?.focus();
  };

  return (
    <div className="f-strip" role="toolbar" aria-label={labels.pipeline} onKeyDown={handleKey}>
      {stages.map((stage, index) => {
        const { key, label, service, count, failed, tone } = stage;
        const active = key === activeStage;
        const failureLabel = failed > 0 ? labels.failures(failed) : '';
        return (
          <Fragment key={key}>
            {index > 0 && (
              <span className={`f-strip__connector${count > 0 ? ' is-flowing' : ''}`} aria-hidden />
            )}
            <div className="f-strip__item">
              <button
                type="button"
                className={`f-strip__node f-strip__node--${tone}${failed > 0 ? ' has-failed' : ''}`}
                aria-pressed={active}
                onClick={() => onSelectStage(active ? null : key)}
                {...control(`stage:${key}`)}
              >
                <span className="f-strip__ring">
                  <span className={`f-strip__count${count === 0 ? ' f-strip__count--zero' : ''}`}>{count}</span>
                  {alarm?.key === key && failed > 0 && <span key={alarm.event} className="f-strip__flare" aria-hidden />}
                </span>
                <span className="f-strip__meta">
                  <span className="f-strip__label">{label}</span>
                  <span className="f-strip__service">{service}</span>
                </span>
              </button>
              {failureLabel && (
                <button
                  type="button"
                  className="f-strip__fail"
                  aria-label={labels.filterFailures(stage)}
                  onClick={() => onSelectFailed ? onSelectFailed(key) : onSelectStage(key)}
                  {...control(`failure:${key}`)}
                >
                  {failureLabel}
                </button>
              )}
            </div>
          </Fragment>
        );
      })}
      {activeStage != null && (
        <div className="f-strip__tail">
          <button
            type="button"
            className="f-strip__clear"
            onClick={(event) => {
              const strip = event.currentTarget.closest('.f-strip');
              const next = strip?.querySelector<HTMLButtonElement>('.f-strip__node[aria-pressed="true"]')
                ?? strip?.querySelector<HTMLButtonElement>('.f-strip__node');
              next?.focus();
              onSelectStage(null);
            }}
            {...control('clear')}
          >
            {labels.clearFilter}
          </button>
        </div>
      )}
    </div>
  );
}
