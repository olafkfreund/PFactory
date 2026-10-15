---
status: approved
issue: 864
intent: intent/2026-10-15-864-invariant-discovery.md
---

# Spec: discover every package; give each a companion

## Design

### Checker (`scripts/check_invariant_ownership.py`)

Replace `Covered`/`COVERED` (lines 37-57) with the two hand-written inputs
RFC-0024 §3.2 allows:

```python
SOURCE_ROOTS = ("apps/backend", "apps/web-server")
# Vendored byte-for-byte from the hub; its drift gate rejects extra files.
VENDORED = {"factory_common": "factory_common_invariants"}
```

- `discover(repo, roots)` returns `{package: root}` for every directory with
  an `__init__.py` directly under each root, plus a problem for each root that
  has none (or does not exist).
- `check(repo)`:
  1. puts every root on `sys.path`;
  2. for every `VENDORED` entry, the package and `<root>/<companion>.py` must
     exist (an exception entry that names nothing is a problem);
  3. for each discovered package, the companion is `VENDORED[pkg]` or
     `<pkg>._invariants`; a missing companion file is a "no declaration"
     problem, otherwise it is imported;
  4. a package with no registered name equal to `pkg` or starting `pkg.` is a
     "no declaration" problem (a companion that registers nothing);
  5. every registered name must resolve on disk: its top-level package was
     discovered, and a dotted name resolves to `<root>/<a>/<b>.py` or
     `<root>/<a>/<b>/__init__.py`. Otherwise it is stale.
- Per-module completeness inside a package is dropped: the unit is the package
  (RFC §3.1). Existing per-module registrations stay valid and are still
  checked for staleness.
- `--self-test` (per source root): create an undeclared package
  `_invariant_selftest_probe/` with an `__init__.py` and assert exactly one
  problem naming it; then assert the tree is clean; then assert `discover()`
  on an empty temp root reports the empty root; finally register a name for
  a module that does not exist and assert it is reported as stale (last,
  because the registry cannot unregister). Prints
  `self-test OK: an undeclared package is caught in each of <roots> ...`.
- Success prints one line: `OK: all N packages under <roots> declare ...`.

### Companions (29 new `<pkg>/_invariants.py`)

Each follows the pilot's form (`apps/backend/factory_invariants/_invariants.py`):
module docstring, then `registry.register(...)`. Imports of product code that
a check needs happen inside the check function, so importing a companion stays
cheap. Real checks where a cheap, local relation is obvious (all six verified
to hold on `origin/dev` today):

| Package | Name | Relation |
| --- | --- | --- |
| `plan` | `plan.registry` | `discover_builtins()` swallows every import error (`plan/registry/loader.py:22-65`); the five infra adapters and five knowledge connectors it names must all appear. |
| `providers` | `providers.factory` | every `_PROVIDER_ALIASES` value is a key of `_AGENTIC_REGISTRY`, and every adapter module there is findable (`importlib.util.find_spec`, no import). |
| `framework_registry` | `framework_registry.loader` | `load_registry()` returns at least one descriptor without raising (`frameworks/` ships in the image via `COPY .`). |
| `security` | `security.hooks` | every command in `project.VALIDATED_COMMANDS` has a validator in the `VALIDATORS` dict the hook consults (`security/hooks.py:20,110`). |
| `tools` | `tools.executor` | the tool names advertised by `tools.definitions._ALL_TOOLS` equal the `ToolExecutor` dispatch table keys. |
| `server` | `server.routes.mcp_rpc` | `MCP_TOOLS` names equal `_TOOL_HANDLERS` keys; a listed tool with no handler is a `KeyError` at `mcp_rpc.py:375`. |

The other 23 (`agents`, `analysis`, `cli`, `context`, `core`, `integrations`,
`mcp_server`, `memory`, `merge`, `pfactory_yml`, `planner_lib`, `project`,
`prompts_pkg`, `review`, `runners`, `services`, `spec`, `task_logger`,
`templates_pkg`, `test_plan`, `tests_catalog`, `ui`, `workspaces`) register
one written reason under the package name, written after reading the package,
naming what it owns and why no package-level relation is observable in the
running service (per-call state, operator CLI only, pure data types, etc.).

### Tests

- `tests/test_invariant_pilot.py`:
  - new `test_every_package_on_disk_is_discovered_and_declared`: imports the
    checker, asserts `discover()` equals the set of
    `apps/{backend,web-server}/*/__init__.py` parents (at least 32) and
    `check() == []`. Fails on `origin/dev` (no `discover`).
  - `test_ownership_checker_can_fail` asserts the new self-test line.
  - one firing test per new real check (monkeypatch the attribute the check
    reads at call time).
- `tests/test_invariant_vendored_seams.py`: unchanged unless it breaks.

## Alternatives rejected

- Keep per-module coverage for the three pilot packages: RFC-0024 §3.1 sets the
  unit to the package; two rules in one checker is harder to read.
- Derive the vendored map from the drift gate: a second source of truth for one
  entry.
- Real checks for every package: most packages hold per-call state only;
  invented checks would be decoration (RFC §8).

## Risks

- Importing all 32 companions imports every package `__init__`. Verified: all
  32 import in the backend venv with `FACTORY_INVARIANTS=0` in under 1 s.
- A real check that is wrong would only show in `check_all()`, which nothing in
  production calls until #865, and then non-critical.
- Reasons decaying into boilerplate: review is the control (RFC §8).

## Verification

- `apps/backend/.venv/bin/python scripts/check_invariant_ownership.py` → OK, 32 packages.
- `... --self-test` → `self-test OK`.
- `apps/backend/.venv/bin/pytest tests/test_invariant_pilot.py tests/test_invariant_vendored_seams.py -q` green; the new discovery test fails on `origin/dev`.
- `ruff check` and `ruff format --check` on touched files.
