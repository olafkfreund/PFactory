// Portal switcher — the four Factory portals as one product, in the topbar.
// Plan (PFactory) is the current active entry; the others link out with their
// per-service accent dot. Hosts are build-time overridable via VITE_*_URL,
// defaulting to the live portals. Part of the unified shell across the family
// (mirrors the CFactory cockpit switcher).

// import.meta.env values are typed loosely; keep the URLs strictly string.
const envUrl = (v: unknown, fallback: string): string =>
  typeof v === 'string' && v ? v : fallback;

const PORTALS: { key: string; label: string; dot: string; url: string; current: boolean }[] = [
  { key: 'plan', label: 'Plan', dot: '#d3869b', url: '', current: true },
  { key: 'build', label: 'Build', dot: '#fabd2f', url: envUrl(import.meta.env.VITE_AIFACTORY_URL, 'https://aifactory.freundcloud.org.uk'), current: false },
  { key: 'test', label: 'Test', dot: '#b8bb26', url: envUrl(import.meta.env.VITE_TFACTORY_URL, 'https://tfactory.freundcloud.org.uk'), current: false },
  { key: 'cockpit', label: 'Cockpit', dot: '#83a598', url: envUrl(import.meta.env.VITE_CFACTORY_URL, 'https://cfactory.freundcloud.org.uk'), current: false },
];

const ITEM = 'inline-flex items-center gap-1.5 rounded-md px-2 py-1 font-mono text-xs';

export function PortalSwitcher() {
  return (
    <nav
      aria-label="Factory portals"
      className="inline-flex items-center gap-0.5 rounded-lg border border-border bg-card p-0.5"
    >
      {PORTALS.map((p) => {
        const inner = (
          <>
            <span
              className="h-2 w-2 flex-none rounded-full"
              style={{ background: p.dot, boxShadow: `0 0 6px ${p.dot}` }}
            />
            <span className="hidden sm:inline">{p.label}</span>
          </>
        );
        return p.current ? (
          <span key={p.key} aria-current="page" className={`${ITEM} bg-muted text-foreground`}>
            {inner}
          </span>
        ) : (
          <a
            key={p.key}
            href={p.url}
            rel="noopener"
            title={`Open the ${p.label} portal`}
            className={`${ITEM} text-muted-foreground transition-colors hover:bg-muted hover:text-foreground`}
          >
            {inner}
          </a>
        );
      })}
    </nav>
  );
}
