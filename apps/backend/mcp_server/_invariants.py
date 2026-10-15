"""mcp_server declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "mcp_server",
    reason=(
        "A stdio MCP entrypoint that Claude Code launches as its own process from the repo's "
        ".mcp.json. It never runs inside the web service, so the service has no relation of it to "
        "observe."
    ),
)
