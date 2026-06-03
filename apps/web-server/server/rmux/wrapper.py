"""Async subprocess wrapper around the ``rmux`` CLI.

Epic #44 / issue #45 (R0b).

Every later piece of the rmux integration (``session.py``, ``bridge.py``,
``agent_service`` hook) depends on this module.  It is deliberately the
thinnest layer that maps Python coroutines to ``rmux <subcommand>``
invocations, with three structured error classes so callers can branch
on *why* a call failed instead of parsing stderr text in five places.

Design notes
------------

**Socket directory.**  rmux refuses to start with a socket in a directory
it does not own (the v0.3.0 startup check rejects ``/tmp`` with
``i/o error: rmux startup refused '/tmp': owned by uid 0 but expected
uid <N>``).  We default the socket to a user-owned path:

  1. ``$XDG_RUNTIME_DIR/pfactory-rmux/sock`` if the env var is set
     (standard on systemd user sessions; ``/run/user/<uid>/`` is owned
     by the user and tmpfs-backed)
  2. ``~/.cache/pfactory/rmux/sock`` as a portable fallback

Operators can override with the ``socket_dir`` constructor arg — the
production container uses ``/var/run/pfactory/rmux/`` via an
``emptyDir`` volume (see design §3.4).

**Daemon lifecycle.**  rmux auto-starts a server on first session
command, so ``ensure_daemon`` is really a "verify the binary exists,
mkdir the socket dir, smoke-test reachability" call rather than an
explicit fork.  It is idempotent and cheap.

**Error classification.**  rmux's stderr is reasonably stable but not
machine-parseable.  We match on substrings ("no server", "can't find
session") and raise typed exceptions.  Anything we don't recognise
becomes a plain ``RmuxError`` — the caller can still log it.

**Concurrency.**  Every public method is a coroutine.  We never share
subprocess state between calls, so concurrent invocations from
different ``asyncio.Task``s are safe.  The shared socket file is
serialised by the rmux server itself.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
from pathlib import Path


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class RmuxError(Exception):
    """Base class for every rmux-related failure raised by this wrapper.

    Callers can ``except RmuxError`` to catch anything from the wrapper
    without also catching unrelated ``OSError`` / ``asyncio`` errors.
    """


class RmuxNotInstalledError(RmuxError):
    """The ``rmux`` binary is not on PATH (or the configured path).

    Raised by ``ensure_daemon`` only.  Distinguishing this from a
    daemon-failed-to-start lets the caller flip the feature flag off
    cleanly rather than retrying forever.
    """


class RmuxDaemonError(RmuxError):
    """The rmux server is unreachable or refused the connection.

    e.g. socket file gone, server crashed mid-task, permissions wrong on
    the socket dir.  Some callers (``list_sessions``) treat this as
    "no sessions exist yet" and swallow it; others (``new_session``)
    surface it to the user.
    """


class RmuxSessionError(RmuxError):
    """The named session does not exist (or no longer exists).

    Raised by ``kill_session`` / ``send_keys`` / ``pipe_pane`` /
    ``capture_pane`` when the target session is missing.  ``kill_session``
    has an optional ``ignore_missing`` arg for the common "best-effort
    reap" use case from the task-shutdown path.
    """


# ---------------------------------------------------------------------------
# Default socket path
# ---------------------------------------------------------------------------


def _default_socket_dir() -> Path:
    """Return a user-owned directory suitable for the rmux server socket.

    See module docstring for the precedence rules.  Caller MUST mkdir
    the result before pointing rmux at it.
    """
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg) / "pfactory-rmux"
    return Path.home() / ".cache" / "pfactory" / "rmux"


# ---------------------------------------------------------------------------
# Wrapper
# ---------------------------------------------------------------------------


class RmuxWrapper:
    """Thin async subprocess wrapper around the ``rmux`` CLI.

    Args:
        rmux_bin: name or absolute path of the rmux binary.  Defaults
            to ``"rmux"`` (resolved via ``PATH``).
        socket_dir: directory containing the rmux server socket.  If
            ``None``, defaults to the user-owned path described in the
            module docstring.
    """

    def __init__(
        self,
        rmux_bin: str = "rmux",
        socket_dir: str | Path | None = None,
    ) -> None:
        self._bin = rmux_bin
        self._socket_dir = Path(socket_dir or _default_socket_dir())
        self._sock = self._socket_dir / "sock"

    # ----- public API --------------------------------------------------

    @property
    def socket_path(self) -> Path:
        """Filesystem path of the rmux server socket (for diagnostics)."""
        return self._sock

    async def ensure_daemon(self) -> None:
        """Verify rmux is installed and reachable; mkdir the socket dir.

        Idempotent.  rmux's server auto-starts on first command, so we
        simply call ``list-sessions`` — if that succeeds (or returns
        the "no server" message which we swallow), the daemon is ready
        for use.

        Raises:
            RmuxNotInstalledError: binary not found on PATH.
        """
        if shutil.which(self._bin) is None:
            raise RmuxNotInstalledError(
                f"rmux binary '{self._bin}' not found on PATH"
            )
        self._socket_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Best-effort reachability probe — list-sessions returns "no
        # server running" when none has been started yet, which is the
        # happy path on first call.  Any other error is genuinely a
        # daemon problem and surfaces as RmuxDaemonError.
        await self._run("list-sessions", swallow_no_server=True, capture=True)

    async def new_session(
        self,
        name: str,
        cwd: str | Path,
        cmd: str | list[str],
    ) -> None:
        """Start a detached session named ``name`` running ``cmd``.

        Args:
            name: session name (must be unique on this server).
            cwd: working directory the shell starts in.
            cmd: shell command — str is passed through; list is shell-
                quoted and joined.

        Raises:
            RmuxDaemonError: server unreachable.
            RmuxError: rmux returned non-zero with an unrecognised stderr.
        """
        if isinstance(cmd, list):
            shell_cmd = " ".join(shlex.quote(arg) for arg in cmd)
        else:
            shell_cmd = cmd
        await self._run(
            "new-session", "-d", "-s", name, "-c", str(cwd), shell_cmd
        )

    async def kill_session(self, name: str, *, ignore_missing: bool = False) -> None:
        """Kill the session named ``name``.

        Args:
            ignore_missing: if True, both "server not running" and
                "session not found" are silently swallowed.  From the
                caller's perspective on the task-shutdown path, both
                outcomes mean the end state we want — the session is
                gone — so distinguishing them would just force every
                caller to write a try/except around it.
        """
        await self._run(
            "kill-session", "-t", name,
            swallow_missing_session=ignore_missing,
            swallow_no_server=ignore_missing,
        )

    async def send_keys(self, name: str, keys: str) -> None:
        """Send raw key bytes to pane 0 of ``name``.

        rmux interprets control sequences (``C-a``, ``Enter``, ``Up``,
        and raw bytes like ``\\x03``) — for literal text use
        ``send_text``.  This is what the attach-mode WebSocket bridge
        forwards browser keystrokes through.
        """
        await self._run("send-keys", "-t", f"{name}:0.0", keys)

    async def send_text(self, name: str, text: str) -> None:
        """Send literal text to pane 0 of ``name`` (no key interpretation).

        Used by tests and by callers that need to type a string verbatim
        without rmux interpreting it as control sequences.  Internally
        passes the ``-l`` flag to ``send-keys``.
        """
        await self._run("send-keys", "-t", f"{name}:0.0", "-l", text)

    async def list_sessions(self) -> list[str]:
        """Return the list of session names on this server.

        Returns an empty list (not raising) if no server is running yet
        — the common "task service just started, no sessions yet" case.
        """
        out = await self._run(
            "list-sessions", "-F", "#{session_name}",
            swallow_no_server=True,
            capture=True,
        )
        return [line for line in out.splitlines() if line.strip()]

    async def pipe_pane(self, name: str, fifo_path: str | Path) -> None:
        """Stream pane 0's output to ``fifo_path`` until further notice.

        Equivalent to ``rmux pipe-pane -t name:0.0 -o "cat >> FIFO"``.
        The ``-o`` flag toggles: calling pipe_pane on an already-piped
        pane disables the previous pipe and starts a new one.

        The bridge layer (R1) makes one pipe_pane call at session
        creation and leaves it on for the session's lifetime — R0a
        confirmed pipe-pane forwards each tick within ~2 ms.
        """
        # Shell-quote the FIFO path because rmux passes the shell-cmd
        # string to /bin/sh -c — spaces or special chars in the path
        # would otherwise break the redirection.
        cmd = f"cat >> {shlex.quote(str(fifo_path))}"
        await self._run("pipe-pane", "-t", f"{name}:0.0", "-o", cmd)

    async def capture_pane(self, name: str) -> str:
        """Return the current visible contents of pane 0 of ``name``.

        DIAGNOSTIC USE ONLY.  This is a snapshot, not a stream — do NOT
        use it as a fallback for ``pipe_pane`` (the R0a test confirmed
        the two are not interchangeable; capture-pane returns a
        rectangular text dump of the visible viewport with no ANSI
        escapes by default).  Useful for the round-trip test in R0b and
        for postmortem-on-failure inspection.
        """
        return await self._run(
            "capture-pane", "-t", f"{name}:0.0", "-p",
            capture=True,
        )

    # ----- internal helpers -------------------------------------------

    async def _run(
        self,
        *args: str,
        capture: bool = False,
        swallow_no_server: bool = False,
        swallow_missing_session: bool = False,
    ) -> str:
        """Run a single ``rmux -S SOCKET ARGS...`` invocation.

        Returns stdout as a string when ``capture=True``, else "".
        Classifies non-zero exits into the typed error hierarchy.
        """
        cmd = [self._bin, "-S", str(self._sock), *args]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            raise RmuxNotInstalledError(
                f"rmux binary '{self._bin}' not found on PATH"
            ) from e

        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        if proc.returncode == 0:
            return stdout if capture else ""

        # Classify the failure.  These substring matches mirror rmux
        # v0.3.0's actual stderr text; if a future rmux changes the
        # wording, update these patterns (or the wrapper falls back to
        # generic RmuxError, which is still safe — just less precise).
        #
        # Two distinct "daemon not reachable" messages observed:
        #   - "no server running on <sock>"  (rmux knows the server hasn't
        #     been started since the last reboot/kill — common on the
        #     happy path of ``list-sessions`` against a fresh socket dir)
        #   - "error connecting to <sock> (No such file or directory)"
        #     (the socket file itself is missing — same outcome from
        #     the caller's perspective; the daemon isn't there)
        lower = stderr.lower()
        no_daemon = (
            "no server" in lower
            or ("error connecting to" in lower and "no such file" in lower)
        )
        if no_daemon:
            if swallow_no_server:
                return ""
            raise RmuxDaemonError(stderr)
        if (
            "can't find session" in lower
            or "session not found" in lower
            or "no such session" in lower
        ):
            if swallow_missing_session:
                return ""
            raise RmuxSessionError(stderr)
        raise RmuxError(f"rmux {args[0]} failed (rc={proc.returncode}): {stderr}")
