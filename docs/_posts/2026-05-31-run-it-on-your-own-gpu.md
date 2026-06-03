---
layout: post
title: "Run the whole thing on your own GPU (yes, with the Wi-Fi off)"
subtitle: "If your code can't leave the building, your test generator shouldn't phone home either. PFactory runs fully on local models."
date: 2026-05-31 11:00:00
author: DataSeek Team
---

A fun question to ask any "AI for your codebase" tool: *where does my code go?*
For a lot of teams — banks, hospitals, anyone with a compliance officer who owns
a red pen — the only acceptable answer is **nowhere**. Their code does not leave
the network. Full stop.

That rules out most AI test tools, which are thin wrappers around a single
hosted API. PFactory isn't.

## Bring your own brain

PFactory picks its LLM provider from the *model string*, and the list is
deliberately long: Claude, Codex, Gemini, GitHub Copilot — and, crucially, the
local crowd: **Ollama, vLLM, LM Studio, LocalAI**, and anything that speaks the
OpenAI-compatible protocol. Point it at a model running on the box under your
desk and the entire pipeline — Planner, Gen-Functional, Evaluator, Triager —
runs against *that*. No token leaves the room.

```bash
# the whole pipeline, on a local Qwen, no internet required
TF_MODEL=ollama:qwen2.5-coder:14b   pfactory handover --spec 001
```

## "Air-gapped" as a feature, not an apology

This isn't a degraded mode you tolerate. It's a first-class path:

- The **Docker sandbox** that runs the generated tests already defaults to
  `--network=none` — hermetic by design.
- The **credential broker** has a `sops`/`age`/`agenix` backend so even your
  secrets stay as local encrypted files.
- There's an honest **"🔒 Local — no data egress"** badge that only lights up
  when the run genuinely keeps everything on your network — and a one-line CLI
  check (`python -m apps.backend.byo_llm <model>`) that exits non-zero if it
  wouldn't.

So you can *prove* it to the person with the red pen, not just claim it.

## The boring truth about local models

They're not as sharp as the frontier hosted ones — a 14B model plans a feature
more conservatively than Claude does. But "runs entirely on our hardware,
verifiably, with no egress" is worth a lot more than a few extra IQ points to
the teams who need it. And the gap shrinks every month.

If that's you, the [BYO-LLM guide](/credentials/) has the provider config, and
the architecture page explains how the model string routes to a provider.
