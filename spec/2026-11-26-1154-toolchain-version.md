---
status: approved
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

### `contract_toolchain(versions: Mapping[str, object]) -> dict[str, str]`

**Input rules.**

- A value that is not a `str` is dropped.
- Surrounding whitespace is stripped.
- A version is 1 to 3 dot-separated components. Each component is 1 to 4 ASCII digits, matched
  with `re.fullmatch(r"[0-9]{1,4}(\.[0-9]{1,4}){0,2}", v)`. ASCII-only and length-bounded means a
  value can't contain non-ASCII digits or an oversized integer.

**Per key.**

- **Exact values** (the input rule above) pass through unchanged, except Go below 1.21 (see
  below).
- **`python`, when not exact,** is resolved by `_resolve_python(spec)`, described next.
- **`go`, when not exact or below `1.21`,** is dropped. The provisioner rejects downloadable Go
  versions below 1.21.
- **Other keys** (`node`, `rust`, `java` and anything else) pass through unchanged when they are
  strings. This is not a behaviour change.
- **A dropped or unresolved key** is left out of the toolchain. A warning is logged that names only
  the key and a fixed reason code (`non_string`, `unparseable`, `unsatisfiable`, `go_too_old`),
  never the repo-supplied value.
- **The contract still records what the repo declared** in `baseline.versions`, which keeps the raw
  value. The `environment` schema is strict (`additionalProperties: false`), so no notes field is
  added, as the intent's constraints require.

### `_resolve_python(spec)`

1. **Parse.** Split on `,`, strip each clause, and reject empty clauses. Each clause is
   `(~=|==|!=|<=|>=|<|>)` followed by a version (the input rule above), with an optional `.*` after
   `==` and `!=` only. `~=` needs at least 2 components. Anything else makes the value
   `unparseable`.
2. **Candidates.** Minor versions only, `3.8` to `3.14`.
3. **A minor `X.Y` satisfies the spec** only if every patch release in that minor would. For each
   clause, `X.Y.0` and `X.Y.9999` must both satisfy it. A `!= V` with three components and
   `V[:2] == (X, Y)` also rejects the minor, because the pinned patch is unknown.
4. **Pick.**
   - First, the newest minor that satisfies the spec and is in `_NIX_CACHED_PYTHON`. This is the
     Nix path, which works without network.
   - Otherwise, the lowest satisfying minor. This is the uv path.
   - Otherwise, `unsatisfiable`, and the key is left out.

### Satisfaction for a single release tuple

Versions are integer tuples padded with zeros.

| Clause | Satisfied when |
|---|---|
| `>= V`, `> V`, `<= V`, `< V` | The tuple comparison holds |
| `== V` / `!= V` | The tuples are equal / not equal |
| `== V.*` | The first `len(V)` components equal `V`. Shorter tuples are padded first. |
| `!= V.*` | The prefix doesn't match |
| `~= V` | `>= V` and `== V[:-1].*` |

### `_NIX_CACHED_PYTHON = ("3.14", "3.13", "3.12")`

A comment names the source, `Factory/scripts/nix_provisioner.py` `_PY_ATTR`, and #1020, the reason
3.11 is excluded. It says to update the constant whenever `_PY_ATTR` changes. A shape test checks
that the entries are sorted newest first and match the exact-version pattern. PFactory doesn't
vendor the provisioner, so drift is a known, documented limit.

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

Tests in `tests/test_environment_block.py`.

`contract_toolchain({"python": X})` gives:

| Input `X` | Result | Why |
|---|---|---|
| `>=3.11` | `3.14` | The newest cached minor. This corrects the intent's `3.13` example; the intent's rule is "newest cached". |
| `>= 3.11, < 3.13` | `3.12` | Whitespace is allowed |
| `>=3.11,!=3.14.*` | `3.13` | |
| `~=3.12` | `3.14` | |
| `==3.12.*` | `3.12` | |
| `==3.12.0` | omitted (`unsatisfiable`) | A minor can't promise patch `.0` |
| `>=3.12,!=3.14.1` | `3.13` | `3.14` is rejected because its patch is unknown |
| `>=3.10,<3.12` | `3.10` | No cached minor fits; the lowest satisfying minor goes to the uv path |
| `>=3.10,!=3.10.*,<3.12` | `3.11` | |
| `<3.12` | `3.8` | The lowest satisfying minor (uv path) |
| `>3.13,<3.14` | omitted | No whole minor fits |
| `stable`, `system`, `>=3.11rc1`, `>=`, `3.12\n`, `٣.١٢`, a 5,000-digit component | omitted (`unparseable`) | |
| `3.12` | `3.12` | Exact values pass through |
| `3` (non-string) | omitted (`non_string`) | |

Go:

- `1.25` is kept.
- `1.25rc1` is dropped.
- `1.20` is dropped (`go_too_old`).

Node, Rust and Java string values pass through unchanged.

Both emission paths (the descriptor path and the legacy-framework path):

- use the helper;
- keep the raw value in `baseline.versions`;
- leave out the `toolchain` key when the helper returns nothing.

Logs:

- each omission logs the key and the reason code;
- no log record contains the raw value.

Then:

- the existing `test_environment_block.py`, contract-emit and schema tests keep passing;
- a schema test validates a contract with a resolved toolchain;
- CI is green;
- live: release PFactory, then run E2E P1 through S4 and S5.
