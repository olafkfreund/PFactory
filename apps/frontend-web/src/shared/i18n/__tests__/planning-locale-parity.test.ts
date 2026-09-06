/**
 * The planning namespaces must exist in every locale, not just `en`.
 *
 * i18next falls back to English for a missing key, so a forgotten `fr` or
 * `pt-BR` entry renders correctly-looking English to a French user, and no test
 * that renders a component in English can see it. Comparing the key SETS is the
 * only place that drift is visible.
 */
import { describe, it, expect } from 'vitest';

import en from '../locales/en/common.json';
import fr from '../locales/fr/common.json';
import ptBR from '../locales/pt-BR/common.json';

const NAMESPACES = ['planEditor', 'annotations'] as const;

function keysOf(bundle: Record<string, unknown>, ns: string): string[] {
  return Object.keys((bundle[ns] ?? {}) as Record<string, unknown>).sort();
}

describe.each([
  ['fr', fr as Record<string, unknown>],
  ['pt-BR', ptBR as Record<string, unknown>],
])('%s', (_locale, bundle) => {
  it.each(NAMESPACES)('defines every %s key that en does', (ns) => {
    expect(keysOf(bundle, ns)).toEqual(keysOf(en as Record<string, unknown>, ns));
  });
});
