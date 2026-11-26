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

TFactory's Nix provisioner (canonical copy in `Factory/scripts/nix_provisioner.py`) uses
`toolchain.python` like this:

- a version it caches in Nix (`3.12`, `3.13`, `3.14`) gets the Nix interpreter;
- any other version goes to the `uv` install path, which first checks that the value is exact
  (`_checked_version`);
- with no value, it uses its cached default (`3.13`).

`">=3.11"` fails that check. The provisioner raises `ProvisionError` before any test runs, the
verify Job fails with `BackoffLimitExceeded`, and no verdict is recorded. AIFactory's merge gate
waits for that verdict, so the PR never auto-merges.

Seen live in E2E runs `mvr-20261126k` and `mvr-20261126l` (Factory#3882). It affects every planned
Python repo whose `requires-python` is a range and that has no exact Python pin (`.python-version`
or `.tool-versions`), which is the common case. Go has the same exact-version check, so a
`go.mod` prerelease such as `1.25rc1`, or `go system` from `.tool-versions`, can fail the same way.
Node, Rust and Java values don't reach that check today, because they map to fixed Nix attributes.
They still feed the manifest digest, though.

## Proposed outcome

- **Python range:** the contract's `toolchain.python` becomes an exact version that satisfies
  every clause of the specifier (`>=`, `>`, `<`, `<=`, `==`, `~=`, `!=`, `.*`). The provisioner's
  Nix-cached versions are preferred, newest first, because they work in network-less lanes.
  - `>=3.11` becomes `3.13`, the newest cached version that satisfies it.
  - `>=3.11,<3.13` becomes `3.12`.
  - If no cached version satisfies the range, the lowest exact version inside it is used, which
    goes to the provisioner's `uv` path.
  - If nothing can be determined, the key is left out and the contract records why, so the
    planner can see it.
- **Go:** a value that isn't an exact version, such as `1.25rc1` or `system`, is left out of the
  toolchain, so the provisioner uses its default.
- **Node, Rust and Java:** values pass through unchanged. This fix doesn't change how they work,
  or their effect on the digest.
- The recon baseline keeps the raw values, so the plan still shows what the repo declared.
- **Live proof:** E2E P1 gets a TFactory verdict, AIFactory merges the PR (S4), and S5 reports the
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

1. **Which version to pick from a range.** The proposal is the newest Nix-cached version that
   satisfies the range, which works without network. The alternative is the oldest version in the
   range, which tests what the project actually promises, but often needs `uv` and network. Is
   "newest cached" acceptable?
2. **Where the cached list lives.** PFactory would need the provisioner's cached Python versions
   (`3.12`, `3.13`, `3.14`). Options:
   - (a) a constant in PFactory with a comment and a test pinned to the Factory canonical file;
   - (b) import it through the vendored `nix_provisioner` copy, if PFactory vendors one.

   (b) is proposed if a vendored copy exists, otherwise (a).

Already decided by the constraints: the provisioner stays strict. A contract that already carries
a rejected value must be re-planned and re-signed.
