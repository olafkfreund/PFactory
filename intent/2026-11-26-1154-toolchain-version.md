---
status: draft
issue: 1154
author: olafkfreund
---

# Intent: the contract's toolchain carries exact versions, not specifiers

## Problem

PFactory's repo reconnaissance (`plan/recon/reconnoiter.py`, `_detect_versions`) reads pinned tool
versions from:

- `.tool-versions`;
- `.python-version` and similar pin files;
- `rust-toolchain.toml`;
- `go.mod`;
- `pyproject.toml`'s `requires-python`.

`plan/emit/environment_block.py` copies those values unchanged into the signed task contract as
`environment.toolchain`.

`requires-python` is a range, not a version. The e2e fixture has `requires-python = ">=3.11"`,
which is the usual form, so the contract says `toolchain.python: ">=3.11"`.

TFactory's Nix provisioner (canonical copy in `Factory/scripts/nix_provisioner.py`,
`_checked_version`) only accepts an exact version such as `3.12`. It raises `ProvisionError` before
any test runs. The verify Job fails with `BackoffLimitExceeded` and no verdict is recorded.
AIFactory's merge gate waits for that verdict, so the PR never auto-merges.

Seen live in E2E runs `mvr-20261126k` and `mvr-20261126l` (Factory#3882). It affects every planned
Python repo whose `requires-python` is a range. Other non-exact pins, for example
`rust-toolchain.toml` `channel = "stable"` or a `.tool-versions` entry such as `python system`,
fail the same way.

## Proposed outcome

- `environment.toolchain` only ever holds exact versions in the form the provisioner accepts
  (`\d+(\.\d+){0,2}`).
- A range is reduced to its lower bound, which is the oldest interpreter the project claims to
  support:
  - `>=3.11` and `~=3.11` become `3.11`;
  - `>=3.10,<4` becomes `3.10`;
  - `==3.12.*` becomes `3.12`.
- A value with no usable lower bound (`stable`, `system`, `<4`, `*`) is left out of the toolchain,
  so the provisioner uses its default for the language instead of failing.
- The recon baseline keeps recording the raw value, so the plan can still say what the repo
  declared.
- Live proof: E2E P1 gets a TFactory verdict, AIFactory merges the PR (S4), and S5 reports the
  verdict on the merge SHA.

## Affected users and systems

- PFactory `plan/emit/environment_block.py` (both places that set `env["toolchain"]`) and its
  tests.
- Every plan PFactory signs for a repo with a ranged or symbolic pin, once PFactory is released
  and deployed.
- TFactory and the provisioner are unchanged: they keep failing closed on a bad value.

## Constraints

- The contract field keeps its meaning and format. No change to the provisioner or the contract
  schema.
- Already signed contracts are not rewritten. They are re-planned or re-run if needed.
- Deterministic: the same input always gives the same toolchain.
- Stdlib parsing only. No new dependency such as `packaging` unless PFactory already ships it.

## Open questions

1. **Lower bound or newest supported?** For `>=3.11` the proposal pins `3.11`, the oldest version
   the project claims to support, which is the strictest check. The alternative is to drop it and
   let the provisioner's default (currently newer) be used. Lower bound is proposed, because it
   tests the version the project actually promises.
2. Should the provisioner also become tolerant, so old signed contracts verify? The proposal is
   no: PFactory is the producer, and a lenient consumer would hide the next malformed value.
