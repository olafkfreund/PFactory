---
layout: post
title: "Your test suite is green. It's also lying to you."
subtitle: "A test that runs but never fails isn't a test — it's a decoration. Here's how mutation testing catches the fakes."
date: 2026-05-31 14:00:00
author: DataSeek Team
---

Picture the most reassuring number in software: **100% coverage, all green.**
Now here's the uncomfortable bit — that number tells you which lines *executed*,
not whether a single assertion would notice if you broke them.

You can write this test and hit every line:

```python
def test_discount():
    apply_discount(100, 0.2)   # ...and assert nothing
```

Green. Covered. Useless. It would happily pass while `apply_discount` returns
the wrong number, raises, or quietly catches fire.

## Enter the saboteur

Mutation testing flips the question. Instead of asking *"did the test run?"* it
asks *"if I deliberately break the code, does the test scream?"*

It introduces a tiny **mutant** — change a `>` to `>=`, flip a `+` to a `-`,
swap a boolean — and re-runs your test. Two outcomes:

- **KILLED** 🟢 — the test failed on the mutant. Good. It's actually watching.
- **SURVIVED** 🔴 — the test passed anyway. Your "test" didn't notice the bug.
  It's decoration.

A suite full of survivors is coverage theatre: looks busy, catches nothing.

## Why PFactory bakes it in

When an AI generates tests, this failure mode gets *worse*, not better — models
are very good at producing plausible-looking assertions that don't actually
pin anything down. So we don't trust generated tests on coverage. **Mutation is
one of the five signals in every verdict.** A generated test that survives its
mutant gets flagged, not merged — no matter how green it looks.

In the portal you'll see it as a blunt little chip on each test card:
`MUTATION killed` or `MUTATION survived`. No percentage to hide behind.

## The honest version of "tested"

We're not anti-coverage — it's a fine smoke alarm for *un*-tested code. We're
against treating it as proof of quality. "Tested" should mean *"if this breaks,
something turns red."* Mutation is the cheapest way to actually check that.

So: run your mutants. Most teams discover a third of their suite is napping.
Better to find out from a saboteur you hired than from production.

Curious how the other four signals stack up? The
[architecture page](/architecture/) walks the whole verdict, and the
[demos](/demos/) show a real run scoring pass *and* fail.
