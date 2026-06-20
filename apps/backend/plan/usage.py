"""Token-usage accumulator for a plan run (#60, RFC-0001 v1.1).

CFactory's *Tokens & cost* page reads an additive ``usage`` block off PFactory's
completion event (see :mod:`plan.completion`). This module is the small,
zeros-safe accumulator the pipeline folds LLM-call usage into, plus a tolerant
normalizer that pulls usage out of whatever shape a provider hands back (an
Anthropic-style ``Message`` with a ``.usage`` object, a plain dict, etc.).

The Plan pipeline is deterministic by default — most stages run without an LLM,
so the accumulator stays at zero and the emitted block is honestly zero. When an
LLM seam *is* supplied (e.g. ``decompose_with_llm``), its usage is recorded here
and surfaces as real numbers on the completion event. Additive and optional: no
schema break.
"""

from __future__ import annotations

from pydantic import BaseModel

# Per-million-token USD pricing for known models, used to derive ``cost_usd``
# only when a usage source did not already supply a cost. Kept deliberately
# small; an unknown model simply yields cost 0.0 (never a guessed number).
_PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    # model id (prefix-matched): (input $/Mtok, output $/Mtok)
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4": (1.0, 5.0),
    "claude-3-5-sonnet": (3.0, 15.0),
    "claude-3-5-haiku": (0.8, 4.0),
    "claude-3-opus": (15.0, 75.0),
}


def _price_for(model: str) -> tuple[float, float] | None:
    """Return ``(input, output)`` $/Mtok for ``model`` by longest-prefix match."""
    if not model:
        return None
    best: tuple[int, tuple[float, float]] | None = None
    for prefix, price in _PRICE_PER_MTOK.items():
        if model.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), price)
    return best[1] if best else None


def estimate_cost_usd(input_tokens: int, output_tokens: int, model: str) -> float:
    """Best-effort cost from a known price table; 0.0 when the model is unknown."""
    price = _price_for(model)
    if price is None:
        return 0.0
    in_rate, out_rate = price
    return round((input_tokens * in_rate + output_tokens * out_rate) / 1_000_000, 6)


class PlanUsage(BaseModel):
    """Accumulated token usage + cost for one plan run."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, other: PlanUsage | None) -> None:
        """Fold another usage record in (no-op for ``None``)."""
        if other is None:
            return
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost_usd = round(self.cost_usd + other.cost_usd, 6)
        # Keep the first non-empty model as the dominant id for the run.
        if other.model and not self.model:
            self.model = other.model

    def as_event_block(self) -> dict:
        """The additive ``usage`` block for the completion envelope (RFC-0001)."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "model": self.model,
        }


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def usage_from_obj(obj: object) -> PlanUsage | None:
    """Tolerantly normalize a provider response into a :class:`PlanUsage`.

    Handles the common shapes:

    * a plain ``dict`` with ``input_tokens`` / ``output_tokens`` (optionally
      nested under a ``"usage"`` key), plus optional ``cost_usd`` / ``model``;
    * an object exposing a ``.usage`` attribute (Anthropic-style ``Message``)
      whose ``.input_tokens`` / ``.output_tokens`` we read, with ``.model`` off
      the parent; or such a usage object directly;
    * an object exposing ``.last_usage`` (an adapter recording its most recent
      call), recursed into.

    Returns ``None`` when no token counts can be found, so callers can fold the
    result unconditionally via :meth:`PlanUsage.add`. ``cost_usd`` is derived
    from the price table only when not already supplied.
    """
    if obj is None:
        return None

    # dict shape — possibly nested under "usage".
    if isinstance(obj, dict):
        data = obj.get("usage") if isinstance(obj.get("usage"), dict) else obj
        if not isinstance(data, dict):
            return None
        in_tok = _as_int(data.get("input_tokens") or data.get("prompt_tokens"))
        out_tok = _as_int(data.get("output_tokens") or data.get("completion_tokens"))
        if in_tok == 0 and out_tok == 0:
            return None
        model = str(obj.get("model") or data.get("model") or "")
        cost = data.get("cost_usd", obj.get("cost_usd"))
        cost_usd = float(cost) if cost is not None else estimate_cost_usd(in_tok, out_tok, model)
        return PlanUsage(input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost_usd, model=model)

    # adapter that records its most recent call.
    last = getattr(obj, "last_usage", None)
    if last is not None and last is not obj:
        return usage_from_obj(last)

    # object exposing a nested ``.usage`` (Anthropic-style Message).
    usage = getattr(obj, "usage", None)
    model = str(getattr(obj, "model", "") or "")
    src = usage if usage is not None else obj
    in_tok = _as_int(getattr(src, "input_tokens", 0))
    out_tok = _as_int(getattr(src, "output_tokens", 0))
    if in_tok == 0 and out_tok == 0:
        return None
    if not model:
        model = str(getattr(src, "model", "") or "")
    cost = getattr(src, "cost_usd", None)
    cost_usd = float(cost) if cost is not None else estimate_cost_usd(in_tok, out_tok, model)
    return PlanUsage(input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost_usd, model=model)
