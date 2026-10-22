---
status: approved
issue: 864
spec: spec/2026-10-15-864-invariant-discovery.md
---

# Plan: discover every package; give each a companion

Implements RFC-0024 §3 (Factory#818) in PFactory. Approved decisions:

- The coverage unit is the top-level package: a directory with an
  `__init__.py` directly under `apps/backend` or `apps/web-server` (32 today).
- The checker discovers packages; the only hand-written inputs are
  `SOURCE_ROOTS` and `VENDORED = {"factory_common": "factory_common_invariants"}`
  (sibling file, because `factory_common` is vendored byte-for-byte and its
  drift gate rejects extra files). Every `VENDORED` entry must exist on disk.
- CI fails on: a package with no declaration; a registered name whose package
  or module no longer exists; a source root with no packages.
- Per-module completeness is dropped; existing per-module registrations stay
  and are checked for staleness.
- `--self-test` proves failure per source root.
- Every companion registers a real check or a package-specific written reason.
  Imports a check needs go inside the check function.
- No runtime change: `FACTORY_INVARIANTS` stays default-off here (#865 changes it).

Python: `apps/backend/.venv/bin/python` (a worktree uses the main clone's
venv: `/mnt/code/Source-home/GitHub/PFactory/apps/backend/.venv`).

## Steps

1. `scripts/check_invariant_ownership.py` (whole file; `Covered`/`COVERED` at
   lines 37-57, `modules_on_disk` 62-71, `check` 94-117, `_self_test` 120-161,
   `main` 164-181): rewrite per the spec.
   - `SOURCE_ROOTS`, `VENDORED` constants.
   - `discover(repo, roots) -> tuple[dict[str, str], list[str]]`: package → root,
     plus a problem per root that is missing or has no packages, and per
     package name found under two roots (review finding; else one is hidden).
   - `_resolves(root: Path, name: str) -> bool`: `parts = name.split(".")`;
     true if `root.joinpath(*parts).with_suffix(".py")` is a file or
     `root.joinpath(*parts) / "__init__.py"` is a file. (`factory_common.__init__`
     resolves via the first form.)
   - `check(repo=_REPO) -> list[str]`: roots on `sys.path`; validate `VENDORED`
     (package dir and `<root>/<companion>.py` exist); per package: companion
     path `<root>/<pkg>/_invariants.py` (or the vendored sibling) must exist,
     else "no declaration" problem; import it via `importlib`; then names from
     `registry.registered()`: a package with none → problem; a name whose
     top-level package is not discovered or that does not `_resolves` → stale.
   - `_self_test()`: per root, write `<root>/_invariant_selftest_probe/__init__.py`,
     expect exactly one problem containing `_invariant_selftest_probe`, remove
     with `shutil.rmtree` in `finally`; then `check()` clean; then `discover()`
     on an empty `tempfile.TemporaryDirectory()` root reports it; last,
     `registry.register("factory_invariants._invariant_selftest_gone", reason=...)`
     (a real ≥6-word reason) and expect a stale problem. Print
     `self-test OK: an undeclared package is caught in each of apps/backend, apps/web-server, ...`.
   - `main()`: OK line `OK: all N packages under apps/backend, apps/web-server declare an invariant or say why not.`
   → verify: `python scripts/check_invariant_ownership.py` (fails now: 29
   undeclared packages listed; that is expected until step 3).
   Traps: drop the `# ruff: noqa: PLC0415` header once no import is deferred (ruff RUF100); keep the `os.environ.setdefault("FACTORY_INVARIANTS", "0")`
   in `__main__`; keep the importlib-only access to `factory_invariants`
   (mypy runs per package root; see the existing docstring of `declared_modules`).
   `print` needs `# noqa: T201` like the existing lines.

2. Six real-check companions. Each file: a docstring saying what the relation
   is and why it can break silently, a generator `def _...() -> Iterator[str]`
   that yields one message per violation, and `registry.register(<name>, <fn>)`.
   Import `from factory_invariants import registry` at top; product imports
   inside the function.
   - `apps/backend/plan/_invariants.py`, name `plan.registry`: call
     `plan.registry.loader.discover_builtins()`; expected ids (literal tuple,
     not derived): `infra:aws infra:azure infra:gcp infra:kubernetes infra:openshift
     knowledge:backstage knowledge:confluence knowledge:git-markdown knowledge:gitbook knowledge:notion`;
     yield one message per missing id ("discover_builtins swallows import errors,
     so a broken adapter just disappears from the catalogue").
   - `apps/backend/providers/_invariants.py`, name `providers.factory`: for each
     alias→target in `providers.factory._PROVIDER_ALIASES` with target not in
     `_AGENTIC_REGISTRY`, yield; for each `(module, cls)` in `_AGENTIC_REGISTRY.values()`
     with `importlib.util.find_spec(module) is None`, yield.
   - `apps/backend/framework_registry/_invariants.py`, name `framework_registry.loader`:
     `framework_registry.loader.load_registry()`; yield if empty. (Exceptions
     propagate; `check_all()` reports a raising check as a violation.)
   - `apps/backend/security/_invariants.py`, name `security.hooks`: for each
     command in `project.VALIDATED_COMMANDS` not in `security.hooks.VALIDATORS`, yield
     ("the hook would allow it with no validation").
   - `apps/backend/tools/_invariants.py`, name `tools.executor`: compare
     `set(tools.definitions._ALL_TOOLS)` with
     `set(ToolExecutor(Path(tempfile.gettempdir()))._handlers)`; yield each
     advertised-without-handler and each handler-not-advertised name.
   - `apps/web-server/server/_invariants.py`, name `server.routes.mcp_rpc`:
     compare `{t["name"] for t in MCP_TOOLS}` with `set(_TOOL_HANDLERS)`; yield
     each listed tool with no handler (`KeyError` at `mcp_rpc.py:375`) and each
     handler never listed.
   → verify: `python -c` import each and `registry.enabled=True; registry.check_all() == []`.
   Traps: `SLF001` (private access) may fire on `._handlers`; add `# noqa: SLF001`
   only if ruff reports it.

3. 23 written-reason companions, `apps/backend/<pkg>/_invariants.py`, each:
   ```python
   """<pkg> declares no runtime invariant (RFC-0024 §3)."""

   from factory_invariants import registry

   registry.register("<pkg>", reason="<text below>")
   ```
   Wrap the reason across lines with implicit string concatenation (line
   length 100). Reasons:
   - `agents`: "Holds the planner, gen_functional, evaluator and triager stage agents. Their only process-wide state is the _BG_*_TASKS sets that anchor fire-and-forget stage tasks and discard each one when it finishes; every other relation (lane outputs, verdicts, evidence) belongs to one task run's workspace."
   - `analysis`: "Scans a target project on request (framework detection, risk classification, test discovery) and returns the findings to its caller. It keeps no state in the service, so nothing holds between calls for a check to read."
   - `cli`: "Argument parsing and command routing for the operator command line (run.py and python -m cli). It runs only in a terminal process, never inside the web service, so it has no runtime relation there."
   - `context`: "Builds one task's coding context (keyword extraction, file categorisation, service matching) from the project directory it is given, per call, and caches nothing in the process between calls."
   - `core`: "Shared plumbing for agent sessions: SDK client construction, auth token lookup, worktree setup and progress files, all built per session from configuration and that session's directory. Its credential probes call external CLIs, which a cheap local check must not do."
   - `integrations`: "Wraps the optional Graphiti knowledge-graph memory, which is off unless GRAPHITI_ENABLED is set and talks to an external graph database. The one relation worth watching is that database's reachability, a network call and so not a local invariant."
   - `mcp_server`: "A stdio MCP entrypoint that Claude Code launches as its own process from the repo's .mcp.json. It never runs inside the web service, so the service has no relation of it to observe."
   - `memory`: "Persists session learnings as files in each spec's memory directory, or in Graphiti when enabled. The files belong to one spec and are read back by the next session for that spec, so no process-wide relation exists."
   - `merge`: "Intent-aware merge of parallel task branches, run once per merge on the git worktrees it is handed. Its timelines and baselines are files per project, so nothing lives in the service between merges."
   - `pfactory_yml`: "Pydantic schema and parser for a target repository's .pfactory.yml. Validation when a file is loaded is already the check, and the service keeps no parsed config between calls."
   - `planner_lib`: "Generates an implementation plan from one spec by reading that spec and the codebase it names, and returns the plan to its caller. It has no module state that could drift."
   - `project`: "Detects a project's stack and builds its command allowlist per project directory. The cross-package relation it feeds, that every command needing validation has a validator, is owned and checked by security.hooks."
   - `prompts_pkg`: "Renders prompt text from templates and project context on each call. A prompt is a string handed to the model, and nothing is kept between calls for a check to read."
   - `review`: "Human review checkpoint between spec and build, reading and writing review_state.json inside each spec directory. Approval state belongs to one spec on disk, not to the running service."
   - `runners`: "Namespace package that re-exports nothing since #43. Its submodules (the GitHub client and providers, the artifact store, the changelog runner) are imported directly by consumers and act on the repository or API call they are given."
   - `services`: "Starts and stops docker-compose or monorepo services for a test run, and tracks rollback attempts for a failing build. Both concern one run's containers and history, checked by that run's own health checks."
   - `spec`: "One path helper, get_specs_dir, and two directory-name constants that locate a project's specs directory for the build CLI. It computes paths from its argument and holds nothing, deliberately unlike AIFactory's larger spec package."
   - `task_logger`: "Writes phase-structured task logs as JSON into each spec directory and streams markers to the UI. Each log belongs to one task, so there is no service-wide relation to assert."
   - `templates_pkg`: "A string.Template engine that loads test-file templates from disk and parses their metadata. Each template's variables are checked against that metadata when it is instantiated, on every call, and the package keeps no state between calls."
   - `test_plan`: "Dataclasses and enums for the subtask-based test_plan.json (phases, subtasks, verification). Pure data types whose consistency is enforced when a plan is built, holding no process state."
   - `tests_catalog`: "Loads and atomically saves the per-repository .pfactory/tests-catalog.json, validating its schema on load. The catalog lives in the target repo, not in the service, so the service holds no copy to check."
   - `ui`: "Terminal output helpers (colours, icons, boxes, spinners, menus) for the command line. They detect terminal capabilities per process and print, and nothing they do is observable in the web service."
   - `workspaces`: "Takes read-only snapshot copies of AIFactory specs under ~/.pfactory/workspaces per project and spec. Each snapshot is written once and never mutated, so no relation persists in the service between snapshots."
   → verify: `python scripts/check_invariant_ownership.py` → `OK: all 32 packages ...`;
   `--self-test` → `self-test OK`.
   Traps: none of these files may live inside `apps/web-server/factory_common/`.

4. `tests/test_invariant_pilot.py`:
   - add `apps/web-server` to `sys.path` beside `BACKEND` (lines 26-28).
   - new `test_every_package_on_disk_is_discovered_and_declared`: load the
     checker with `importlib.util.spec_from_file_location`, assert
     `set(discover(REPO, SOURCE_ROOTS)[0]) == {p.parent.name for root in roots for p in (REPO/root).glob("*/__init__.py")}`,
     `len(...) >= 32`, and `check() == []`.
   - `test_ownership_checker_can_fail` (lines 173-186): expect
     `"an undeclared package is caught"`.
   - one firing test per step-2 check, using `monkeypatch` on the attribute the
     check reads (`setitem` on `_PROVIDER_ALIASES`, `delitem` on `VALIDATORS`,
     `setattr` of `loader._DEFAULT_FRAMEWORKS_DIR` to an empty `tmp_path`,
     `setattr` of `plan.enrich.base.available_adapters` to drop `aws`,
     `setitem` on `_ALL_TOOLS`, `setitem` on `mcp_rpc._TOOL_HANDLERS`), each
     asserting a violation attributed to the step-2 name, using the existing
     `live` fixture.
   → verify: `pytest tests/test_invariant_pilot.py tests/test_invariant_vendored_seams.py -q`;
   the discovery test fails against `origin/dev`'s checker (prove with
   `git stash` of the script only).
   Traps: `# ruff: noqa: S603` header stays; the registry is process-global, so
   firing tests must restore via `monkeypatch`, never by hand.

## Tests

- `apps/backend/.venv/bin/python scripts/check_invariant_ownership.py` → exit 0, 32 packages.
- `apps/backend/.venv/bin/python scripts/check_invariant_ownership.py --self-test` → exit 0.
- `apps/backend/.venv/bin/pytest tests/test_invariant_pilot.py tests/test_invariant_vendored_seams.py -q` → green.
- `ruff check` and `ruff format --check` on every touched or new file.

## Deviations from review (recorded after implementation)

- `merge` now has a **real check** instead of a written reason:
  `merge.semantic_analyzer` requires `TREE_SITTER_AVAILABLE` and the `.py`,
  `.js`, `.ts` grammars in `LANGUAGES_AVAILABLE` (`merge/semantic_analyzer.py:57-91`
  swallows import errors; all four packages are pinned in
  `apps/backend/requirements.txt:40-43`). Two firing tests added. Seven real checks in total.
- Reasons rewritten to name process-wide state the first versions missed:
  `agents` (`_BG_*_TASKS`, `evidence.http_recorder._entries`), `core`
  (`client._PROJECT_INDEX_CACHE`, `cache._PREFIX_HASHES`, the `lru_cache` on
  `mcp_credentials._load_operator_config`) and `analysis` (per-instance
  `_cache` dicts). `templates_pkg` and `spec` were corrected earlier.
  `memory`, `context`, `planner_lib`, `prompts_pkg` and `services` were
  re-read: no module-level state.
- Checker: a missing vendored companion is reported once, not three times;
  a package name under two roots is reported. mypy `--strict` fixes: a
  shadowed loop variable, and `security` reads `VALIDATORS` from
  `security.validator` (same dict object `security.hooks` uses).

## Rollback

Revert the PR. The companions are inert unless `check_all()` is called, and
nothing in production calls it before #865.
