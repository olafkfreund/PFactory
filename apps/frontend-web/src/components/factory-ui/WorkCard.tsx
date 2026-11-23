import { memo, useId } from 'react';
import type { ReactNode } from 'react';

export type WorkTone = 'neutral' | 'running' | 'success' | 'warning' | 'danger';

export interface WorkAction {
  id: string;
  label: string;
  onClick(): void;
  variant: 'primary' | 'outline' | 'ghost' | 'danger';
  disabled?: boolean | undefined;
  busy?: boolean | undefined;
}

export interface WorkPhase {
  key: string;
  label: string;
  state: 'done' | 'active' | 'failed' | 'pending';
}

export interface WorkCardLabels {
  expand(title: string): string;
  collapse(title: string): string;
  phaseStates: Record<WorkPhase['state'], string>;
}

export interface WorkCardProps {
  labels?: WorkCardLabels | undefined;
  title: string;
  subtitle?: string | undefined;
  status: string;
  tone: WorkTone;
  age: string;
  reason?: string | undefined;
  progress?: number | undefined;
  phases?: WorkPhase[] | undefined;
  badges?: string[] | undefined;
  onOpen?: (() => void) | undefined;
  expanded?: boolean | undefined;
  onToggle?: (() => void) | undefined;
  actions?: WorkAction[] | undefined;
  children?: ReactNode | undefined;
  collapsedByDefault?: boolean | undefined;
}

function displayedActions({ actions = [], tone, expanded }: WorkCardProps): WorkAction[] {
  if (expanded) return actions;
  if (tone === 'danger') return actions.slice(0, 2);
  const primary = actions.find(({ variant }) => variant === 'primary');
  return primary ? [primary] : [];
}

function isCollapsed(card: WorkCardProps) {
  return !!card.collapsedByDefault && !card.expanded;
}

function sameItems<T>(a: T[] = [], b: T[] = [], same: (left: T, right: T) => boolean) {
  return a === b || (a.length === b.length && a.every((item, i) => {
    const other = b[i];
    return other !== undefined && same(item, other);
  }));
}

function sameCard(a: WorkCardProps, b: WorkCardProps) {
  if (
    a.title !== b.title || a.status !== b.status || a.tone !== b.tone || a.age !== b.age
    || a.onOpen !== b.onOpen || a.onToggle !== b.onToggle || !!a.expanded !== !!b.expanded
    || isCollapsed(a) !== isCollapsed(b) || a.labels !== b.labels
  ) return false;
  if (isCollapsed(a)) return true;
  if (
    a.subtitle !== b.subtitle || a.progress !== b.progress
    || (a.tone === 'danger' && a.reason !== b.reason)
    || !sameItems(displayedActions(a), displayedActions(b), (left, right) =>
      left.id === right.id && left.label === right.label && left.onClick === right.onClick
      && left.variant === right.variant && left.busy === right.busy && left.disabled === right.disabled,
    )
  ) return false;
  return !a.expanded || (
    a.children === b.children
    && sameItems(a.badges, b.badges, (left, right) => left === right)
    && sameItems(a.phases, b.phases, (left, right) =>
      left.key === right.key && left.label === right.label && left.state === right.state,
    )
  );
}

export const WorkCard = memo(function WorkCard(props: WorkCardProps) {
  const { title, subtitle, status, tone, age, reason, progress, phases, badges, onOpen, expanded, onToggle, children, labels } = props;
  const detailsId = useId();
  const collapsed = isCollapsed(props);
  const failed = tone === 'danger';
  const state = collapsed ? 'collapsed' : failed ? 'failed' : 'active';
  const actions = collapsed ? [] : displayedActions(props);
  const percent = progress !== undefined && Number.isFinite(progress)
    ? Math.max(0, Math.min(100, progress))
    : undefined;

  return (
    <article className={`f-card f-card--${tone} f-card--${state}${expanded ? ' f-card--expanded' : ''}`}>
      <div className="f-card__row">
        <span className="f-card__dot" aria-hidden />
        {onOpen ? (
          <button type="button" className="f-card__open" title={title} onClick={onOpen}>{title}</button>
        ) : <span className="f-card__title" title={title}>{title}</span>}
        <span className="f-card__badge">{status}</span>
        <span className="f-card__age">{age}</span>
        {onToggle && (
          <button
            type="button"
            className="f-card__chevron"
            aria-label={expanded ? (labels?.collapse(title) ?? `Collapse ${title}`) : (labels?.expand(title) ?? `Expand ${title}`)}
            aria-expanded={!!expanded}
            aria-controls={expanded ? detailsId : undefined}
            onClick={onToggle}
          >
            <span aria-hidden>{expanded ? '▴' : '▾'}</span>
          </button>
        )}
      </div>
      {!collapsed && (
        <>
          {subtitle && <span className="f-card__subtitle" title={subtitle}>{subtitle}</span>}
          {percent !== undefined && (
            <div className="f-card__progress" role="progressbar" aria-label={status} aria-valuenow={percent}>
              <div className="f-card__progress-fill" style={{ width: `${percent}%` }} />
            </div>
          )}
          {failed && reason && <div className="f-card__reason" title={reason}>{reason}</div>}
          {expanded && (
            <div id={detailsId} className="f-card__details">
              {!!badges?.length && (
                <div className="f-card__badges">
                  {badges.map((badge, i) => <span key={i} className="f-card__badge">{badge}</span>)}
                </div>
              )}
              {!!phases?.length && (
                <div className="f-card__phases">
                  {phases.map(({ key, label, state: phaseState }) => (
                    <span key={key} className={`f-card__phase f-card__phase--${phaseState}`}>
                      <span aria-hidden>{{ done: '✓', active: '◉', failed: '!', pending: '○' }[phaseState]}</span>
                      <span className="f-sr-only">{labels?.phaseStates[phaseState] ?? phaseState} </span>
                      {label}
                    </span>
                  ))}
                </div>
              )}
              {children}
            </div>
          )}
          {!!actions.length && (
            <div className="f-card__actions">
              {actions.map(({ id, label, onClick, variant, disabled, busy }) => (
                <button
                  key={id}
                  type="button"
                  className={`f-btn f-btn--sm f-btn--${variant}`}
                  disabled={disabled || busy}
                  aria-busy={busy}
                  onClick={onClick}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </article>
  );
}, sameCard);
