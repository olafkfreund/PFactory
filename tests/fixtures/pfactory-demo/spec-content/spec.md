# Greeting Generator

## User Story

As a visitor on the demo page, I want to pick a category and tone and
click Generate so I see text matching my selection appear in the output
panel; clicking Clear empties it; clicking Generate again gives me a
fresh, different result.

## Acceptance Criteria

### AC#1 — Generate produces non-empty text

When the user clicks the Generate button, the output panel
(`[data-testid="output"]`) must contain non-empty text.

### AC#2 — Greeting category vocabulary

When `category = "greeting"`, the generated text must contain at least
one word from the greeting vocabulary (case-insensitive): `hello`,
`hi`, `greetings`, or `welcome`.

### AC#3 — Snarky tone vocabulary

When `tone = "snarky"`, the generated text must contain at least one
word from the snarky vocabulary (case-insensitive): `obviously`,
`whatever`, `sure`, or `fine`.

### AC#4 — Clear empties the output

When the user clicks the Clear button, the output panel
(`[data-testid="output"]`) must become empty.

### AC#5 — Different text on consecutive Generate clicks

When the user clicks Generate twice in a row with the same dropdown
selections and without clicking Clear between, the second click must
produce DIFFERENT text from the first. (This catches stale
state / caching bugs.)

## Out of Scope

- API endpoints (this is a static SPA)
- Persistence across page reloads
- Authentication / user accounts
- Localization

## Technical Notes

- SUT URL: https://olafkfreund.github.io/pfactory-demo/
- Test selectors: every interactive + assertable element exposes a
  `data-testid` attribute. Use these for stable Playwright selectors.
- Browser lane only (no API / Integration / Unit lanes apply to this
  static SPA).
