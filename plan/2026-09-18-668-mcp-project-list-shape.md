---
status: approved
issue: 668
spec: spec/2026-09-18-668-mcp-project-list-shape.md
---

# Plan: MCP `project_list` crashes on the web-server's `projects.json` shape

Approved decisions (from the spec):

- MCP and web server share `~/.pfactory/projects.json` (confirmed, incl.
  container `APP_PROJECTS_DATA_DIR=/home/nonroot/.pfactory`).
- `_load_projects()` normalises to `{"projects": [...], "_shape": ...}`;
  id-keyed entries become `{"id": pid, **entry, "root_path": entry.root_path or entry.path or ""}`.
  Unknown/unreadable → empty list shape (as today).
- Writes preserve the shape read. For `by_id`, re-read the file and add only the
  new entry `{name, path, root_path, created_at}`; existing entries untouched.
- No migration; no web-server code change.

## Steps

All in `apps/backend/agents/tools_pkg/tools/task_control.py` unless stated.

1. `_load_projects()`: after `json.loads`, branch on shape — dict with a
   `projects` list → list shape; dict of dicts → normalise to list with
   `_shape: "by_id"`; anything else → empty list shape.
   → verify by the new list test (step 4).
2. `_save_projects()` → `_add_project(entry, shape)`: list shape appends and
   writes `{"projects": [...]}` as today; `by_id` re-reads the raw dict, sets
   `raw[id] = {name, path, root_path, created_at}`, writes it back.
   `project_create` calls it instead of append + `_save_projects`.
   → verify by `git grep -n "_save_projects"` shows no stale callers.
3. `project_list`: return only `count`/`projects` (no `_shape` leak).
4. `tests/test_pfactory_mcp_tools.py`: add
   - id-keyed file (two web-server-style entries incl. `repo`, `settings`,
     `path`) → `project_list` returns 2 with `id` and `root_path` set;
   - same file + `project_create` → still id-keyed, original entries
     byte-identical, new entry has `path` == `root_path`;
   - garbage JSON shape (e.g. a list) → `project_list` returns count 0.
   → verify green.
5. Negative control: revert step 1 only, the id-keyed list test raises
   `KeyError`; restore.

## Tests

    apps/backend/.venv/bin/pytest tests/test_pfactory_mcp_tools.py tests/test_auto_fix_pull_on_poll.py -q

Expected: all pass, including existing `test_project_create_happy`,
`test_project_create_duplicate_errors`, `test_project_list_empty`,
`test_project_list_after_create`.

## Rollback

Revert the commit. Files written in by_id shape by the new code are valid
web-server files, so rollback leaves no bad data behind (the old MCP code will
again `KeyError` on them, as it does today).
