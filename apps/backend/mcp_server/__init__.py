"""
PFactory standalone MCP server package.

Hosts ``pfactory_server`` — a stdio-transport entrypoint that exposes the
same MCP tools the in-process Claude Agent SDK session does
(``update_subtask_status``, ``get_build_progress``, ``record_discovery``,
``record_gotcha``, ``get_session_context``, ``update_qa_status``,
``test_memory_integration``) so Claude Code can auto-register them via the
project-scoped ``.mcp.json`` at the repo root.

Issue #10 — Epic #6.
"""
