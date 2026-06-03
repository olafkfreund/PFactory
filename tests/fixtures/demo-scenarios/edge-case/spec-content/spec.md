# Shipping Cost

## User Story

As a checkout service, I want `shipping_cost(weight_g)` to return the correct
flat rate for a parcel's weight bracket, so that customers are charged
correctly — especially at the **exact boundary weights** between brackets,
where off-by-one bugs hide.

## Weight brackets (grams)

| Weight (g) | Cost |
|---|---|
| 1 – 500 | $5 |
| 501 – 2000 | $10 |
| 2001 – 10000 | $20 |
| > 10000 | $50 |

## Acceptance Criteria

> Pay special attention to **boundary values** — the exact weight at the edge
> of each bracket — not just typical mid-bracket values. Use parametrised
> cases.

### AC#1 — Typical mid-bracket weights

`shipping_cost(250) == 5`, `shipping_cost(1000) == 10`,
`shipping_cost(5000) == 20`, `shipping_cost(20000) == 50`.

### AC#2 — Lower boundary: exactly 500 g is still $5

`shipping_cost(500) == 5`. (500 g is the inclusive top of the first bracket —
it must NOT roll into the $10 bracket.)

### AC#3 — Bracket-edge weights

`shipping_cost(501) == 10`, `shipping_cost(2000) == 10`,
`shipping_cost(2001) == 20`, `shipping_cost(10000) == 20`,
`shipping_cost(10001) == 50`.

### AC#4 — Non-positive weight is rejected

`shipping_cost(0)` and `shipping_cost(-5)` must raise `ValueError`.

## Out of Scope

- Currency / locale formatting
- Dimensional (volumetric) weight
- Carrier selection

## Technical Notes

- Language/lane: Python — **unit** (pytest) + **mutation** (mutmut).
- Module under test: `shipping.py` (`shipping_cost`).
- Prefer `pytest.mark.parametrize` for the boundary tables; assert exact
  integer equality (not approx).
