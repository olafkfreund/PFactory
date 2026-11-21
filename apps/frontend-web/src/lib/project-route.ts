// URL <-> project mapping for /p/:slug[/:view]. Pure: no store, no router.
import type { Project } from '../shared/types';

export const PROJECT_VIEWS = [
  'planning', 'pfactory', 'terminals', 'editor', 'context', 'github-issues',
  'github-prs', 'changelog', 'insights', 'worktrees', 'agent-tools', 'skills',
  'cloud', 'visual-reports',
] as const;
export type ProjectView = (typeof PROJECT_VIEWS)[number];

export type AppRoute =
  | { kind: 'project'; ref: string; view: ProjectView }
  | { kind: 'projects' }
  | { kind: 'home' };

export function slugify(name: string, fallback: string): string {
  const s = name
    .normalize('NFKD')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return s || fallback;
}

const created = (p: Project) => new Date(p.createdAt).getTime() || 0;

/** Canonical slug per project id. Backend slugs are reserved first; derived
 * ones go oldest-first so a new "Demo" never takes over an existing /p/demo. */
export function projectSlugs(projects: Project[]): Map<string, string> {
  const out = new Map<string, string>();
  const used = new Set<string>();
  for (const p of projects) {
    if (p.slug) {
      out.set(p.id, p.slug);
      used.add(p.slug);
    }
  }
  const derived = projects
    .filter((p) => !p.slug)
    .sort((a, b) => created(a) - created(b) || a.id.localeCompare(b.id));
  for (const p of derived) {
    const base = slugify(p.name, p.id);
    const withId = `${base}-${p.id.slice(0, 8)}`;
    let slug = used.has(base) ? withId : base;
    for (let n = 2; used.has(slug); n++) slug = `${withId}-${String(n)}`;
    used.add(slug);
    out.set(p.id, slug);
  }
  return out;
}

export const projectSlug = (projects: Project[], p: Project): string =>
  projectSlugs(projects).get(p.id) ?? p.id;

/** Canonical slug first, then id, then any alias (backend or derived slug). */
export function findProjectByRef(projects: Project[], ref: string): Project | undefined {
  const r = ref.toLowerCase();
  const slugs = projectSlugs(projects);
  return (
    projects.find((p) => slugs.get(p.id)?.toLowerCase() === r) ??
    projects.find((p) => p.id.toLowerCase() === r) ??
    projects.find(
      (p) =>
        p.slug?.toLowerCase() === r ||
        slugify(p.name, p.id) === r ||
        `${slugify(p.name, p.id)}-${p.id.slice(0, 8)}`.toLowerCase() === r,
    )
  );
}

export function parseRoute(pathname: string): AppRoute {
  const m = /^\/p\/([^/]+)(?:\/([^/]+))?/.exec(pathname);
  if (m) {
    const view = PROJECT_VIEWS.find((v) => v === m[2]) ?? 'planning';
    let ref = m[1];
    try { ref = decodeURIComponent(ref); } catch { /* keep the raw segment */ }
    return { kind: 'project', ref, view };
  }
  if (/^\/projects\/?$/.test(pathname)) return { kind: 'projects' };
  return { kind: 'home' };
}

export const projectPath = (slug: string, view?: ProjectView | string): string =>
  `/p/${encodeURIComponent(slug)}${view && view !== 'planning' ? `/${view}` : ''}`;
