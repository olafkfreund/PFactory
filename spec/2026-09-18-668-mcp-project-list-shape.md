---
status: approved
issue: 668
intent: intent/2026-09-18-668-mcp-project-list-shape.md
---

# Spec: MCP `project_list` crashes on the web-server's `projects.json` shape

## Facts established

- **Same file (intent Q1 answered):** MCP resolves
  `PFACTORY_WORKSPACE_ROOT` or `~/.pfactory` → `projects.json`
  (`task_control.py:74,81,114`). The web server resolves
  `PROJECTS_DATA_DIR` → `get_data_dir()` (`config.py:123`); the image sets
  `APP_PROJECTS_DATA_DIR=/home/nonroot/.pfactory` (`Dockerfile:276`). In the
  deployed container, and in a default local install, it is one file.
- **Entry fields differ, not just the top level.** Web server:
  `{"<id>": {"name", "path", "repo"?, "settings"?, "created_at", ...}}` and
  several routes index `projects[id]["path"]` directly
  (`changelog.py:465` …). MCP: `{"projects": [{"id", "name", "root_path",
  "created_at"}]}`, and `task_create_and_run` reads `root_path`.

## Design

All in `apps/backend/agents/tools_pkg/tools/task_control.py`.

1. **`_load_projects()` normalises on read**, returning the list form every
   caller already expects plus the shape it came from:

       {"projects": [...], "_shape": "list" | "by_id"}

   For the id-keyed shape each entry becomes
   `{"id": pid, **entry, "root_path": entry.get("root_path") or entry.get("path", "")}`.
   A file that is neither shape (or unreadable) → `{"projects": [], "_shape": "list"}`,
   as today. Callers at lines 283, 528, 571, 696 need no change.
   `project_list` strips `_shape` from its output (it only returns
   `count`/`projects` today, so it already does).

2. **Decided (intent Q2): `_save_projects()` preserves the shape it read.**
   - `_shape == "list"`: write `{"projects": [...]}` exactly as today.
   - `_shape == "by_id"`: re-read the file, add only the new entry keyed by id
     as `{"name", "path": root_path, "root_path": root_path, "created_at"}`,
     and write the dict back. Entries the web server wrote are never re-derived
     from the normalised list, so their extra fields (`repo`, `settings`,
     `source`, …) survive byte-for-byte.

   Implementation stays small: `project_create` passes the new entry to
   `_save_projects`, which appends (list) or keys-in (by_id).

## Alternatives rejected

- **Normalise read only (issue's suggestion)**: `project_create` would then
  write the list shape over a web-server file; every web-server route that
  does `projects[id]["path"]` breaks. Data-loss direction.
- **Make the web-server shape canonical for both and migrate**: rewrites
  users' files, touches web-server code and its tests; intent forbids a
  migration on read.
- **Separate files for the two components**: splits the registry, so a project
  created in the portal is invisible to MCP — the opposite of the goal.

## Risks

- Read-modify-write race with the web server on `projects.json` (already
  exists today in both components; not made worse). Out of scope.
- Web-server repo-only projects have `path: ""` → MCP sees `root_path: ""`.
  `task_create_and_run` on such a project behaves as it would for any empty
  path today; not changed here.

## Verification

New tests in `tests/test_pfactory_mcp_tools.py`:
- `projects.json` holds the id-keyed dict → `project_list` returns both
  entries with `id` and `root_path` filled, no `KeyError` (the issue's case).
- id-keyed file + `project_create` → file is still id-keyed, the original
  entries are byte-identical (including `settings`/`repo`), the new entry has
  `path` and `root_path`.
- list-shaped file → create/list behave exactly as today (existing tests
  `test_project_create_happy`, `test_project_list_after_create` stay green).
- Negative control: revert the normalisation, the id-keyed list test raises
  `KeyError`.
- `apps/backend/.venv/bin/pytest tests/test_pfactory_mcp_tools.py -q` green.
