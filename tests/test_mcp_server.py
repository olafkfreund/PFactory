"""
Smoke tests for the standalone pfactory MCP server.

Issue #10 — Epic #6. Three layers:

1. **Module import** — ``apps.backend.mcp.pfactory_server`` imports cleanly
   (catches dependency / package-layout bugs before any subprocess work).
2. **stdio JSON-RPC handshake** — spawn the module as a subprocess and run an
   ``initialize`` + ``tools/list`` exchange. Asserts all 7 expected tool names
   are exported by the standalone server.
3. **`.mcp.json` schema** — validate the committed project-scoped config at
   the repo root (top-level key, server transport, wrapper script presence
   and executable bit).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "apps" / "backend"


_EXPECTED_TOOL_NAMES = {
    "update_subtask_status",
    "get_build_progress",
    "record_discovery",
    "record_gotcha",
    "get_session_context",
    "update_qa_status",
    "test_memory_integration",
}


# ---------------------------------------------------------------------------
# Layer 1 — module imports
# ---------------------------------------------------------------------------


class TestModuleImports:
    def test_pfactory_server_module_imports(self) -> None:
        """The standalone entrypoint must import without dragging in heavy deps."""
        sys.path.insert(0, str(_BACKEND))
        try:
            from mcp_server import pfactory_server  # noqa: F401
        finally:
            sys.path.pop(0)

    def test_main_callable(self) -> None:
        sys.path.insert(0, str(_BACKEND))
        try:
            from mcp_server.pfactory_server import main
        finally:
            sys.path.pop(0)
        assert callable(main)


# ---------------------------------------------------------------------------
# Layer 2 — subprocess JSON-RPC handshake
# ---------------------------------------------------------------------------


def _send_jsonrpc(proc: subprocess.Popen, message: dict) -> None:
    """Write a single MCP JSON-RPC message (line-delimited JSON over stdin)."""
    data = json.dumps(message) + "\n"
    assert proc.stdin is not None
    proc.stdin.write(data.encode("utf-8"))
    proc.stdin.flush()


def _read_jsonrpc(proc: subprocess.Popen, timeout: float = 5.0) -> dict:
    """Read one MCP JSON-RPC frame from stdout (line-delimited)."""
    import select

    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready:
        # Drain any stderr to surface the real failure
        stderr_data = b""
        if proc.stderr is not None:
            try:
                stderr_data = proc.stderr.read1(8192) or b""
            except Exception:
                pass
        raise TimeoutError(
            f"No MCP response within {timeout}s. stderr: {stderr_data.decode('utf-8', 'replace')!r}"
        )
    line = proc.stdout.readline()
    if not line:
        raise EOFError("MCP server closed stdout before responding")
    return json.loads(line.decode("utf-8"))


@pytest.fixture
def mcp_subprocess(tmp_path: Path):
    """Spawn the standalone MCP server. Yields the Popen handle; cleans up after."""
    env = os.environ.copy()
    env["PFACTORY_SPEC_DIR"] = str(tmp_path)  # degraded "no active spec" mode
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_server.pfactory_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(_BACKEND),
        env=env,
    )
    try:
        yield proc
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


class TestStdioJsonRpc:
    def test_initialize_and_list_seven_tools(self, mcp_subprocess: subprocess.Popen) -> None:
        # Step 1 — initialize
        _send_jsonrpc(
            mcp_subprocess,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-harness", "version": "0.0.0"},
                },
            },
        )
        init_response = _read_jsonrpc(mcp_subprocess)
        assert init_response.get("id") == 1
        assert "result" in init_response, f"initialize failed: {init_response}"
        assert init_response["result"]["serverInfo"]["name"] == "pfactory"

        # MCP requires `initialized` notification before further requests
        _send_jsonrpc(
            mcp_subprocess,
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
        )

        # Step 2 — tools/list
        _send_jsonrpc(
            mcp_subprocess,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
            },
        )
        tools_response = _read_jsonrpc(mcp_subprocess)
        assert tools_response.get("id") == 2
        assert "result" in tools_response, f"tools/list failed: {tools_response}"

        returned_names = {t["name"] for t in tools_response["result"]["tools"]}
        # The SDK may prefix tool names; allow either bare or mcp__pfactory__-prefixed
        normalised = {n.split("__")[-1] for n in returned_names}
        assert _EXPECTED_TOOL_NAMES.issubset(normalised), (
            f"Missing tools: {_EXPECTED_TOOL_NAMES - normalised}\n"
            f"Got: {returned_names}"
        )


# ---------------------------------------------------------------------------
# Layer 3 — .mcp.json shape
# ---------------------------------------------------------------------------


class TestMcpJsonSchema:
    def test_file_exists_and_parses(self) -> None:
        path = _REPO_ROOT / ".mcp.json"
        assert path.exists(), f".mcp.json missing at repo root ({path})"
        data = json.loads(path.read_text())
        assert isinstance(data, dict)

    def test_pfactory_server_registered(self) -> None:
        data = json.loads((_REPO_ROOT / ".mcp.json").read_text())
        assert "mcpServers" in data, "top-level key must be 'mcpServers' (per Claude Code docs)"
        servers = data["mcpServers"]
        assert "pfactory" in servers, f"expected 'pfactory' entry; got {list(servers)}"
        entry = servers["pfactory"]
        assert entry.get("type") == "stdio", "type must be 'stdio'"
        assert entry.get("command") == "bash"
        args = entry.get("args", [])
        assert any("start-pfactory-mcp.sh" in str(a) for a in args), (
            f"args must reference the wrapper script; got {args}"
        )

    def test_wrapper_script_present_and_executable(self) -> None:
        script = _REPO_ROOT / "scripts" / "start-pfactory-mcp.sh"
        assert script.exists(), "scripts/start-pfactory-mcp.sh missing"
        assert os.access(script, os.X_OK), (
            "scripts/start-pfactory-mcp.sh must have the executable bit set "
            "(run `chmod +x scripts/start-pfactory-mcp.sh`)"
        )

    def test_windows_wrapper_present(self) -> None:
        # Existence only — we don't shell out to .cmd on POSIX CI.
        script = _REPO_ROOT / "scripts" / "start-pfactory-mcp.cmd"
        assert script.exists(), "scripts/start-pfactory-mcp.cmd missing (Windows companion)"
