---
status: draft
issue: 1154
intent: intent/2026-11-26-1154-toolchain-version.md
---

# Spec: resolve detected versions into exact toolchain versions

## Design

The only file that changes is `apps/backend/plan/emit/environment_block.py`, plus its tests in
`tests/test_environment_block.py`. Both places that set `env["toolchain"] = dict(versions)` (about
line 87 in `_descriptor_environment` and about line 162 in `derive_environment`) call one new
helper:

```python
toolchain = contract_toolchain(versions)
if toolchain:
    env["toolchain"] = toolchain
```

### `contract_toolchain(versions: Mapping[str, str]) -> dict[str, str]`

- **Exact values pass through unchanged.** A value matches `^\d+(\.\d+){0,2}$`, the same pattern
  as the provisioner's `_VERSION_RE`.
- **`python`, when not exact,** is resolved with `_resolve_python(spec)`:
  1. Parse the comma-separated clauses. Each clause is an operator (`~=`, `==`, `!=`, `<=`, `>=`,
     `<`, `>`) followed by a version, with an optional `.*` after `==` and `!=`. Any clause that
     doesn't parse (for example `===`, a local version, or text) makes the whole value
     unresolvable.
  2. Candidates: `_NIX_CACHED_PYTHON = ("3.14", "3.13", "3.12")`, newest first. Return the first
     one that satisfies every clause.
  3. Otherwise, the lowest candidate is the version named by a `>=V`, `~=V` or `==V`/`==V.*`
     clause. Return it if it satisfies every clause.
  4. Otherwise, return `None`.
- **`go`, when not exact,** is dropped.
- **Other keys** (`node`, `rust`, `java` and anything else) pass through unchanged. This is not a
  behaviour change.
- **A dropped or unresolvable key is left out**, and a warning is logged with the key name and
  `sanitize_log(value)`. The contract schema doesn't change, and the recon baseline keeps the raw
  value.

### Satisfaction

Versions are compared as integer tuples padded with zeros, so `3.13` means `3.13.0`. The
operators mean:

| Operator | Meaning |
|---|---|
| `>= V` / `> V` / `<= V` / `< V` | Tuple comparison |
| `== V` | Equal tuples |
| `== V.*` | The candidate's first `len(V)` components equal `V` |
| `!= V` | Not equal |
| `!= V.*` | Prefix doesn't match |
| `~= V` (at least two components) | `>= V` and `== V[:-1].*` |

This is PEP 440 for plain release versions only. Pre-releases, epochs and local versions are
unresolvable.

### `_NIX_CACHED_PYTHON`

The constant gets a comment that names its source:
`Factory/scripts/nix_provisioner.py` `_PY_ATTR`, the versions the pin serves from Nix, and
#1020, the reason 3.11 is excluded. PFactory doesn't vendor the provisioner, so the comment says
to update the constant whenever `_PY_ATTR` changes. A guard test asserts the constant is
non-empty, sorted newest first, and that every entry matches the exact-version pattern.

## Alternatives rejected

- **Normalize in `reconnoiter.py`:** the baseline would lose what the repo actually declared.
- **Make the provisioner tolerant:** this hides malformed contracts. The intent keeps it strict.
- **Use `packaging.specifiers`:** this adds a dependency, unless PFactory already ships it. The
  stdlib subset above covers the plain-release grammar that recon reads.
- **Leave out every Python range:** this drops a real constraint. `<3.13` would silently get the
  `3.13` default, which falls outside the range.

## Risks

- **The cached list drifts from the provisioner.** A version that left the pin would then go to
  `uv`, which works but needs network. A version that joined the pin wouldn't be preferred, which
  is still valid. Both are safe; neither breaks verification.
- **Contracts that already exist** still carry the raw value and must be re-planned.
- **Prod:** PFactory is released and deployed through the normal process. There is no migration.

## Verification

Tests in `tests/test_environment_block.py`:

| Input | Result |
|---|---|
| `>=3.11` | `3.13` |
| `>=3.11,<3.13` | `3.12` |
| `>=3.11,!=3.14.*` | `3.13` |
| `~=3.12` | `3.14` |
| `==3.12.*` | `3.12` |
| `>=3.10,<3.12` | `3.10` (uv path) |
| `<3.12` alone | omitted |
| `>3.13,<3.14` | omitted |
| `stable`, `system`, `>=3.11rc1` | omitted |
| `3.12` (exact) | unchanged |

- Go: `1.25rc1` is dropped, `1.25` is kept.
- Node, Rust and Java values pass through unchanged.
- Both emission paths use the helper: the descriptor path and the legacy-framework path.
- When nothing is left, there is no `toolchain` key.
- A warning is logged for each omitted key.

Then:

- The existing `test_environment_block.py` and contract-emit tests keep passing.
- CI is green.
- Live: PFactory is released, and E2E P1 then runs through S4 and S5.
