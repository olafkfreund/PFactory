---
status: approved
issue: 668
author: Olaf Krasicki-Freund
---

# Intent: MCP `project_list` crashes on the web-server's `projects.json` shape

## Problem

Two components read and write `projects.json` with different top-level shapes:

- MCP tools (`apps/backend/agents/tools_pkg/tools/task_control.py`):
  `{"projects": [ {...}, ... ]}` — `_load_projects()` returns the file
  unvalidated, and lines 283, 528, 571, 580 index `data["projects"]`.
- Web server (`apps/web-server/server/services/project_paths.py:32`):
  `{"<project_id>": {...}, ...}` keyed by id.

When the web server wrote last, MCP `project_list` raises a bare
`KeyError: 'projects'`. `project_list` is the documented way to find a
`project_id` before `plan_ingest` / `emit-contract`, so a web-server-managed
install cannot be driven over MCP.

Worse, MCP `project_create` (line 581, `_save_projects`) writes the list shape
back. If the read side is merely normalised, creating a project over MCP would
rewrite a web-server file into the list shape and the web server would then
misread every project.

## Proposed outcome

- `project_list` returns the projects on disk whichever component wrote them.
- MCP `project_create` never destroys projects the web server wrote, and the
  web server still reads the file afterwards.
- A test covers the id-keyed shape for list and for create.

## Affected users and systems

- MCP clients (Claude Code, PARR conductor) listing/creating projects.
- Web server project routes (must keep working unchanged).
- `task_control.py`, `tests/test_pfactory_mcp_tools.py`.

## Constraints

- No migration that rewrites a user's existing `projects.json` on read.
- Web-server shape is the one the portal depends on; it must not change.

## Open questions

1. Do both components actually resolve to the same file in the default
   deployment (`PFACTORY_WORKSPACE_ROOT` default `~/.pfactory` vs
   `PROJECTS_DATA_DIR`)? The issue measured the crash live, so at least one
   deployment shares it; the spec will confirm.
2. On write, MCP should preserve the shape it read (write id-keyed back when it
   read id-keyed). Accept that, or make the web-server shape canonical for
   both? Recommendation: preserve-on-write — smallest, no migration.
