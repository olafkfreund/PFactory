import { describe, it, expect } from 'vitest';
import { findProjectByRef, parseRoute, projectPath, projectSlugs, slugify } from '../project-route';
import type { Project } from '../../shared/types';

const proj = (id: string, name: string, createdAt: string, slug?: string): Project =>
  ({ id, name, slug, path: `/p/${id}`, createdAt: new Date(createdAt) }) as unknown as Project;

describe('slugify', () => {
  it('normalizes and falls back to the id', () => {
    expect(slugify('My  Project_1!', 'x')).toBe('my-project-1');
    expect(slugify('Café', 'x')).toBe('cafe');
    expect(slugify('日本', 'abc123')).toBe('abc123');
  });
});

describe('projectSlugs', () => {
  it('oldest keeps the plain slug; newcomers cannot steal it', () => {
    const a = proj('aaaaaaaa-1', 'Demo', '2026-01-01');
    const b = proj('bbbbbbbb-2', 'Demo', '2026-02-01');
    expect(projectSlugs([b, a]).get(a.id)).toBe('demo');
    expect(projectSlugs([b, a]).get(b.id)).toBe('demo-bbbbbbbb');
  });
  it('final slugs are unique even for adversarial names', () => {
    const ps = [
      proj('aaaaaaaa-1', 'Demo', '2026-01-01'),
      proj('bbbbbbbb-2', 'Demo', '2026-02-01'),
      proj('cccccccc-3', 'Demo bbbbbbbb', '2026-03-01'),
    ];
    const slugs = [...projectSlugs(ps).values()];
    expect(new Set(slugs).size).toBe(3);
  });
  it('backend slugs win and are reserved first', () => {
    const a = proj('a1', 'Demo', '2026-01-01');
    const b = proj('b2', 'Other', '2026-02-01', 'demo');
    expect(projectSlugs([a, b]).get('b2')).toBe('demo');
    expect(projectSlugs([a, b]).get('a1')).not.toBe('demo');
  });
  it('uses id order for equal creation dates and retries a taken suffix', () => {
    const a = proj('aaaaaaaa-1', 'Demo', '2026-01-01');
    const b = proj('bbbbbbbb-2', 'Demo', '2026-01-01');
    const c = proj('c', 'Other', '2026-01-01', 'demo-bbbbbbbb');
    expect(projectSlugs([b, c, a]).get(a.id)).toBe('demo');
    expect(projectSlugs([b, c, a]).get(b.id)).toBe('demo-bbbbbbbb-2');
  });
});

describe('findProjectByRef', () => {
  const a = proj('id-a', 'Alpha App', '2026-01-01');
  const b = proj('id-b', 'Beta', '2026-01-02', 'beta-x');
  it('resolves canonical slug, id and alias', () => {
    expect(findProjectByRef([a, b], 'alpha-app')?.id).toBe('id-a');
    expect(findProjectByRef([a, b], 'ID-A')?.id).toBe('id-a');
    expect(findProjectByRef([a, b], 'beta-x')?.id).toBe('id-b');
    expect(findProjectByRef([a, b], 'beta')?.id).toBe('id-b'); // derived alias
    expect(findProjectByRef([a, b], 'nope')).toBeUndefined();
  });
});

describe('findProjectByRef collision aliases', () => {
  it('a suffixed slug keeps resolving after the older twin is removed', () => {
    const a = proj('aaaaaaaa-1', 'Demo', '2026-01-01');
    const b = proj('bbbbbbbb-2', 'Demo', '2026-02-01');
    expect(findProjectByRef([a, b], 'demo-bbbbbbbb')?.id).toBe(b.id);
    expect(findProjectByRef([b], 'demo-bbbbbbbb')?.id).toBe(b.id); // now canonical 'demo'
  });
  it('prefers a canonical slug over an id or derived alias', () => {
    const a = proj('id-a', 'Alpha', '2026-01-01');
    const b = proj('alpha', 'Beta', '2026-01-02');
    const c = proj('id-c', 'Other', '2026-01-03', 'beta');
    expect(findProjectByRef([a, b], 'alpha')?.id).toBe(a.id);
    expect(findProjectByRef([b, c], 'beta')?.id).toBe(c.id);
  });
});

describe('parseRoute', () => {
  it('parses project routes and defaults the view', () => {
    expect(parseRoute('/p/alpha')).toEqual({ kind: 'project', ref: 'alpha', view: 'planning' });
    expect(parseRoute('/p/alpha/terminals')).toEqual({ kind: 'project', ref: 'alpha', view: 'terminals' });
    expect(parseRoute('/p/alpha/editor')).toEqual({ kind: 'project', ref: 'alpha', view: 'editor' });
    expect(parseRoute('/p/alpha/pfactory')).toEqual({ kind: 'project', ref: 'alpha', view: 'pfactory' });
    expect(parseRoute('/p/alpha/cloud')).toEqual({ kind: 'project', ref: 'alpha', view: 'cloud' });
    expect(parseRoute('/p/alpha/bogus')).toEqual({ kind: 'project', ref: 'alpha', view: 'planning' });
    expect(parseRoute('/p/a%20b')).toMatchObject({ ref: 'a b' });
    expect(parseRoute('/p/%E0%A4%A')).toMatchObject({ kind: 'project' });
  });
  it('parses the other routes', () => {
    expect(parseRoute('/projects')).toEqual({ kind: 'projects' });
    expect(parseRoute('/projects/')).toEqual({ kind: 'projects' });
    expect(parseRoute('/')).toEqual({ kind: 'home' });
    expect(parseRoute('/whatever')).toEqual({ kind: 'home' });
  });
  it('projectPath round-trips', () => {
    expect(projectPath('a b', 'terminals')).toBe('/p/a%20b/terminals');
    expect(projectPath('x')).toBe('/p/x');
    expect(projectPath('x', 'planning')).toBe('/p/x');
  });
});
