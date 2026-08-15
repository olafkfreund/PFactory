"""Provider runtime version manager (#121).

Detect / update-to-latest / pin the external provider CLIs & SDKs PFactory
drives — Claude (SDK), Codex, GitHub Copilot, Gemini (and its ``antigravity``
binary). Each runtime knows how to report its installed version, where
"latest" comes from, and how to build an install/update command for a chosen
version. Pins persist to ``~/.pfactory/provider-runtimes.json`` so a
known-good version survives a bad upstream release.

Ollama / OpenAI-compatible are user-managed endpoints (detect-only / N/A).

Design: ``install_argv`` is **pure** (it only builds the command); running it
is the caller's explicit choice (never silent). ``detect_installed`` /
``latest_version`` shell out + hit the network best-effort and degrade to
``None`` rather than raising.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from client_errors import InputRejectedError

__all__ = [
    "InstallResult",
    "ProviderRuntime",
    "RuntimeStatus",
    "get_all_status",
    "get_runtime",
    "get_status",
    "install_argv",
    "pinned_version",
    "run_install",
    "runtimes",
    "set_pin",
]

RuntimeKind = Literal["npm", "pip", "gh", "binary"]

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?)")


@dataclass(frozen=True)
class ProviderRuntime:
    """How to detect + manage one provider's CLI/SDK."""

    name: str
    kind: RuntimeKind
    binaries: tuple[str, ...]  # candidate binary names, first found wins ("" for pip)
    version_args: tuple[str, ...]  # e.g. ("--version",)
    package: str | None = None  # npm / pip package name
    managed: bool = True  # False → detect-only (e.g. ollama)
    # Well-known install locations to probe when none of ``binaries`` is on
    # PATH (``~`` expanded). Mirrors cli_accounts' antigravity-cli probing so
    # the Provider Runtimes panel agrees with the CLI Accounts panel (#121).
    extra_paths: tuple[str, ...] = ()


# The provider runtimes PFactory cares about. Gemini lists ``antigravity``
# first since that's the post-sunset binary (see providers/gemini_agentic.py).
_REGISTRY: dict[str, ProviderRuntime] = {
    "claude": ProviderRuntime("claude", "pip", (), (), package="claude-agent-sdk"),
    "codex": ProviderRuntime("codex", "npm", ("codex",), ("--version",), package="@openai/codex"),
    "copilot": ProviderRuntime(
        "copilot",
        "gh",
        ("copilot", "github-copilot"),
        ("--version",),
        package="@github/copilot",
    ),
    "gemini": ProviderRuntime(
        "gemini",
        "npm",
        ("antigravity", "gemini"),
        ("--version",),
        package="@google/gemini-cli",
        # Antigravity CLI (post-Gemini-sunset successor) installs under
        # ~/.gemini/antigravity-cli/bin/ by default — rarely on PATH.
        extra_paths=(
            "~/.gemini/antigravity-cli/bin/antigravity",
            "~/.gemini/antigravity-cli/bin/gemini",
        ),
    ),
    "ollama": ProviderRuntime("ollama", "binary", ("ollama",), ("--version",), managed=False),
}


@dataclass(frozen=True)
class RuntimeStatus:
    name: str
    kind: RuntimeKind
    managed: bool
    installed: bool
    installed_version: str | None
    latest_version: str | None
    pinned_version: str | None
    update_available: bool


# ── registry access ─────────────────────────────────────────────────────────


def runtimes() -> list[ProviderRuntime]:
    return list(_REGISTRY.values())


def get_runtime(name: str) -> ProviderRuntime:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        # Factory#718: KeyError can't take an InputRejectedError-style
        # constructor (routes already `except KeyError`, so the raised type
        # can't change), but the client_message CONTRACT is a duck-typed
        # attribute -- setting it here still lets client_error() trust this
        # message, which echoes only the caller's own `name`.
        message = f"unknown provider runtime {name!r}; known: {sorted(_REGISTRY)}"
        rejected = KeyError(message)
        rejected.client_message = message
        raise rejected from exc


