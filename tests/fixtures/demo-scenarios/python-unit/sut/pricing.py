"""Pricing helpers for the PFactory `python-unit` demo SUT.

A small, *finished* feature a developer hands to PFactory to prove the maths is
covered, stable, and mutation-resistant before merge. The module is correct;
the demo's teaching moment is that a naive generated test for ``bulk_total``'s
rounding under-asserts, so the Evaluator's mutate-and-check signal surfaces a
SURVIVED mutant and the Triager returns a ``flag`` verdict.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Mapping


def apply_discount(price: float, pct: float) -> float:
    """Return ``price`` reduced by ``pct`` percent.

    ``pct`` is clamped to the inclusive range [0, 100] so callers can't push a
    price negative or inflate it with a negative discount.
    """
    clamped = max(0.0, min(100.0, pct))
    return round(price * (1.0 - clamped / 100.0), 2)


def bulk_total(items: Iterable[Mapping[str, float]]) -> float:
    """Sum line items, each ``{"price": float, "qty": int, "pct": float}``.

    Per-line discount is applied before summing; the grand total is rounded to
    two decimal places using HALF_UP (banker-safe for currency).
    """
    total = Decimal("0")
    for item in items:
        price = Decimal(str(item["price"]))
        qty = Decimal(str(item.get("qty", 1)))
        pct = Decimal(str(item.get("pct", 0)))
        pct = max(Decimal("0"), min(Decimal("100"), pct))
        line = price * qty * (Decimal("1") - pct / Decimal("100"))
        total += line
    return float(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
