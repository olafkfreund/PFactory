---
status: approved
issue: 786
author: Olaf Krasicki-Freund
---

# Intent: The pre-commit ratchet does not gate what CI gates

## Problem

The issue reports that `mypy --strict` regressions in `apps/web-server` pass the
pre-commit hook and are caught only by CI — twice in a row (#778, #785) — and
proposes passing CI's three `--package` flags in the hook.

**That fix cannot work, and the measurements say why.** Three separate gaps hide
behind one issue:

1. **The `--package` gap is real but ruff-only.** The hook passes
   `--package apps/backend --package apps/web-server` (`.husky/pre-commit:177`);
   CI passes those two plus `--package scripts`
   (`cq-ratchet.yml:129`). So net-new **ruff** violations in `scripts/` pass the
   hook. Worth fixing, but it is not what bit #778 or #785.
2. **The hook never runs mypy at all.** `scripts/ratchet_lint.py:503` reads
   `no_mypy = args.no_mypy or args.staged`, and the hook runs `--staged`; the
   docstring says so too ("Staged mode is ruff-only"), as does the hook's own
   comment ("the mypy half of the ratchet stays CI-only (too slow for a hook)").
   Both cited incidents were mypy errors, so **no arrangement of `--package`
   flags would have caught either**. The issue's evidence and its proposed fix
   are about different halves of the gate.
3. **"Too slow for a hook" is worth re-measuring.** `mypy --strict` on one
   web-server file with `standards/mypy.ini`: **12.0s cold, 0.4s warm**
   (incremental cache). The ratchet runs it twice per file (base and HEAD), so a
   small commit is seconds warm, not minutes.

On the title's claim — that the ratchet silently degrades in a worktree with no
`apps/backend/.venv` — measured, it is milder than it sounds: the hook falls back
to a global ruff (0.14.10 here) against the pin (0.15.17), correctly **skips**
autofix/format on the version mismatch and says so, and the ratchet's counts
agreed with the pinned ruff on all four files I sampled. The louder worktree
problem is elsewhere: the pytest step resolves no interpreter and fails hard,
which blocks the commit (hit twice in this session's own work).

## Proposed outcome

- The hook's ruff ratchet covers the same packages as CI's, so a net-new ruff
  violation in `scripts/` cannot pass locally and fail in CI.
- A developer reading the hook's output knows which half ran. The hook must not
  print a clean pass in a way that implies mypy was checked when it was not —
  that is the "control that looks like it ran" shape this repo already refuses.
- Whether mypy runs locally is decided on the measured cost rather than an
  inherited assumption, and the decision is written down.
- #786 closed with its premise corrected, so the next person does not re-file
  the same fix.

## Affected users and systems

- `.husky/pre-commit` (the hook), and whatever documents the local gate.
- Everyone committing to this repo, including agent sessions.
- Not `scripts/ratchet_lint.py`'s ratchet logic, and not CI.

## Constraints

- The hook must stay fast enough to keep. A gate developers disable is worth
  less than a gate that admits its scope.
- No change to CI's verdict: CI stays the authority.
- The worktree case must not silently weaken the gate; a degraded run says so.

## Open questions

1. **Does mypy run in the hook?** Options: (a) leave it CI-only and make the
   hook's output say plainly that mypy is not checked locally; (b) run it on
   staged files, accepting ~12s on a cold cache; (c) opt-in via an env var,
   default off. Recommendation: (a) plus (c) — the honest default costs nothing
   and someone touching typed code can opt in. (b) makes every first commit of
   a session pay 12s for a check CI repeats.
2. **Scope of this task:** just the `--package scripts` parity plus the output
   honesty, or also the worktree tooling story (a missing `.venv` currently
   fails the pytest half hard)? Recommendation: the former here; the worktree
   ergonomics deserve their own issue, since the fix is developer setup rather
   than the gate's logic.
