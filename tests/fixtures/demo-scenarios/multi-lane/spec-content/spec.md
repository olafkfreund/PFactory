# Quote Feature

## User Story

As a developer who just finished a full-stack `/quote` feature — a Python
calculator, a React `<QuoteForm>`, and a `GET /api/quote` route — I want to hand
the whole branch to PFactory and watch a single plan fan out across the unit,
browser, and api lanes so I know every layer is covered before I merge.

## Acceptance Criteria

### AC#1 — Quote core maths (unit / pytest)

`quote(base, qty, discount_pct)` must compute the discounted line total rounded
to two decimals. `quote(10, 3)` must equal `30.0`; `quote(10, 3, 50)` must
equal `15.0`; a negative `qty` must raise `ValueError`.

### AC#2 — Form validation (unit / jest)

`<QuoteForm>` must reject invalid input before calling the API: an empty or
negative base price, and a non-integer or negative quantity, must surface
`[data-testid="quote-error"]` and must NOT fire the network request.

### AC#3 — Submit flow renders the quote (browser / playwright)

Filling base + quantity and clicking `[data-testid="quote-submit"]` must render
the computed total in `[data-testid="quote-total"]`.

### AC#4 — API returns the right JSON (api / httpx)

`GET /api/quote?base=10&qty=3&discount_pct=50` must return HTTP 200 with a JSON
body whose `total` equals `15.0` and which echoes `base`, `qty`, `discount_pct`.

## Out of Scope

- Persistence / database
- Authentication
- Currency / locale formatting

## Technical Notes

- Polyglot: Python (`quote.py`, `api_quote.py`) + TypeScript/React
  (`QuoteForm.tsx`). The Planner emits `(language, framework)` per subtask:
  `(python, pytest)`, `(typescript, jest)`, `(typescript, playwright)`,
  `(python, httpx)`.
- `.pfactory.yml` declares one http target (`web`) that serves both the UI
  (browser lane) and the `/api/quote` route (api lane).
- Test selectors: every interactive + assertable element exposes a
  `data-testid`.
