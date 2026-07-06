// @factory/ui — Portal switcher. Canonical shared component; vendored
// byte-identical into each portal (see README.md). Renders the four Factory
// portals as one product in the topbar: the `current` portal is a static active
// chip, the others link out with their per-service accent dot. Which portal is
// "current" is the host's only per-portal input — pass it as a prop so this file
// stays identical everywhere. Sibling hosts are build-time overridable via
// VITE_*_URL, defaulting to the live portals.
//
// Optional `needsCount` (#148/#149) renders a small badge on the Cockpit chip —
// the fleet count of work items blocked on a human — so every portal's top bar
// shows the same "N need you" nudge toward the cockpit inbox. Omit it and the
// component renders exactly as before (byte-identical for hosts that don't wire
// the count).
export type FactoryPortal = 'plan' | 'build' | 'test' | 'cockpit';

// import.meta.env values are typed loosely; keep the URLs strictly string.
const envUrl = (v: unknown, fallback: string): string =>
  typeof v === 'string' && v ? v : fallback;

const PORTALS: { key: FactoryPortal; label: string; dot: string; url: string }[] = [
  { key: 'plan', label: 'Plan', dot: '#d3869b', url: envUrl(import.meta.env.VITE_PFACTORY_URL, 'https://pfactory.freundcloud.org.uk') },
  { key: 'build', label: 'Build', dot: '#fabd2f', url: envUrl(import.meta.env.VITE_AIFACTORY_URL, 'https://aifactory.freundcloud.org.uk') },
  { key: 'test', label: 'Test', dot: '#b8bb26', url: envUrl(import.meta.env.VITE_TFACTORY_URL, 'https://tfactory.freundcloud.org.uk') },
  { key: 'cockpit', label: 'Cockpit', dot: '#83a598', url: envUrl(import.meta.env.VITE_CFACTORY_URL, 'https://cfactory.freundcloud.org.uk') },
];

const ITEM = 'inline-flex items-center gap-1.5 rounded-md px-2 py-1 font-mono text-xs';

export function PortalSwitcher({
  current,
  needsCount,
}: {
  current: FactoryPortal;
  needsCount?: number;
}) {
  return (
    <nav
      aria-label="Factory portals"
      className="inline-flex items-center gap-0.5 rounded-lg border border-border bg-card p-0.5"
    >
      {PORTALS.map((p) => {
        const badge = p.key === 'cockpit' && needsCount ? needsCount : 0;
        const inner = (
          <>
            <span
              className="h-2 w-2 flex-none rounded-full"
              style={{ background: p.dot, boxShadow: `0 0 6px ${p.dot}` }}
            />
            <span className="hidden sm:inline">{p.label}</span>
            {badge > 0 && (
              <span
                aria-label={`${String(badge)} need you`}
                className="ml-0.5 min-w-4 rounded-full bg-primary px-1 text-center text-[0.6rem] font-semibold leading-4 text-primary-foreground"
              >
                {badge}
              </span>
            )}
          </>
        );
        return p.key === current ? (
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
