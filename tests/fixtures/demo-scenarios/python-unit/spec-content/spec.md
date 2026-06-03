# Pricing Helper

## User Story

As a developer who has just finished the `pricing.py` helper, I want to hand it
to PFactory so it proves the maths is covered, stable across re-runs, and
resistant to mutation before I merge — catching any place where a test passes
but doesn't actually pin the behaviour.

## Acceptance Criteria

### AC#1 — Discount is applied and clamped

`apply_discount(100, 10)` must equal `90.0`. The percentage must be clamped to
the inclusive range [0, 100]: `apply_discount(100, -5) == 100.0` and
`apply_discount(100, 150) == 0.0`.

### AC#2 — Bulk total sums discounted line items

`bulk_total` must sum every line item after applying its per-line discount.
For `[{"price": 10, "qty": 2, "pct": 0}, {"price": 20, "qty": 1, "pct": 50}]`
the result must equal `30.0`.

### AC#3 — Bulk total rounds to two decimals (HALF_UP)

`bulk_total` must round the grand total to two decimal places using HALF_UP.
For `[{"price": 0.105, "qty": 1, "pct": 0}]` the result must equal `0.11`.
(A test that asserts only the integer part, or uses `pytest.approx` with a
loose tolerance, will pass but leave the rounding rule un-pinned — the mutation
signal should catch that.)

## Out of Scope

- Currency formatting / locale
- Tax calculation
- Persistence

## Technical Notes

- Language/lane: Python — **unit** (pytest) + **mutation** (mutmut).
- Module under test: `pricing.py` (`apply_discount`, `bulk_total`).
- No HTTP target — this is a pure library; `.pfactory.yml` declares no http
  targets, only the unit + mutation lanes apply.
