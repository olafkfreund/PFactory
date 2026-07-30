import '@testing-library/jest-dom/vitest';

// Web Storage for Node 26 (Factory#495).
//
// Node 26 ships its own global `localStorage` / `sessionStorage`. Both are the
// plain value `undefined` unless the process was started with
// `--localstorage-file`, and vitest's jsdom environment does not replace globals
// Node has already defined — so every `localStorage.getItem` in app code and in
// tests throws "Cannot read properties of undefined". 147 tests here, 117 in
// TFactory, on the first CI run that used the image's Node major.
//
// Measured under Node 26.5 rather than assumed: `globalThis === window ===
// document.defaultView`, and `localStorage` is `undefined` on all three. There
// is no jsdom Storage hiding behind `window` to re-point the global at, so this
// installs one. `--localstorage-file` is not an option: it is file-backed and
// shared, which would leak state between test files.
//
// A Map is enough. vitest isolates per test file, so each file gets a fresh
// store, which is what the suites already assume (`localStorage.clear()` in
// beforeEach). No-op on Node 24, where jsdom's own Storage is in place.
//
// The cast is load-bearing: `lib.dom` types these globals as `Storage` and never
// undefined, so a direct `!globalThis.localStorage` is a lint error for a
// condition the type system believes can never be true. The type is wrong on
// Node 26; the cast says so once instead of suppressing the rule at each use.
const webStorageGlobals = globalThis as unknown as Record<string, Storage | undefined>;

function memoryStorage(): Storage {
  const items = new Map<string, string>();
  return {
    get length() {
      return items.size;
    },
    clear: () => items.clear(),
    getItem: (key: string) => items.get(key) ?? null,
    key: (index: number) => Array.from(items.keys()).at(index) ?? null,
    removeItem: (key: string) => void items.delete(key),
    setItem: (key: string, value: string) => void items.set(key, value),
  };
}

for (const key of ['localStorage', 'sessionStorage'] as const) {
  if (webStorageGlobals[key] === undefined) {
    Object.defineProperty(globalThis, key, {
      value: memoryStorage(),
      configurable: true,
      writable: true,
    });
  }
}
