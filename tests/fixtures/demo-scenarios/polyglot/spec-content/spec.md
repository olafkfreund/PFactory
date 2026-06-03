# Polyglot Helpers

## User Story

As a team with a mixed Python + TypeScript codebase, I want PFactory to test
**both** languages from a single handoff: the Python `price_with_tax` helper
and the TypeScript `slugify` helper — each in its own native framework.

## Acceptance Criteria

> Two languages, two frameworks. AC#1–#2 are **Python** (`tax.py`) and must be
> generated as **pytest** tests. AC#3–#4 are **TypeScript** (`slugify.ts`) and
> must be generated as **Jest** tests (`*.test.ts`).

### AC#1 — Tax is added and rounded (Python · pytest)

`price_with_tax(1000, 8.5) == 1085` (1000¢ + 8.5% = 1085¢). `price_with_tax`
must reject a negative `cents` with `ValueError`.

### AC#2 — Tax rounds HALF_UP to a whole cent (Python · pytest)

`price_with_tax(100, 8.5) == 109` (108.5¢ rounds up to 109¢, not 108).

### AC#3 — Slugify lowercases and hyphenates (TypeScript · Jest)

`slugify("Hello World") === "hello-world"`. Leading/trailing separators are
trimmed: `slugify("  Hi!  ") === "hi"`.

### AC#4 — Slugify collapses consecutive separators (TypeScript · Jest)

A run of non-alphanumeric characters must collapse to a **single** hyphen:
`slugify("a -- b") === "a-b"` and `slugify("Café & Bar") === "cafe-bar"`.
(A slug with doubled hyphens like `a---b` is a bug.)

## Out of Scope

- Currency formatting / locale
- Unicode transliteration beyond ASCII folding
- Persistence

## Technical Notes

- Polyglot project: `tax.py` (Python) + `slugify.ts` (TypeScript).
- The Planner must emit `(python, pytest)` for the tax ACs and
  `(typescript, jest)` for the slugify ACs. Jest config (`jest.config.js`,
  ts-jest) is already present in the repo.
- Assert exact equality; for the TypeScript tests use `expect(...).toBe(...)`.
