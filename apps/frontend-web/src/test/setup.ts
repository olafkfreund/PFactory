import '@testing-library/jest-dom/vitest';

// Node 26 ships its own global `localStorage` / `sessionStorage` (Web Storage).
// They are `undefined` unless the process was started with `--localstorage-file`,
// and vitest's jsdom environment does not overwrite globals Node already defines
// — so Node's undefined pair SHADOWS jsdom's and every `localStorage.getItem` in
// app code and in tests throws "Cannot read properties of undefined".
//
// Point the globals back at jsdom's window storage. A no-op on Node 24, where
// the globals do not exist and jsdom's copy is already in place.
//
// Factory#495: this was invisible until CI moved onto the Node major the image
// actually builds the shipped bundle with. The image had been building on 26
// while every test ran on 24, so nothing here had ever executed on 26.
//
// The cast is load-bearing, not decoration: lib.dom types these as `Storage`,
// never undefined, so a direct `!globalThis.localStorage` is a lint error for a
// condition the type system believes is always false. The type is wrong on Node
// 26; the cast says so once rather than suppressing the rule.
const webStorageGlobals = globalThis as unknown as Record<string, Storage | undefined>;
for (const key of ['localStorage', 'sessionStorage'] as const) {
  if (webStorageGlobals[key] === undefined) {
    Object.defineProperty(globalThis, key, { value: window[key], configurable: true });
  }
}
