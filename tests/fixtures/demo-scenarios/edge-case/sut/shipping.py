"""Shipping-cost calculator — the edge-case/boundary demo SUT.

Weight brackets (grams):
    0   – 500     → $5
    501 – 2000    → $10
    2001 – 10000  → $20
    > 10000       → $50

Seeded boundary bug (AC#2): the first bracket uses a STRICT ``< 500`` check, so
exactly 500 g falls through to the $10 bracket instead of staying at $5. A
boundary test at 500 g catches it; the Triager surfaces a reject.
"""
from __future__ import annotations


def shipping_cost(weight_g: int) -> int:
    if weight_g <= 0:
        raise ValueError("weight_g must be positive")
    if weight_g < 500:           # BUG: should be <= 500
        return 5
    if weight_g <= 2000:
        return 10
    if weight_g <= 10000:
        return 20
    return 50
