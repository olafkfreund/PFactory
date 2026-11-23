// @factory/ui — Portal switcher. Canonical shared component; vendored
// byte-identical into each portal (see README.md). Renders the five Factory
// portals as one product in the topbar: the `current` portal is a static active
// chip, the others link out with their per-service accent dot. Which portal is
// "current" is passed by the host; optional `project` keeps its slug on named
// siblings. Sibling hosts are build-time overridable via
// VITE_*_URL, defaulting to the live portals.
//
// Optional `needsCount` (#148/#149) renders a small badge on the Cockpit chip —
// the fleet count of work items blocked on a human — so every portal's top bar
// shows the same "N need you" nudge toward the cockpit inbox.
export type FactoryPortal = 'plan' | 'build' | 'test' | 'cockpit' | 'meet';

// import.meta.env values are typed loosely; keep the URLs strictly string.
const envUrl = (v: unknown, fallback: string): string =>
  typeof v === 'string' && v ? v : fallback;

const PORTALS: { key: FactoryPortal; label: string; dot: string; url: string }[] = [
  { key: 'plan', label: 'Plan', dot: 'var(--f-plan)', url: envUrl(import.meta.env.VITE_PFACTORY_URL, 'https://pfactory.freundcloud.org.uk') },
  { key: 'build', label: 'Build', dot: 'var(--f-build)', url: envUrl(import.meta.env.VITE_AIFACTORY_URL, 'https://aifactory.freundcloud.org.uk') },
  { key: 'test', label: 'Test', dot: 'var(--f-test)', url: envUrl(import.meta.env.VITE_TFACTORY_URL, 'https://tfactory.freundcloud.org.uk') },
  { key: 'cockpit', label: 'Cockpit', dot: 'var(--f-cockpit)', url: envUrl(import.meta.env.VITE_CFACTORY_URL, 'https://cfactory.freundcloud.org.uk') },
  { key: 'meet', label: 'Meet', dot: 'var(--f-meet)', url: envUrl(import.meta.env.VITE_RFACTORY_URL, 'https://rfactory.freundcloud.org.uk') },
];

export function PortalSwitcher({
  current,
  needsCount,
  project,
  labels,
  ariaLabel,
}: {
  current: FactoryPortal;
  needsCount?: number | undefined;
  project?: { slug: string; portals: FactoryPortal[] } | undefined;
  labels?: Record<FactoryPortal, string> | undefined;
  ariaLabel?: string | undefined;
}) {
  return (
    <nav
      aria-label={ariaLabel ?? "Factory portals"}
      className="f-portal-switcher"
    >
      {PORTALS.map((p) => {
        const label = labels?.[p.key] ?? p.label;
        const badge = p.key === 'cockpit' && needsCount ? needsCount : 0;
        const inner = (
          <>
            <span
              className="f-portal-switcher__dot"
              aria-hidden
              style={{ background: p.dot, boxShadow: `0 0 6px ${p.dot}` }}
            />
            <span className="f-portal-switcher__label">{label}</span>
            {badge > 0 && (
              <span
                aria-label={`${String(badge)} need you`}
                className="f-portal-switcher__badge"
              >
                {badge}
              </span>
            )}
          </>
        );
        return p.key === current ? (
          <span key={p.key} aria-current="page" className="f-portal-switcher__item">
            {inner}
          </span>
        ) : (
          <a
            key={p.key}
            href={project?.portals.includes(p.key) ? `${p.url.replace(/\/+$/, '')}/p/${encodeURIComponent(project.slug)}` : p.url}
            rel="noopener"
            title={label}
            className="f-portal-switcher__item"
          >
            {inner}
          </a>
        );
      })}
    </nav>
  );
}
