# Triage Report

_Mode: initial · Generated at 2026-05-28T15:30:00+00:00_

## Summary

| Bucket | Count |
|---|---:|
| Dedup input | 4 |
| Committed (accept) | 1 |
| Flagged | 1 |
| Skipped (operator locked) | 0 |
| Rejected | 1 |
| Dedup collisions | 1 |


## Committed

- **`ac1-login-expiry`** — `tests/test_ac1-login-expiry.py`
  - signals: coverage +7.50%, stability=stable, mutation=killed, semantic=high
  - reason: coverage +7.5%; mutation killed; semantic relevance high

## Flagged

- **`ac2-store-mut`** — `tests/test_ac2-store-mut.py`
  - signals: coverage +1.20%, stability=stable, mutation=no_mutation, semantic=medium
  - reason: mutation probe found nothing to mutate
  - reason: shallow assertion

## Skipped

_(none)_

## Rejected

- **`ac3-naive-true`** — `tests/test_ac3-naive-true.py`
  - signals: coverage +0.00%, stability=stable, mutation=survived, semantic=low
  - reason: mutation survived — assertion is tautological

## Dedup Collisions

- **whitespace_normalised**: kept `ac1-login-expiry`, dropped `ac1-login-expiry-dup`

---

_Rendered by triager-task8-commit3._
