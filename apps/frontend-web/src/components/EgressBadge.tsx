/**
 * Honest data-egress badge for an LLM endpoint — shows whether a run keeps data
 * on your network (mirrors the backend byo_llm posture). Renders nothing for an
 * unclassifiable / empty URL.
 */

import { classifyEgress, EGRESS_META } from '../lib/egress';

const TONE: Record<'success' | 'info' | 'warning', string> = {
  success: 'bg-success/15 text-success border-success/30',
  info: 'bg-info/15 text-info border-info/30',
  warning: 'bg-warning/15 text-warning border-warning/30',
};

export function EgressBadge({ baseUrl, className = '' }: { baseUrl: string | null | undefined; className?: string }) {
  const cls = classifyEgress(baseUrl);
  if (!cls) return null;
  const meta = EGRESS_META[cls];
  return (
    <span
      data-testid="egress-badge"
      data-egress={cls}
      title={meta.label}
      className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium ${TONE[meta.tone]} ${className}`}
    >
      {meta.badge}
    </span>
  );
}
