---
title: Tests Catalog
layout: default
---

# `.pfactory/tests-catalog.json` — developer guide

The tests catalog is a JSON file checked in to the **AIFactory repo** (the
repo under test, not the PFactory repo) at `.pfactory/tests-catalog.json`.
It is the authoritative cross-run continuity store that lets PFactory know
which tests already exist before it generates new ones.

Without the catalog, every PFactory run would generate fresh test files and
the AIFactory repo would accumulate dead tests over time.  The Triager (Task
11 / #29) reads the catalog at triage time to decide whether to UPDATE an
existing test in place or CREATE a new one.

---

## What the catalog stores

Each test that PFactory has ever generated gets exactly one `CatalogEntry`.
The catalog is append-friendly: new entries are added; existing entries are
updated in place by the Triager.

### JSON schema

```json
{
  "version": 1,
  "updated_at": "2026-05-28T12:00:00Z",
  "tests": [
    {
      "test_id": "ac1-login-flow",
      "test_file": "tests/e2e/login-flow.spec.ts",
      "framework": "playwright",
      "lane": "browser",
      "language": "typescript",
      "covers_acs": ["AC#1: User can log in with valid credentials"],
      "generated_at": "2026-05-28T10:30:00Z",
      "generated_by_task": "042-session-expiry",
      "last_verdict": "accept",
      "browsers_tested": ["chromium"],
      "target_ref": "web-staging",
      "operator_locked": false,
      "generation_version": 1
    }
  ]
}
```

### Root fields

| Field | Type | Description |
|-------|------|-------------|
| `version` | integer | Schema version. Always `1` for v0.2; future migrations increment this. |
| `updated_at` | ISO-8601 UTC string | Timestamp of the last write, e.g. `"2026-05-28T12:00:00Z"`. |
| `tests` | array | Ordered list of `CatalogEntry` objects. |

### `CatalogEntry` fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `test_id` | string | yes | Unique identifier within the catalog. Derived from the test filename stem (e.g. `test_login_expiry.py` → `"test-login-expiry"`). |
| `test_file` | string | yes | Repo-relative path to the test file, e.g. `"tests/e2e/login-flow.spec.ts"`. |
| `framework` | string | yes | Framework name, e.g. `"playwright"`, `"pytest"`, `"jest"`. Not validated against the framework registry (see below). |
| `lane` | string | yes | One of the five v0.2 lane values: `"unit"`, `"browser"`, `"api"`, `"integration"`, `"mutation"`. |
| `language` | string | yes | Programming language, e.g. `"typescript"`, `"python"`. |
| `covers_acs` | array of strings | yes | The acceptance-criterion strings this test covers. May be empty. |
| `generated_at` | ISO-8601 UTC string | yes | When the test was first generated. |
| `generated_by_task` | string | yes | The spec-ID of the PFactory task that generated this test, e.g. `"042-session-expiry"`. |
| `last_verdict` | string | yes | Most recent Evaluator verdict. One of `"accept"`, `"reject"`, `"flag"`, `"skip"`. |
| `browsers_tested` | array of strings | no | For `lane="browser"` tests: the browsers exercised, e.g. `["chromium", "firefox"]`. Empty for other lanes. |
| `target_ref` | string or null | no | The target name from `.pfactory.yml` this test was generated for. `null` if absent. |
| `operator_locked` | boolean | no | When `true` the Triager skips regeneration of this test. Default `false`. |
| `generation_version` | integer | no | Starts at `1`; incremented by the Triager on each UPDATE-in-place cycle. Default `1`. |

### Why `framework` is loose

The catalog stores framework names as plain strings and does **not** validate
them against the framework registry at write time.  This is intentional: if a
framework is renamed in the registry (e.g. `"playwright"` → `"playwright-v2"`),
the catalog should still parse without requiring a migration.  The Triager
validates the framework field against the registry at triage time when it
actually matters.

---

## The 3-step `lookup_by_ac` algorithm

`lookup_by_ac(catalog, candidate_ac)` is the single entry point the Triager
uses to check whether an existing test already covers an acceptance criterion.
It implements a **deterministic 3-step match** in priority order:

```
Step 1 — Exact match (best signal):
  exact = [e for e in catalog.tests if candidate_ac in e.covers_acs]
  if exact:
      return exact

Step 2 — AC-ID prefix match:
  ac_id = candidate_ac.split(':', 1)[0].strip()  # e.g. "AC#1"
  if ac_id:
      prefix = [e for e in catalog.tests
                if any(s.startswith(ac_id) for s in e.covers_acs)]
      if prefix:
          return prefix

Step 3 — No match:
  return []
```

### Step 1: Exact match

The candidate AC string must appear verbatim in `entry.covers_acs`.  This
covers the most common case where the same spec is re-run (same AC text).

**Example:**

```python
# Catalog has:
entry.covers_acs = ("AC#1: User can log in with valid credentials",)

# Lookup:
lookup_by_ac(catalog, "AC#1: User can log in with valid credentials")
# → [entry]  (exact match)
```

### Step 2: AC-ID prefix match

When the exact text changes between runs (e.g. the product team polished the
AC description), the AC-ID before the `:` is usually stable.  Step 2 extracts
the prefix and looks for any stored AC string that starts with it.

**Example:**

```python
# Catalog has (same AC#1, different description):
entry.covers_acs = ("AC#1: login flow",)

# Lookup with updated description:
lookup_by_ac(catalog, "AC#1: login expiry")
# Step 1: "AC#1: login expiry" not in covers_acs — no exact match
# Step 2: ac_id = "AC#1", stored "AC#1: login flow".startswith("AC#1") → match
# → [entry]  (prefix match)
```

### Step 3: No match

When neither step succeeds, `[]` is returned and the Triager creates a new
test file.

**When prefix extraction produces an empty string:**  
If the candidate has no `:` character (or only has whitespace before it), the
`ac_id` is empty and step 2 is skipped.  The lookup falls through to `[]`.

```python
lookup_by_ac(catalog, "no colon in this string")
# ac_id = "no colon in this string".split(":", 1)[0].strip() = "no colon in this string"
# This is non-empty, so step 2 runs — but won't match unless a stored AC starts
# with that exact prefix text.
```

### Return value

`lookup_by_ac` returns a **list** of `CatalogEntry` objects.  Only one tier is
returned per call: if exact matches exist, only exact matches are returned (not
prefix matches too).  Insertion order of `catalog.tests` is preserved within
each tier.

---

## The Triager's UPDATE-vs-CREATE-vs-SKIP policy

For each candidate test from Gen-Functional, the Triager runs:

```
matches = catalog.lookup_by_ac(candidate.ac_id)

if matches and matches[0].operator_locked:
    → SKIP  (operator pinned this test, don't regenerate)

elif len(matches) == 1:
    → UPDATE in place
       same test_file path
       increment generation_version
       update last_verdict, generated_at, generated_by_task

elif len(matches) > 1:
    → flag as catalog ambiguity
       pick the most-recent entry (highest generation_version, then newest generated_at)
       emit a warning in the triage report: "N matching entries for AC#X — using most recent"
       UPDATE the picked entry

else:  # len(matches) == 0
    → CREATE new file in the framework-conventional path
       ADD a new CatalogEntry to the catalog
```

The Triager always writes the updated catalog back via `save_catalog` after
processing all candidates.

---

## Migrating a v0.1 workspace

PFactory v0.1 generated pytest files directly into `spec_dir/tests/` but had
no catalog.  The `migrate_v0_1_workspace` helper synthesises `CatalogEntry`
objects for every `test_*.py` file it finds in `spec_dir/tests/`.

### What it does

1. Walks `spec_dir/tests/` for `test_*.py` files.
2. For each file, builds a `CatalogEntry` with:
   - `framework="pytest"`, `lane="unit"`, `language="python"` (v0.1 constants)
   - `test_id` derived from the filename stem (`_` → `-`)
   - `covers_acs` resolved from `spec_dir/test_plan.json` subtask rationale
   - `last_verdict` from `spec_dir/findings/verdicts.json` (defaults to `"accept"`)
   - `generated_at` from the file's mtime (ISO-8601 UTC)
   - `generated_by_task` = `spec_dir.name`
3. Deduplicates by `test_id`: existing entries in the input catalog win.
4. Returns the new catalog (existing entries + appended migrated entries).

### What it does NOT do

- Does not write to disk.
- Does not mutate `spec_dir`.
- Does not call any LLM.

The caller is responsible for saving the result via `save_catalog`.

### Python API

```python
from pathlib import Path
from tests_catalog import load_catalog, save_catalog, migrate_v0_1_workspace, TestsCatalog

spec_dir = Path("/path/to/spec_dir")   # e.g. ~/.pfactory/workspaces/.../specs/042-session-expiry
repo_root = Path("/path/to/aifactory-repo")

# Load existing catalog (or start empty)
existing = load_catalog(repo_root) or TestsCatalog(
    version=1,
    updated_at="2026-05-28T12:00:00Z",
    tests=(),
)

# Migrate
migrated = migrate_v0_1_workspace(spec_dir, existing)

# Persist
save_catalog(repo_root, migrated)
```

### CLI preview (Task 15)

Task 15 ships the `pfactory migrate` command that wraps this helper:

```bash
# Migrate all v0.1 specs in the default workspace
pfactory migrate --workspace ~/.pfactory/workspaces/my-project --repo /path/to/aifactory-repo

# Dry-run (prints what would change, does not write)
pfactory migrate --dry-run ...
```

---

## How operators use `operator_locked`

Setting `operator_locked: true` on a catalog entry tells the Triager to never
regenerate that test, even if a new AC match is found.  This is the right tool
for:

- **Hand-crafted tests** that an engineer wrote by hand after PFactory's
  generated version was rejected.
- **Complex integration tests** where the generated version is known to be
  fragile and the hand-crafted version is stable.
- **Tests in review** that should not be modified until the review is resolved.

To lock a test, edit `.pfactory/tests-catalog.json` directly and set
`"operator_locked": true`.  The Triager respects this field unconditionally.

```json
{
  "test_id": "ac3-payment-flow",
  "operator_locked": true,
  ...
}
```

To unlock, set `"operator_locked": false` (or remove the field, which defaults
to `false`).

---

## Python API reference

```python
from tests_catalog import (
    CatalogEntry,    # frozen dataclass — one test
    TestsCatalog,    # frozen dataclass — root catalog object
    load_catalog,    # (repo_root: Path) -> TestsCatalog | None
    save_catalog,    # (repo_root: Path, catalog: TestsCatalog) -> Path
    lookup_by_ac,    # (catalog, candidate_ac: str) -> list[CatalogEntry]
    migrate_v0_1_workspace,  # (spec_dir: Path, catalog: TestsCatalog) -> TestsCatalog
    CatalogError,    # exception: CatalogError(field: str, reason: str)
)
```

### `load_catalog(repo_root: Path) -> TestsCatalog | None`

Reads `<repo_root>/.pfactory/tests-catalog.json`.  Returns `None` if the file
does not exist.  Raises `CatalogError("file", ...)` on malformed JSON.

### `save_catalog(repo_root: Path, catalog: TestsCatalog) -> Path`

Atomically writes the catalog to `<repo_root>/.pfactory/tests-catalog.json`
(write to `.json.tmp`, then `os.replace`).  Creates the `.pfactory/` directory
if absent.  Output is deterministic (`sort_keys=True`, `indent=2`) so
byte-identical saves are guaranteed for the same input.

### `lookup_by_ac(catalog, candidate_ac) -> list[CatalogEntry]`

3-step deterministic AC-match lookup (exact → prefix → empty).  Returns a
list; never raises.

### `migrate_v0_1_workspace(spec_dir, catalog) -> TestsCatalog`

Pure function; does not write to disk.  Returns new `TestsCatalog` with
migrated entries appended.

### `CatalogError(field, reason)`

Custom exception with `.field` and `.reason` attributes.  Common field values:
`"lane"` (invalid lane string), `"file"` (IO or JSON error), `"field"`
(missing required field in `from_dict`).
