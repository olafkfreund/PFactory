---
status: draft
issue: 786
intent: intent/2026-09-29-786-hook-ratchet-parity.md
---

# Spec: The pre-commit ratchet does not gate what CI gates

## Facts established

- Hook: `scripts/ratchet_lint.py --staged --package apps/backend --package
  apps/web-server` (`.husky/pre-commit:177`). CI: the same two plus
  `--package scripts` (`cq-ratchet.yml:129`).
- `no_mypy = args.no_mypy or args.staged` (`ratchet_lint.py:503`) — the hook's
  run is ruff-only, which is why #778/#785 (mypy) passed it.
- The success line hides that: `suffix = "" if no_mypy else " (ruff + mypy)"`
  (line 545), so a staged run prints "ratchet PASSED: no changed file
  regressed; new violations: none." — indistinguishable from a full pass.
- `mypy --strict` on one web-server file: 12.0s cold, 0.4s warm.
- In staged mode `base = "HEAD"`, so a mypy base count would compare against
  HEAD — right. But `mypy_errors()` type-checks the file **on disk**, not the
  staged content, so with a partially staged file its verdict describes the
  working tree, not what is being committed. The ruff half has no such gap
  (`regressions(..., staged=True)`).

## Design

Three small changes; no ratchet logic is rewritten.

### 1. Parity for the half that runs (ruff)

`.husky/pre-commit:177` gains `--package scripts`, matching CI. One line.

### 2. The success line names the half that ran

`ratchet_lint.py:545`:

    suffix = " (ruff only; mypy runs in CI)" if no_mypy else " (ruff + mypy)"

So a hook pass reads "ratchet PASSED: no changed file regressed (ruff only;
mypy runs in CI)". The developer who reads it knows what was not checked —
the intent's requirement, and it costs nothing.

### 3. Opt-in mypy, default off (intent Q1)

- `ratchet_lint.py`: a `--mypy` flag that overrides the staged default:
  `no_mypy = args.no_mypy or (args.staged and not args.mypy)`. `--mypy` with
  `--no-mypy` is a parser error (contradictory).
- `.husky/pre-commit`: append `--mypy` when `PFACTORY_HOOK_MYPY` is truthy,
  and say in the comment what it costs (12s cold, sub-second warm) and the
  on-disk caveat from above.
- Default unchanged: no env var, no mypy, no slowdown.

`--mypy`'s help text carries the caveat, so someone enabling it is told that a
partially staged file is judged as it sits on disk.

## Alternatives rejected

- **The issue's fix alone** (`--package scripts` and nothing else): leaves the
  reported symptom — mypy regressions — entirely uncaught, while looking like a
  fix for it.
- **Run mypy in the hook by default**: every session's first commit pays ~12s
  for a check CI repeats, and the on-disk/index mismatch makes its verdict
  subtly different from the commit. A gate people disable is worth less.
- **Make the hook's mypy read the index** (materialise staged content in a temp
  tree): removes the caveat, but that is a change to the ratchet's mypy path —
  more machinery than an opt-in warrants. If the opt-in proves popular, it is
  the natural follow-up.
- **Silence over honesty** (leave the PASSED line as is): a control whose output
  cannot be told from a stricter one is the shape this repo already refuses.

## Risks

- `--package scripts` may reveal pre-existing ruff debt in `scripts/`. It cannot:
  the ratchet blocks only net-new violations per changed file, and CI already
  gates that package, so anything committed through CI is already at parity.
- The opt-in is a new code path in the ratchet. It is exercised by the
  verification below rather than left to the first user to discover.

## Verification

- **Parity, negative control:** stage a file under `scripts/` carrying one
  net-new ruff violation. Before the change the hook's ratchet passes it; after,
  it fails — and the same file fails CI's invocation, which is the point.
- **Output:** a staged ruff-only run prints "(ruff only; mypy runs in CI)"; a
  `--base` run still prints "(ruff + mypy)".
- **Opt-in works:** stage a file with a net-new `mypy --strict` error; the
  default hook run passes (documented behaviour), and with
  `PFACTORY_HOOK_MYPY=1` the ratchet fails naming that error. This is the test
  that would have caught #778/#785 locally.
- **Contradiction rejected:** `--staged --mypy --no-mypy` exits as a parser
  error.
- **Cost recorded:** time the opt-in run, cold and warm, so the comment's
  numbers are measured rather than claimed.
- `tests/test_ruff_pin_agreement.py` and any hook/ratchet tests stay green.
