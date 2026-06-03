/**
 * Client-side data-egress classifier for LLM endpoints — mirrors the backend
 * `byo_llm.py` taxonomy so the portal can show an honest trust signal at the
 * point a user picks a provider (the tool ingests proprietary diffs).
 *
 *   LOCAL         — localhost / RFC-1918 / .local / .internal — no egress.
 *   SELF_HOSTED   — your own routable server — you control egress.
 *   MANAGED_CLOUD — a third-party managed API — data leaves your network.
 */

export type EgressClass = 'local' | 'self_hosted' | 'managed_cloud';

const MANAGED_HOSTS = [
  'api.openai.com', 'openrouter.ai', 'api.together.xyz', 'api.groq.com',
  'api.mistral.ai', 'api.deepseek.com', 'generativelanguage.googleapis.com',
  'api.anthropic.com', 'api.anyscale.com', 'api.fireworks.ai', 'api.cohere.ai',
];

function hostIsLocal(host: string): boolean {
  const h = host.toLowerCase().replace(/:\d+$/, '');
  if (h === 'localhost' || h === '127.0.0.1' || h === '::1' || h === '0.0.0.0') return true;
  if (h.endsWith('.local') || h.endsWith('.internal')) return true;
  if (h.startsWith('10.') || h.startsWith('192.168.')) return true;
  // 172.16.0.0 – 172.31.255.255
  const m = h.match(/^172\.(\d+)\./);
  if (m) {
    const second = Number(m[1]);
    if (second >= 16 && second <= 31) return true;
  }
  return false;
}

export function classifyEgress(baseUrl: string | null | undefined): EgressClass | null {
  if (!baseUrl || !baseUrl.trim()) return null;
  let host: string;
  try {
    host = new URL(baseUrl.includes('://') ? baseUrl : `http://${baseUrl}`).hostname;
  } catch {
    return null;
  }
  if (!host) return null;
  if (hostIsLocal(host)) return 'local';
  if (MANAGED_HOSTS.some((m) => host === m || host.endsWith(`.${m}`))) return 'managed_cloud';
  return 'self_hosted';
}

export const EGRESS_META: Record<EgressClass, { badge: string; label: string; tone: 'success' | 'info' | 'warning' }> = {
  local: { badge: '🔒 Local', label: 'Local — no data egress', tone: 'success' },
  self_hosted: { badge: '🏠 Self-hosted', label: 'Self-hosted — your server', tone: 'info' },
  managed_cloud: { badge: '☁️ Managed cloud', label: 'Managed cloud — data leaves your network', tone: 'warning' },
};
