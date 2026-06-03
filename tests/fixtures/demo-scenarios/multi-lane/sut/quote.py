"""Quote calculator — the Python core of the `multi-lane` demo SUT.

Shared by the API route (`api_quote.py`) and exercised directly by the unit
lane. Deliberately small and correct: this scenario sells the *breadth* of the
v0.2 5-lane spine (one spec fanning out to pytest + Jest + Playwright + httpx),
not a seeded failure.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def quote(base: float, qty: int, discount_pct: float = 0.0) -> float:
    """Return the total price for ``qty`` units of a ``base``-priced item.

    ``discount_pct`` is clamped to [0, 100] and applied to the line total. The
    result is rounded to two decimal places (HALF_UP).
    """
    if qty < 0:
        raise ValueError("qty must be non-negative")
    pct = max(0.0, min(100.0, discount_pct))
    line = Decimal(str(base)) * Decimal(str(qty)) * (Decimal("1") - Decimal(str(pct)) / Decimal("100"))
    return float(line.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
