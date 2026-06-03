"""Tax helper (Python side of the polyglot demo)."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def price_with_tax(cents: int, rate_pct: float) -> int:
    """Return ``cents`` plus tax at ``rate_pct`` percent, rounded HALF_UP to a
    whole cent."""
    if cents < 0:
        raise ValueError("cents must be non-negative")
    base = Decimal(cents)
    total = base + base * Decimal(str(rate_pct)) / Decimal(100)
    return int(total.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
