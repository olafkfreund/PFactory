/**
 * A `useTranslation` mock that resolves against the REAL `en` resource.
 *
 * The house mock returns the key itself, which lets a component ask for a key
 * no locale defines and still render — the test passes on a string that would
 * never reach a user. Looking the key up for real makes a missing or misspelt
 * key fail here instead of in the browser.
 */
import en from '../../../shared/i18n/locales/en/common.json';

type Vars = Record<string, unknown> | undefined;

function lookup(key: string): unknown {
  return key
    .split('.')
    .reduce<unknown>((acc, k) => (acc as Record<string, unknown> | undefined)?.[k], en);
}

export function translate(key: string, vars?: Vars): string {
  // i18next selects the `_plural` form for any count that is not exactly 1.
  const count = vars?.count;
  const plural = typeof count === 'number' && count !== 1 ? lookup(`${key}_plural`) : undefined;
  const raw = typeof plural === 'string' ? plural : lookup(key);
  if (typeof raw !== 'string') throw new Error(`missing en translation for '${key}'`);
  return raw.replace(/\{\{(\w+)\}\}/g, (_m, name: string) => String(vars?.[name] ?? ''));
}
