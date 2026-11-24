import type { Project } from '../shared/types';

export const PROJECT_VIEWS = [
  'planning', 'pfactory', 'terminals', 'editor', 'context', 'github-issues',
  'github-prs', 'changelog', 'insights', 'worktrees', 'agent-tools', 'skills',
  'cloud', 'visual-reports', 'settings',
] as const;
export type ProjectView = (typeof PROJECT_VIEWS)[number];
export type AppRoute =
  | { kind: 'project'; ref: string; view: ProjectView; subview?: string }
  | { kind: 'projects' }
  | { kind: 'pfactory' }
  | { kind: 'preferences' }
  | { kind: 'home' };

export function slugify(name: string, fallback: string): string {
  return name.normalize('NFKD').toLowerCase().replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '') || fallback;
}

const key = 'pfactory-project-slug-owners';
const norm = (value: string) => value.toLowerCase();
const base = (p: Project) => norm(p.slug || slugify(p.name, p.id));

function owners(): Record<string, string> {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(key) || '{}');
    return saved && typeof saved === 'object' && !Array.isArray(saved)
      ? Object.assign(Object.create(null) as Record<string, string>, saved) : Object.create(null) as Record<string, string>;
  }
  catch { return {}; }
}

export function projectSlugs(projects: Project[]): Map<string, string> {
  const reserved = owners();
  const groups = new Map<string, Project[]>();
  for (const p of projects) groups.set(base(p), [...(groups.get(base(p)) || []), p]);
  const out = new Map<string, string>();
  const used = new Set([...projects.map((p) => norm(p.id)), ...groups.keys()]);
  for (const [root, group] of groups) reserved[root] ||= group.length > 1 ? '__collision__' : group[0].id;
  for (const p of [...projects].sort((a, b) => a.id.localeCompare(b.id))) {
    const root = base(p);
    const plain = groups.get(root)?.length === 1 && reserved[root] === p.id &&
      !projects.some((other) => other.id !== p.id && norm(other.id) === root);
    const stem = plain ? root : `${root}-${p.id.slice(0, 8)}`;
    let slug = stem;
    for (let n = 2; (used.has(norm(slug)) && slug !== root) || (reserved[norm(slug)] && reserved[norm(slug)] !== p.id); n++) {
      slug = `${stem}-${n}`;
    }
    used.add(norm(slug));
    reserved[norm(slug)] = p.id;
    out.set(p.id, slug);
  }
  try { localStorage.setItem(key, JSON.stringify(reserved)); } catch { /* private storage */ }
  return out;
}

export const projectSlug = (projects: Project[], p: Project): string =>
  projectSlugs(projects).get(p.id) ?? p.id;

export function collisionProjects(projects: Project[], ref: string): Project[] {
  const matches = projects.filter((p) => base(p) === norm(ref));
  return matches.length > 1 ? matches : [];
}

export function findProjectByRef(projects: Project[], ref: string): Project | undefined {
  const r = norm(ref);
  const slugs = projectSlugs(projects);
  return projects.find((p) => norm(p.id) === r) ??
    projects.find((p) => norm(slugs.get(p.id) || '') === r);
}

export function parseRoute(pathname: string): AppRoute {
  const m = /^\/p\/([^/]+)(?:\/([^/]+))?(?:\/([^/]+))?/.exec(pathname);
  if (m) {
    const view = PROJECT_VIEWS.find((v) => v === m[2]) ?? 'planning';
    let ref = m[1];
    try { ref = decodeURIComponent(ref); } catch { /* keep the raw segment */ }
    const subview = view === m[2] && m[3] ? m[3] : undefined;
    return { kind: 'project', ref, view, ...(subview ? { subview } : {}) };
  }
  if (/^\/projects\/?$/.test(pathname)) return { kind: 'projects' };
  if (/^\/pfactory\/?$/.test(pathname)) return { kind: 'pfactory' };
  if (/^\/preferences\/?$/.test(pathname)) return { kind: 'preferences' };
  return { kind: 'home' };
}

export const projectPath = (slug: string, view?: ProjectView | string, subview?: string): string =>
  `/p/${encodeURIComponent(slug)}${view && view !== 'planning' ? `/${view}${subview ? `/${subview}` : ''}` : ''}`;
