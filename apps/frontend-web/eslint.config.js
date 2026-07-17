// ESLint 9 flat config for the PFactory web frontend (Factory hub #261),
// mirroring AIFactory's apps/frontend-web/eslint.config.js (#647).
//
// The strict bar is typescript-eslint's `strictTypeChecked` (type-aware lint)
// plus the React Hooks rules and jsx-a11y — the shape the fleet coding
// standards aim for, mirroring AIFactory and CFactory#112.
//
// This is being adopted onto a large existing legacy frontend, so the gate is
// tuned to be GREEN at adoption WITHOUT weakening the bar for new code:
//
//   - Rules the legacy tree violates pervasively (and that are stylistic /
//     low-risk to defer) are downgraded to "warn", and CI runs with
//     `--max-warnings <baseline>` (see package.json) so net-new warnings still
//     fail the build.
//   - Genuinely important correctness rules stay at "error".
//
// TODO(Factory hub #261): ratchet the warn-listed rules below back up to "error" as the
// legacy call-sites are cleaned up, then drop --max-warnings to 0. Each is
// annotated with why it's deferred.

import js from "@eslint/js";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    // Build output, deps, generated bundles and config files are out of scope.
    ignores: [
      "dist",
      "node_modules",
      "eslint.config.js",
      "vite.config.ts",
      "vitest.config.ts",
      "postcss.config.js",
      "public",
    ],
  },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.strictTypeChecked],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser, ...globals.node },
      parserOptions: {
        // projectService type-checks every file against the nearest tsconfig
        // without enumerating projects by hand (handles the test/node split).
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
      "jsx-a11y": jsxA11y,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      ...jsxA11y.flatConfigs.recommended.rules,
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],

      // Boundary discipline: ban `as unknown as` double-casts; parse at the
      // boundary (Zod) instead. Net-new code must not reintroduce them.
      "no-restricted-syntax": [
        "warn",
        {
          selector: "TSAsExpression > TSAsExpression",
          message:
            "Avoid `as unknown as` double-casts; validate/parse at the boundary (Zod) instead.",
        },
      ],

      // --- Deferred to "warn" for the legacy tree (TODO #261: → "error") ------
      // The portal leans on template literals over backend-shaped values
      // (statuses, ids, counts) typed `unknown`/`number`/`null`; erroring would
      // touch hundreds of render call-sites at once.
      "@typescript-eslint/restrict-template-expressions": "warn",
      // Legacy `catch {}` / void-returning handlers and fire-and-forget effect
      // bodies. Low-risk; clean up incrementally.
      "@typescript-eslint/no-confusing-void-expression": "warn",
      "@typescript-eslint/no-misused-promises": "warn",
      "@typescript-eslint/no-floating-promises": "warn",
      // `??`/`||` and optional-chain tidy-ups across the legacy components.
      "@typescript-eslint/prefer-nullish-coalescing": "warn",
      "@typescript-eslint/no-unnecessary-condition": "warn",
      "@typescript-eslint/no-unnecessary-type-parameters": "warn",
      "@typescript-eslint/no-dynamic-delete": "warn",
      // `foo!` non-null assertions on values the author knows are present.
      "@typescript-eslint/no-non-null-assertion": "warn",
      // Auto-fixable, safe cleanups still present in the legacy tree.
      "@typescript-eslint/no-unnecessary-type-assertion": "warn",
      "@typescript-eslint/no-unnecessary-boolean-literal-compare": "warn",
      // The legacy adapter/store layer has many `any`-typed escape hatches and
      // untyped third-party payloads. These are the highest-count strict rules;
      // deferring them keeps the gate green while Zod boundary adoption lands.
      "@typescript-eslint/no-explicit-any": "warn",
      "@typescript-eslint/no-unsafe-assignment": "warn",
      "@typescript-eslint/no-unsafe-member-access": "warn",
      "@typescript-eslint/no-unsafe-call": "warn",
      "@typescript-eslint/no-unsafe-argument": "warn",
      "@typescript-eslint/no-unsafe-return": "warn",
      // Enum/union narrowing and base-to-string on legacy value objects.
      "@typescript-eslint/no-base-to-string": "warn",
      "@typescript-eslint/no-redundant-type-constituents": "warn",

      // --- Legacy-prevalent rules deferred to "warn" (TODO #261: → "error") ---
      // These each have dozens of pre-existing call-sites; the warning baseline
      // (--max-warnings) freezes the count so net-new violations still fail.
      // Deprecated React/lib APIs (e.g. legacy ReactDOM, deprecated props). Each
      // wants a per-call migration; tracked for follow-up.
      "@typescript-eslint/no-deprecated": "warn",
      // `async` functions with no `await` — harmless but noisy in the legacy tree.
      "@typescript-eslint/require-await": "warn",
      // eslint's unused-vars overlaps tsc's noUnusedLocals (already enforced by
      // the typecheck gate) but also flags caught-error binders etc.; keep as a
      // warning to avoid double-erroring and let the underscore convention pass.
      "@typescript-eslint/no-unused-vars": [
        "warn",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
      "@typescript-eslint/use-unknown-in-catch-callback-variable": "warn",
      "@typescript-eslint/no-unnecessary-type-conversion": "warn",
      "@typescript-eslint/unbound-method": "warn",
      "@typescript-eslint/prefer-reduce-type-parameter": "warn",
      "@typescript-eslint/no-unnecessary-template-expression": "warn",
      "@typescript-eslint/await-thenable": "warn",
      "@typescript-eslint/no-empty-object-type": "warn",
      "@typescript-eslint/no-invalid-void-type": "warn",
      "@typescript-eslint/no-extraneous-class": "warn",
      "no-control-regex": "warn",
      "prefer-const": "warn",

      // jsx-a11y: the legacy UI has many click-handlers on non-interactive
      // elements and unlabelled controls. Real a11y debt — deferred to warn so
      // it's visible and ratchetable without blocking adoption.
      "jsx-a11y/no-static-element-interactions": "warn",
      "jsx-a11y/click-events-have-key-events": "warn",
      "jsx-a11y/no-autofocus": "warn",
      "jsx-a11y/label-has-associated-control": "warn",
      "jsx-a11y/anchor-has-content": "warn",
      "jsx-a11y/heading-has-content": "warn",
    },
  },
  // Test files use vitest globals and intentionally feed malformed payloads /
  // reach into mocks; relax the type-safety rules there.
  {
    files: ["**/*.test.{ts,tsx}", "**/__tests__/**", "src/test/**"],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
    },
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unsafe-assignment": "off",
      "@typescript-eslint/no-unsafe-member-access": "off",
      "@typescript-eslint/no-unsafe-call": "off",
      "@typescript-eslint/no-unsafe-argument": "off",
      "@typescript-eslint/no-unsafe-return": "off",
      "@typescript-eslint/no-non-null-assertion": "off",
      "@typescript-eslint/no-confusing-void-expression": "off",
      "no-restricted-syntax": "off",
    },
  },
);
