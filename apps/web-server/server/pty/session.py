"""
PTY Session - Wraps a pseudo-terminal for WebSocket communication.

Each session represents one terminal instance that can be connected
to via WebSocket for bidirectional I/O.
"""

import asyncio
import os
import signal
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

# Import ptyprocess for PTY handling (cross-platform)
try:
    from ptyprocess import PtyProcess, PtyProcessUnicode
except ImportError:
    PtyProcess = None
    PtyProcessUnicode = None


@dataclass
class PTYSession:
    """A single PTY (pseudo-terminal) session."""

    id: str = field(default_factory=lambda: str(uuid4()))
    cwd: str = field(default_factory=lambda: str(Path.home()))
    shell: str = field(default_factory=lambda: os.environ.get("SHELL", "/bin/bash"))
    cols: int = 80
    rows: int = 24
    env: dict[str, str] | None = None
    created_at: datetime = field(default_factory=datetime.now)

    # Internal state
    _pty: Optional["PtyProcessUnicode"] = field(default=None, repr=False)
    _output_buffer: str = field(default="", repr=False)
    _closed: bool = field(default=False, repr=False)

    def __post_init__(self):
        if PtyProcess is None:
            raise RuntimeError("ptyprocess package is required. Install with: pip install ptyprocess")

    def start(self) -> None:
        """Start the PTY process."""
        if self._pty is not None:
            raise RuntimeError("PTY already started")

        # Prepare environment
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        # Remove CLAUDECODE to allow launching claude CLI from the terminal
        env.pop("CLAUDECODE", None)
        if self.env:
            env.update(self.env)

        # Start the PTY process
        self._pty = PtyProcessUnicode.spawn(
            [self.shell, "-l"],  # Login shell
            dimensions=(self.rows, self.cols),
            cwd=self.cwd,
            env=env,
        )
        self._closed = False

    def write(self, data: str) -> None:
        """Write data to the PTY (user input)."""
        if self._pty is None or self._closed:
            raise RuntimeError("PTY not running")
        self._pty.write(data)

    def read(self, size: int = 4096) -> str:
        """Read data from the PTY (terminal output).

        This is a blocking call - use read_async for async contexts.
        Returns empty string if no data available.
        """
        if self._pty is None or self._closed:
            return ""

        try:
            # Non-blocking read
            import select
            if select.select([self._pty.fd], [], [], 0)[0]:
                return self._pty.read(size)
            return ""
        except EOFError:
            self._closed = True
            return ""
        except Exception:
            return ""

    async def read_async(self, size: int = 4096) -> str:
        """Async version of read - runs blocking read in executor."""
        if self._pty is None or self._closed:
            return ""

        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._pty.read, size)
        except EOFError:
            self._closed = True
            return ""
        except Exception:
            return ""

    def resize(self, cols: int, rows: int) -> None:
        """Resize the terminal."""
        if self._pty is None or self._closed:
            return

        self.cols = cols
        self.rows = rows
        self._pty.setwinsize(rows, cols)

    def is_alive(self) -> bool:
        """Check if the PTY process is still running."""
        if self._pty is None:
            return False
        return self._pty.isalive()

    def close(self) -> None:
        """Close the PTY session."""
        if self._pty is None:
            return

        self._closed = True

        try:
            if self._pty.isalive():
                self._pty.terminate(force=True)
        except Exception:
            pass

        self._pty = None

    def send_signal(self, sig: int) -> None:
        """Send a signal to the PTY process."""
        if self._pty is None or self._closed:
            return

        try:
            self._pty.kill(sig)
        except Exception:
            pass

    def interrupt(self) -> None:
        """Send Ctrl+C (SIGINT) to the PTY."""
        self.send_signal(signal.SIGINT)

    def to_dict(self) -> dict:
        """Serialize session to dictionary."""
        return {
            "id": self.id,
            "cwd": self.cwd,
            "shell": self.shell,
            "cols": self.cols,
            "rows": self.rows,
            "created_at": self.created_at.isoformat(),
            "is_alive": self.is_alive(),
        }


async def create_pty_reader(
    session: PTYSession,
    on_output: Callable[[str], None],
    check_interval: float = 0.01,
) -> None:
    """Continuously read from PTY and call callback with output.

    This runs until the session is closed or the PTY exits.
    """
    while session.is_alive() and not session._closed:
        try:
            output = await session.read_async(4096)
            if output:
                if asyncio.iscoroutinefunction(on_output):
                    await on_output(output)
                else:
                    on_output(output)
            else:
                # No data, wait a bit before trying again
                await asyncio.sleep(check_interval)
        except Exception:
            break
