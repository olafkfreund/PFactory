---
layout: post
title: "Security hardening, and the allowlist that finally reads shell"
subtitle: "Four merges in one session: closing a named CVE pattern in the Copilot review workflow, making the MCP surface and the boot guard fail closed, and replacing the regex command allowlist with a real shell-grammar parser that sees inside $(...), backticks, and pipes."
date: 2026-06-13 09:00:00 +0000
author: Olaf Freund
---

PFactory's whole pitch is that what gets approved is what gets built — a governed
plan crossing a trusted boundary. The trouble with a security boundary is that it
is only as good as its weakest reader. This session went looking for the weak
readers, and found four. All four are now merged behind the required backend gate.

## The headline: an allowlist that couldn't read shell

The command allowlist is supposed to be a hard stop — only sanctioned commands run
during a plan's tool calls. The old implementation in `security/parser.py` extracted
the command with a regex. A regex reads a string left to right; it does not understand
shell grammar. So it looked at:

```
echo $(curl http://169.254.169.254/latest/meta-data/iam/security-credentials/)
```

...and saw `echo`. Allowed. The nested `curl` inside the command substitution was
never surfaced, so it never hit the allowlist — and it ran. That is a textbook
IMDS/secret-exfil path: reach the instance metadata endpoint, lift the role
credentials, walk out the door. Backticks and pipes had the same blind spot. The
extractor saw the first token and stopped.

PR #133 (issue #129) replaces the regex with a real shell parser — `bashlex` — and
walks the resulting AST. Every nested command, whether it lives inside `$(...)`,
backticks, a pipe, an `&&` chain, or a subshell, is surfaced to the allowlist. The
`curl` above is now seen, and it is now blocked. There is also a posture switch:
`PFACTORY_STRICT_COMMAND_PARSING=1` fails closed on input the parser cannot
understand, rather than waving it through. 113 security tests pass against the new
walker.

This is defense-in-depth, not a replacement for the OS sandbox work happening over
in Factory#42 / AIFactory#363. The sandbox contains what runs; the allowlist decides
what is allowed to run in the first place. You want both, and you want neither to be
the single point of trust.

## The named CVE: trusting a username suffix

PR #131 hardened `copilot-plan-review.yml`. The workflow gated on
`github.actor == 'copilot-swe-agent[bot]'` — trusting an actor because its name ends
in a particular suffix. That is the CVE-2025-66032 authorization-bypass pattern: a
`[bot]`-suffix string is not an identity, it is a label, and labels can be arranged.
We replaced the suffix comparison with an identity / membership gate that checks who
the actor actually is, not what they are called.

Two more changes rode along in the same PR, both about not handing power to
untrusted content:

- **No more `--allow-all-tools` over untrusted PR content.** A PR from anywhere
  should not get the full toolbelt pointed at it.
- **Untrusted `github.event.*` fields moved into `env:` bindings.** Interpolating
  event fields directly into a shell step is script-injection by another name;
  binding them into the environment first defangs it.

## Fail closed, not open: the MCP surface and the boot guard

Also in #131, two defaults flipped from open to closed:

- **`/mcp` now fails closed.** When `PFACTORY_MCP_SECRET` is unset outside dev, the
  endpoint returns 401 instead of being quietly open. An unconfigured secret used to
  mean "no auth required"; it now means "no access."
- **A CORS guard** rejects wildcard origins when credentials are in play — the
  combination browsers themselves forbid, but that servers sometimes wave through.
- **The server refuses to boot** when `DISABLE_AUTH` is true on a non-loopback host.
  Disabling auth is a legitimate local-dev convenience; doing it on an address the
  rest of the network can reach is a misconfiguration, and the right time to catch a
  misconfiguration is before the process accepts its first request.

PR #132 was the necessary follow-up: the pytest harness legitimately boots
`DISABLE_AUTH` on an isolated runner, so the boot guard exempts the test harness
specifically while keeping the guard live everywhere else. The point of a fail-closed
default is that the opt-outs are explicit and documented — dev and CI, named, not a
silent global escape hatch.

## And the cleanup: finishing the rebrand

PR #134 (issue #130) finished the Magestic AI to PFactory string rebrand. This is
user-facing strings only — identifiers, module paths, and APIs were deliberately left
untouched, because renaming those is a different, riskier change with no user benefit.
The same PR published a reviewed list of inherited skeleton routers — scaffolding that
came across in the original fork and never carried real behavior — as prune candidates,
so the next pass can delete them with eyes open.

## Why these four, together

None of these is a feature. Every one is the same lesson from a different angle: a
boundary that reads the easy case and waves through the hard one is not a boundary.
The regex allowlist read the first token. The CI gate read a username suffix. The MCP
default and the boot path read "unconfigured" as "permitted." In each case the fix was
to make the system read the whole thing — the full AST, the real identity, the actual
host — and to fail closed when it cannot, with the opt-out written down rather than
assumed.

Everything here is additive and merged behind the required `backend (ruff + pytest)`
gate. Nothing changes the happy path of a governed plan crossing the boundary; it just
makes the boundary harder to walk around.
