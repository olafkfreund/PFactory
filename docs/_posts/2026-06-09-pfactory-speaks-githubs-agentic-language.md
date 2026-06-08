---
layout: post
title: "PFactory speaks GitHub's agentic language"
subtitle: "Free GitHub Models inference, Copilot cloud-agent delegation, an MCP server that hands the plan to whoever is building it, and the Actions that wire it all together — PFactory now plugs straight into GitHub's native agentic surface, opt-in and additive."
date: 2026-06-09
author: DataSeek Team
---

A governance gate is only useful if it sits where the work already happens. In
2026 a growing share of that work happens on **GitHub's agentic surface** —
free model inference inside Actions, a Copilot cloud agent that codes from an
issue, and MCP servers that let those agents call home for context. Today
PFactory learned to speak all of it.

The shipped epic — *GitHub Agentic Integration* ([#87](https://github.com/olafkfreund/PFactory/issues/87)) —
is four independently useful components, every one **opt-in and additive**.
Nothing changes until you turn it on; absent the new config, PFactory behaves
exactly as before.

### 1 · Free inference with GitHub Models

PFactory's planning phases — decompose, enrich, the review lenses — burn tokens.
GitHub Models gives you OpenAI-compatible inference at **zero extra billing**
inside Actions, authed by the `GITHUB_TOKEN` you already have.

So `github-models/openai/gpt-4.1` is now a first-class model string. It routes
through PFactory's existing `openai-compatible` provider with the
`models.github.ai` endpoint and token injected automatically — *no new provider
class, no new secret.* The one subtlety we were careful about: the resolver
recognises the `github-models/` prefix **ahead of** the `gpt-`/codex check, so
`github-models/openai/gpt-4.1` is never mistaken for Codex. And we deliberately
did **not** claim the bare `github` alias — that name already belongs to the
`gh` integration threaded through the codebase, and shadowing it would have been
a quiet footgun.

### 2 · Delegate the draft to Copilot

Label a planning issue `copilot:delegate` and PFactory assigns it to GitHub's
Copilot cloud agent (`copilot-swe-agent[bot]`). The agent drafts the plan — a
requirements doc, a decomposition, a Task Contract v2 skeleton — and opens a PR.
PFactory's governance gates then review *that*.

It reuses the `gh` token (no new PAT), it is off by default
(`PFACTORY_COPILOT_DISPATCH_ENABLED`), and — importantly — when dispatch can't
run it **fails loud, not silent**: the call surfaces the reason and the caller
falls back to the normal flow. A label that gets quietly ignored is worse than
one that errors.

### 3 · An MCP server that hands over the plan

This is the piece that makes the rest matter. PFactory now exposes its planning
outputs as an **HTTP MCP server** at `POST /mcp` (JSON-RPC 2.0), so the Copilot
cloud agent — or AIFactory, or TFactory — can retrieve the plan *while it is
building from it*. Five tools:

- `pfactory_get_epic` — the epic decomposition
- `pfactory_get_requirements` — normalised requirements + acceptance criteria
- `pfactory_get_decomposition` — the child-issue dependency graph
- `pfactory_get_task_contract` — the signed **Task Contract v2** payload
- `pfactory_get_review_status` — the governance gate status

Look it up by the emitted GitHub issue number or by session id; auth is a bearer
token (`PFACTORY_MCP_SECRET`). This is the runtime bridge for **RFC-0002 Task
Contract v2**: the executing agent fetches the *signed* contract instead of
re-deriving the plan. Plan once, build from the plan everywhere.

### 4 · The Actions that wire it together

Two workflows close the loop. A `pfactory:run` label on an issue ingests it as a
plan. A PR opened by the Copilot agent triggers PFactory's plan-review gates and
posts the gate summary back as a comment — and, true to PFactory's
**no-automatic-pushes** rule, that comment is *opt-in*: by default the rendered
summary is returned for the workflow to post, never pushed behind your back.

### The part we're proudest of: we didn't merge it red

The honest footnote. When the PR was ready, CI came back **red** — a `ruff`
import-sort error in the new test files. GitHub would have let us merge anyway
(the failing checks weren't marked required, so the merge was *mergeable, but
unstable*). We didn't. We stopped, found that the pre-commit hook only lints
staged `apps/backend/` files while CI lints the whole tree, fixed the lint,
watched every check go green — backend, the fast gate, and the P0–P7 acceptance
suite — and *then* merged. Along the way we also unblocked the local test venv
(a few already-declared dependencies that were never installed) and made the
commit-message hook executable so it actually runs.

That is the same standard PFactory asks of every plan it governs: be
discoverable, be documented, be **verified before you ship**. A governance gate
that waves its own work through hasn't earned the right to gate anyone else's.

---

*Everything here is additive and off by default. The full operator guide —
provider strings, dispatch flow, the GitHub `Settings → Copilot → MCP servers`
config snippet, and the workflow secrets — lives in
[`guides/github-agentic-integration.md`](https://github.com/olafkfreund/PFactory/blob/main/guides/github-agentic-integration.md).*
