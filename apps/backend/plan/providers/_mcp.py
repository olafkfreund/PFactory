"""Real provider clients the adapters lazily import (#3, Phase B).

The provider adapters (`terraform`/`aws`/`azure`/`gcp`) call an injected
``client`` in tests and fall back to building one of these when none is injected.
Each client is **read-only** and **guarded**: if the underlying tool/server isn't
installed it raises, and the adapter degrades to "unavailable" (the advisory to
install it is emitted separately by ``plan.providers.registry.suggest_installs``).

- :class:`TerraformScanClient` shells out to **Checkov** (a real IaC scanner) and
  returns its failed checks — concrete and offline-capable.
- :class:`StdioMcpClient` is a minimal newline-delimited JSON-RPC 2.0 client for
  any MCP server over stdio; the cloud clients drive it.
"""

from __future__ import annotations

import json
import select
import shutil
import subprocess
from typing import Any

from plan.providers.registry import get_spec, is_installed

# ── Terraform / IaC via Checkov ──────────────────────────────────────────


class TerraformScanClient:
    """Read-only IaC scan via the ``checkov`` CLI (Checkov ``CKV_*`` checks)."""

    def __init__(self, **config: Any) -> None:
        self.config = config

    def _iac_dir(self, context: dict) -> str | None:
        return (
            context.get("terraform_dir")
            or context.get("iac_dir")
            or self.config.get("terraform_dir")
            or self.config.get("iac_dir")
        )

    def scan(self, context: dict) -> list[dict]:
        directory = self._iac_dir(context)
        if not directory:
            return []
        if shutil.which("checkov") is None:
            raise FileNotFoundError("checkov not installed")
        proc = subprocess.run(  # noqa: S603 - fixed argv, read-only scan
            ["checkov", "-d", str(directory), "-o", "json", "--compact"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return _checkov_failed_checks(proc.stdout)


def _checkov_failed_checks(stdout: str) -> list[dict]:
    """Flatten Checkov JSON into the failed-check dicts terraform.py maps."""
    try:
        data = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        return []
    runs = data if isinstance(data, list) else [data]
    out: list[dict] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        failed = (run.get("results") or {}).get("failed_checks") or []
        for chk in failed:
            if not isinstance(chk, dict):
                continue
            out.append(
                {
                    "check_id": chk.get("check_id", ""),
                    "check_name": chk.get("check_name", "IaC policy violation"),
                    "severity": (chk.get("severity") or "medium"),
                    "resource": chk.get("resource") or chk.get("file_path") or "",
                    "file": chk.get("file_path", ""),
                    "result": "failed",
                    "guideline": chk.get("guideline") or "",
                }
            )
    return out


# ── generic MCP stdio client ─────────────────────────────────────────────


class StdioMcpClient:
    """Minimal MCP stdio JSON-RPC client: initialize → tools/call → close."""

    def __init__(self, argv: list[str], *, timeout: float = 30.0) -> None:
        self.argv = argv
        self.timeout = timeout
        self._proc: subprocess.Popen[str] | None = None
        self._id = 0

    def __enter__(self) -> StdioMcpClient:
        self._proc = subprocess.Popen(  # noqa: S603 - caller-supplied known argv
            self.argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pfactory", "version": "0.1"},
            },
        )
        self._notify("notifications/initialized", {})
        return self

    def __exit__(self, *exc: object) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def call_tool(self, name: str, arguments: dict) -> Any:
        """Call an MCP tool and return its parsed content (structured or text)."""
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if isinstance(result, dict):
            if "structuredContent" in result:
                return result["structuredContent"]
            for block in result.get("content", []) or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    try:
                        return json.loads(block.get("text", ""))
                    except json.JSONDecodeError:
                        return block.get("text", "")
        return result

    # ── JSON-RPC plumbing ──
    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _send(self, obj: dict) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(json.dumps(obj) + "\n")
        self._proc.stdin.flush()

    def _notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict) -> Any:
        rid = self._next_id()
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        assert self._proc and self._proc.stdout
        while True:
            ready, _, _ = select.select([self._proc.stdout], [], [], self.timeout)
            if not ready:
                raise TimeoutError(f"MCP server timed out on {method}")
            line = self._proc.stdout.readline()
            if not line:
                raise ConnectionError("MCP server closed the stream")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RuntimeError(f"MCP error: {msg['error']}")
                return msg.get("result")


class _CloudMcpClient:
    """Base for cloud provider clients backed by an MCP server over stdio."""

    provider_id = ""
    default_tool = ""

    def __init__(self, **config: Any) -> None:
        self.config = config

    def assess(self, context: dict) -> list[dict]:
        spec = get_spec(self.provider_id)
        if spec is None or not is_installed(spec):
            raise FileNotFoundError(f"{self.provider_id} MCP server not installed")
        tool = str(self.config.get("tool") or self.default_tool)
        with StdioMcpClient(spec.launch_argv) as client:
            raw = client.call_tool(tool, {"context": context})
        return raw if isinstance(raw, list) else []


class AwsMcpClient(_CloudMcpClient):
    provider_id = "aws"
    default_tool = "assess_best_practices"


class AzureMcpClient(_CloudMcpClient):
    provider_id = "azure"
    default_tool = "assess_best_practices"


class GcpMcpClient(_CloudMcpClient):
    provider_id = "gcp"
    default_tool = "assess_best_practices"
