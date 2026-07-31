import '@testing-library/jest-dom/vitest';
import { JSDOM } from 'jsdom';

// Web Storage under Node 26 (Factory#495).
//
// Node 26 ships its own global `localStorage` / `sessionStorage`, and both are
// `undefined` unless the process was started with `--localstorage-file`. Inside
// vitest's jsdom environment that undefined global is what app and test code
// see, so every `localStorage.getItem` throws "Cannot read properties of
// undefined" — 147 tests in PFactory, 117 here, on the first CI run that used
// the Node major the image already builds the shipped bundle with.
//
// WHERE THE FAULT IS NOT. Measured under Node 26.5, not assumed:
//
//   * jsdom is fine. jsdom 27.4.0 on Node 26 builds a working Storage and
//     round-trips it (`new JSDOM(...).window.localStorage` set/get). A jsdom
//     bump therefore fixes nothing — it is already correct.
//   * There is nothing to re-point the global at. Inside the environment
//     `globalThis === window === document.defaultView`, and `localStorage` is
//     `undefined` on all three — as a plain data property, where Node's own is
//     an accessor. vitest's global population and Node's new global do not
//     compose, and the working jsdom Storage never reaches the global.
//   * `--localstorage-file` is not the answer either: it is file-backed and
//     process-wide, so it would leak state between test files.
//
// So borrow a Storage from a throwaway jsdom window: real jsdom Storage with
// real semantics, out of a devDependency this repo already declares, rather
// than a hand-rolled Map that would drift from the spec. vitest isolates per
// test file, so each file gets a clean store — which is what these suites
// already assume (`localStorage.clear()` in beforeEach). Measured cost of the
// extra window: none detectable across a 28-file run.
//
// No-op on Node 24, which has no such global and gets jsdom's Storage normally.
//
// The cast is load-bearing: `lib.dom` types these globals as `Storage`, never
// undefined, so a direct `!globalThis.localStorage` is a lint error for a
// condition the type system believes can never be true. The type is wrong on
// Node 26; the cast says so once rather than suppressing the rule at each use.
const webStorageGlobals = globalThis as unknown as Record<string, Storage | undefined>;
if (webStorageGlobals.localStorage === undefined) {
  const donor = new JSDOM('', { url: 'http://localhost/' }).window;
  for (const key of ['localStorage', 'sessionStorage'] as const) {
    Object.defineProperty(globalThis, key, {
      value: donor[key],
      configurable: true,
      writable: true,
    });
  }
}
