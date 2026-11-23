import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import type { ReactNode } from 'react';
import type { StageTone } from './PipelineStrip';

export interface WorkColumnItem {
  id: string;
  projectId: string;
  collapsedByDefault?: boolean | undefined;
  render(state: { expanded: boolean; onToggle(): void }): ReactNode;
}

export interface WorkColumnProps {
  title: string;
  tone?: StageTone | undefined;
  projectId?: string | undefined;
  items: WorkColumnItem[];
  emptyLabel: string;
  showMoreLabel(count: number): string;
}

const expansionEvent = 'factory-ui:work-expansion';
const unsaved = new Map<string, string | null>();

function readExpansion(key: string): string | null {
  if (unsaved.has(key)) return unsaved.get(key) ?? null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function subscribeExpansion(update: () => void) {
  window.addEventListener(expansionEvent, update);
  window.addEventListener('storage', update);
  return () => {
    window.removeEventListener(expansionEvent, update);
    window.removeEventListener('storage', update);
  };
}

function WorkColumnRow({
  item, itemKey, storageKey, fleet, expanded,
}: {
  item: WorkColumnItem;
  itemKey: string;
  storageKey: string;
  fleet: boolean;
  expanded: boolean;
}) {
  const onToggle = useCallback(() => {
    const activeKey = fleet ? 'factory-ui:work-expansion:fleet' : storageKey;
    const value = readExpansion(activeKey) === itemKey ? null : itemKey;
    try {
      if (value === null && !fleet) window.localStorage.removeItem(storageKey);
      else if (value !== null) window.localStorage.setItem(storageKey, value);
      unsaved.delete(storageKey);
    } catch {
      if (!fleet || value !== null) unsaved.set(storageKey, value);
    }
    if (fleet) {
      try {
        if (value === null) window.localStorage.removeItem('factory-ui:work-expansion:fleet');
        else window.localStorage.setItem('factory-ui:work-expansion:fleet', itemKey);
        unsaved.delete('factory-ui:work-expansion:fleet');
      } catch {
        unsaved.set('factory-ui:work-expansion:fleet', value);
      }
    }
    window.dispatchEvent(new Event(expansionEvent));
  }, [fleet, itemKey, storageKey]);
  return item.render({ expanded, onToggle });
}

function Column({
  title, tone, projectId, items, emptyLabel, showMoreLabel,
}: WorkColumnProps) {
  const [showMore, setShowMore] = useState(false);
  const reveal = useRef<HTMLDivElement>(null);
  const fleet = projectId === undefined;
  const storageKey = `factory-ui:work-expansion:${fleet ? 'fleet' : JSON.stringify(projectId)}`;
  const selected = useSyncExternalStore(subscribeExpansion, () => readExpansion(storageKey), () => null);
  useEffect(() => {
    if (showMore) {
      (reveal.current?.querySelector<HTMLElement>('button, a[href], [tabindex="0"]') ?? reveal.current)?.focus();
    }
  }, [showMore]);
  const rows = items.map((item) => {
    const key = JSON.stringify([item.projectId, item.id]);
    return { item, key, expanded: key === selected };
  });
  let hidden = 0;
  let collapsed = 0;
  let firstHidden: string | undefined;
  const visible = rows.filter(({ item, key, expanded }) => {
    if (expanded || !item.collapsedByDefault) return true;
    if (++collapsed <= 8) return true;
    firstHidden ??= key;
    if (showMore) return true;
    hidden++;
    return false;
  });

  return (
    <section className={`f-column${tone ? ` f-column--${tone}` : ''}${items.length ? '' : ' f-column--empty'}`}>
      <header className="f-column__head">
        <span className="f-column__dot" aria-hidden />
        <h2 className="f-column__title">{title}</h2>
        {!items.length && <span className="f-column__empty">{emptyLabel}</span>}
        <span className="f-column__count">{items.length}</span>
      </header>
      {!!items.length && (
        <div className="f-column__body">
          {visible.map(({ item, key, expanded }) => (
            <div key={key} ref={showMore && key === firstHidden ? reveal : undefined} tabIndex={-1}>
              <WorkColumnRow item={item} itemKey={key} storageKey={fleet ? `factory-ui:work-expansion:${JSON.stringify(item.projectId)}` : storageKey} fleet={fleet} expanded={expanded} />
            </div>
          ))}
          {hidden > 0 && (
            <button type="button" className="f-column__more" onClick={() => setShowMore(true)}>{showMoreLabel(hidden)}</button>
          )}
        </div>
      )}
    </section>
  );
}

export function WorkColumn(props: WorkColumnProps) {
  return <Column key={JSON.stringify(props.projectId ?? null)} {...props} />;
}