def _parse_version(text: str | None) -> str | None:
    if not text:
        return None
    m = _VERSION_RE.search(text)
    return m.group(1) if m else None


# ── detection ────────────────────────────────────────────────────────────────


def _find_binary(rt: ProviderRuntime) -> str | None:
    for name in rt.binaries:
        path = shutil.which(name)
        if path:
            return path
    # Not on PATH — probe the runtime's well-known install locations.
    for raw in rt.extra_paths:
        cand = Path(raw).expanduser()
        if cand.is_file() or cand.is_symlink():
            return str(cand)
    return None


def _backend_python() -> str | None:
    """Path to the backend venv's interpreter, if present and not the current
    one. Lets pip-kind detection work when this module is imported from a
    *different* venv (e.g. the web-server, which doesn't install the SDK)."""
    cand = Path(__file__).resolve().parent / ".venv" / "bin" / "python"
    if cand.exists() and str(cand) != sys.executable:
        return str(cand)
    return None


def _pip_version_via(python: str | None, package: str | None) -> str | None:
    """Query ``package``'s installed version inside another interpreter."""
    if not python or not package:
        return None
    try:
        proc = subprocess.run(
            [
                python,
                "-c",
                f"from importlib.metadata import version;print(version({package!r}))",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return _parse_version(proc.stdout) if proc.returncode == 0 else None


def _npm_package_version(bin_path: str) -> str | None:
    """Read the version from a globally-installed npm package's package.json,
    walking up from the binary. Avoids slow Node.js startup (e.g. the Gemini
    CLI takes ~4s to print ``--version``). Ported from cli_accounts (#121)."""
    try:
        for parent in Path(bin_path).resolve().parents:
            pkg_json = parent / "package.json"
            if pkg_json.exists():
                version = json.loads(pkg_json.read_text()).get("version")
                return _parse_version(version) if version else None
    except (OSError, ValueError):
        return None
    return None


def detect_installed(rt: ProviderRuntime) -> str | None:
    """Installed version, or ``None`` if not installed / undetectable."""
    if rt.kind == "pip":
        # Fast path: the importing interpreter has the package.
        try:
            from importlib.metadata import version as _pkg_version

            return _parse_version(_pkg_version(rt.package))
        except Exception:  # noqa: BLE001 - not installed in *this* interpreter
            # Fallback: this module may be imported from the web-server venv,
            # which lacks the SDK — ask the backend venv instead (#121).
            return _pip_version_via(_backend_python(), rt.package)
    binary = _find_binary(rt)
    if binary is None:
        return None
    # npm CLIs: prefer package.json (cheap) over spawning the slow CLI.
    if rt.kind == "npm":
        pkg_version = _npm_package_version(binary)
        if pkg_version:
            return pkg_version
    try:
        proc = subprocess.run(
            [binary, *rt.version_args],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return _parse_version((proc.stdout or "") + (proc.stderr or ""))


def latest_version(rt: ProviderRuntime) -> str | None:
    """Best-effort latest version from the package source (network); ``None``
    on any failure or for sources we don't query (gh / binary)."""
    if not rt.package:
        return None
    if rt.kind == "npm":
        npm = shutil.which("npm")
        if not npm:
            return None
        try:
            proc = subprocess.run(
                [npm, "view", rt.package, "version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return _parse_version(proc.stdout.strip()) if proc.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None
    if rt.kind == "pip":
        try:
            import urllib.request

            url = f"https://pypi.org/pypi/{rt.package}/json"
            with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
                data = json.loads(resp.read().decode("utf-8"))
            return _parse_version(data.get("info", {}).get("version"))
        except Exception:  # noqa: BLE001 - network/parse best-effort
            return None
    return None


# ── pins (persisted choice) ──────────────────────────────────────────────────


def _pins_path() -> Path:
    # Computed lazily so tests can monkeypatch Path.home().
    return Path.home() / ".pfactory" / "provider-runtimes.json"


def _read_pins() -> dict[str, str]:
    path = _pins_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


def pinned_version(name: str) -> str | None:
    return _read_pins().get(name)


def set_pin(name: str, version: str | None) -> None:
    """Pin ``name`` to ``version`` (rollback target), or clear with ``None``."""
    get_runtime(name)  # validate
    pins = _read_pins()
    if version is None:
        pins.pop(name, None)
    else:
        pins[name] = version
    path = _pins_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pins, indent=2, sort_keys=True))


# ── status ───────────────────────────────────────────────────────────────────


def _semver_lt(a: str, b: str) -> bool:
    """True if version ``a`` is older than ``b`` (numeric-tuple compare)."""

    def parts(v: str) -> list[int]:
        out = []
        for chunk in v.split("."):
            num = re.match(r"\d+", chunk)
            out.append(int(num.group()) if num else 0)
        return out

    pa, pb = parts(a), parts(b)
    n = max(len(pa), len(pb))
    pa += [0] * (n - len(pa))
    pb += [0] * (n - len(pb))
    return pa < pb


def get_status(name: str, *, check_latest: bool = True) -> RuntimeStatus:
    rt = get_runtime(name)
    installed = detect_installed(rt)
    latest = latest_version(rt) if (check_latest and rt.managed) else None
    pinned = pinned_version(name)
    update_available = bool(installed and latest and _semver_lt(installed, latest))
    return RuntimeStatus(
        name=rt.name,
        kind=rt.kind,
        managed=rt.managed,
        installed=installed is not None,
        installed_version=installed,
        latest_version=latest,
        pinned_version=pinned,
        update_available=update_available,
    )


def get_all_status(*, check_latest: bool = True) -> list[RuntimeStatus]:
    return [get_status(name, check_latest=check_latest) for name in _REGISTRY]


# ── install / update command (pure builder) ──────────────────────────────────


def install_argv(rt: ProviderRuntime, version: str | None = None) -> list[str]:
    """Build the install/update command for ``rt`` at ``version`` (or latest).

    Pure — returns the argv; the caller decides whether to run it. Raises
    ``ValueError`` for runtimes PFactory can't install (binary / unmanaged).
    """
    if not rt.managed:
        raise InputRejectedError(f"{rt.name} is user-managed; PFactory does not install it")
    if rt.kind == "npm":
        spec = f"{rt.package}@{version or 'latest'}"
        return ["npm", "install", "-g", spec]
    if rt.kind == "pip":
        spec = f"{rt.package}=={version}" if version else rt.package
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", spec]
        return cmd
    if rt.kind == "gh":
        # Copilot is upgraded in place; no version pin via this path.
        return ["copilot", "upgrade"]
    raise InputRejectedError(f"cannot build an install command for kind {rt.kind!r}")


@dataclass(frozen=True)
class InstallResult:
    name: str
    command: list[str]
    returncode: int
    output: str  # combined stdout+stderr tail
    installed_version: str | None  # re-detected after the install


def run_install(name: str, version: str | None = None, *, timeout: int = 600) -> InstallResult:
    """Execute the install/update for ``name`` at ``version`` (or latest).

    Runs :func:`install_argv` — a **real** package install on the host. This is
    an explicit action (callers gate it behind a user request, never silent).
    Returns the result + the re-detected installed version. Raises ``KeyError``
    for an unknown runtime and ``ValueError`` for an unmanaged one.
    """
    rt = get_runtime(name)
    argv = install_argv(rt, version)  # raises ValueError if unmanaged
    try:
        proc = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=timeout)
        returncode = proc.returncode
        output = ((proc.stdout or "") + (proc.stderr or ""))[-4000:]
    except (OSError, subprocess.SubprocessError) as exc:
        returncode = -1
        output = f"install failed to launch: {exc}"
    return InstallResult(
        name=name,
        command=argv,
        returncode=returncode,
        output=output,
        installed_version=detect_installed(rt),
    )
