"""Promotion gates from scratchpad signals to gotchas (RFC-0021 Phase 4).

Cheapest first. Deterministic: no LLM call, so the call count cannot rise.
``dead_end`` signals are never promoted here; they are recalled directly.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import retention, scratchpad, trust

logger = logging.getLogger(__name__)

MIN_COUNT = 2

Signal = dict[str, Any]


@dataclass
class Context:
    existing: set[str]  # stripped bodies already in gotchas.md
    cap: int


def render(signal: Signal) -> str:
    """The stored form: fixed sentence, headings escaped."""
    kind = str(signal["kind"]).replace("_", " ")
    text = f"Recurring {kind} (seen {signal['n']} times): {signal['text']}"
    return retention.escape_headings(text).strip()


def validation(
    signals: list[Signal],
    ctx: Context,  # noqa: ARG001 - uniform gate signature
) -> list[Signal]:
    """Drop signals whose every observation belongs to an abandoned approach."""
    return [s for s in signals if int(s["n"]) - int(s.get("ab", 0)) >= 1]


def frequency(
    signals: list[Signal],
    ctx: Context,  # noqa: ARG001 - uniform gate signature
) -> list[Signal]:
    """One occurrence is an anecdote."""
    return [s for s in signals if int(s["n"]) >= MIN_COUNT]


def novelty(signals: list[Signal], ctx: Context) -> list[Signal]:
    """Exact repeat of an existing gotcha, or already promoted."""
    return [s for s in signals if not s.get("promoted") and render(s) not in ctx.existing]


def trust_gate(
    signals: list[Signal],
    ctx: Context,  # noqa: ARG001 - uniform gate signature
) -> list[Signal]:
    """Any observation made inside the untrusted window disqualifies the key."""
    return [s for s in signals if int(s.get("bad", 0)) == 0]


def score_cap(signals: list[Signal], ctx: Context) -> list[Signal]:
    ranked = sorted(signals, key=lambda s: (int(s["n"]), str(s["last"])), reverse=True)
    return ranked[: max(ctx.cap, 0)]


GATES: list[Callable[[list[Signal], Context], list[Signal]]] = [
    validation,
    frequency,
    novelty,
    trust_gate,
    score_cap,
]


def promote(
    memory_dir: Path,
    session_type: str | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Run the gates over the scratchpad and append survivors to gotchas.md.

    Never raises; returns the bodies written.
    """
    try:
        store = scratchpad.read(memory_dir)
        signals = [
            {**entry, "kind": kind, "key": key}
            for kind, entries in store.items()
            if kind != "dead_end"
            for key, entry in entries.items()
        ]
        gotchas = memory_dir / "gotchas.md"
        text = gotchas.read_text() if gotchas.exists() else ""
        ctx = Context(
            existing={
                trust.strip_marker(body.strip()) for _, body, _ in retention.split_gotchas(text)
            },
            cap=retention.CAPS[retention.session_type(session_type)],
        )
        for gate in GATES:
            signals = gate(signals, ctx)
        if not signals:
            return []
        stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M")
        bodies = [render(s) for s in signals]
        with gotchas.open("a") as f:
            if not text:
                f.write("# Gotchas & Pitfalls\n\nThings to watch out for in this codebase.\n")
            f.write("".join(f"\n## [{stamp}]\n{body}\n" for body in bodies))
        for s in signals:
            store[s["kind"]][s["key"]]["promoted"] = True
        scratchpad.write(memory_dir, store)
        retention.confirm(memory_dir, bodies)
    except Exception:  # noqa: BLE001 - promotion must never fail a session save
        logger.warning("memory promotion failed in %s", memory_dir, exc_info=True)
        return []
    return bodies
