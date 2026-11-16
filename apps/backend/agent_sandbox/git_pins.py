"""Git command-scope pins: exec-bearing config keys, merge drivers, filters."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping

RunFn = Callable[[list[str]], tuple[int, str]]

# (key, value) pins; None means the editor/pager argument (#1689, #1717, #1745).
_GIT_PINS: tuple[tuple[str, str | None], ...] = (
    ("core.hooksPath", "/dev/null"),
    ("core.fsmonitor", "false"),
    ("protocol.ext.allow", "never"),
    ("core.sshCommand", "ssh"),
    ("core.pager", None),
    ("core.editor", None),
    ("core.askPass", ""),
    ("credential.helper", ""),  # empty resets every inherited helper
    ("credential.https://github.com.helper", "!gh auth git-credential"),
    ("sequence.editor", None),
)


def pin_git_config(
    env: dict[str, str], *, editor: str = "true", pager: str = "cat"
) -> dict[str, str]:
    """Append git command-scope pins and default ``GIT_PAGER`` onto ``env``."""
    try:
        n = max(int(env.get("GIT_CONFIG_COUNT", "0")), 0)
    except ValueError:
        n = 0
    for i, (key, val) in enumerate(_GIT_PINS):
        env[f"GIT_CONFIG_KEY_{n + i}"] = key
        env[f"GIT_CONFIG_VALUE_{n + i}"] = (
            pager if key == "core.pager" else editor if val is None else val
        )
    env["GIT_CONFIG_COUNT"] = str(n + len(_GIT_PINS))
    env.setdefault("GIT_PAGER", pager)
    return env


_MERGE_DRIVER_KEYS = (
    "git",
    "config",
    "-z",
    "--includes",
    "--name-only",
    "--list",
)


def merge_driver_pins(
    cwd: str | os.PathLike[str],
    run: RunFn | None = None,
    env: Mapping[str, str] | None = None,
    check: Callable[[str | os.PathLike[str], RunFn | None], object] | None = None,
) -> list[str]:
    """Return empty command-scope pins for merge drivers and content filters.

    An agent can define ``merge.<name>.driver`` in ``.git/config`` and bind it in
    ``.git/info/attributes``; overriding each key with an empty value makes git
    report a conflict instead of running the program. The same pins disable
    ``filter.<name>.clean``, ``.smudge`` and ``.process`` commands; ``.required``
    remains untouched. Callers fail closed on ``RuntimeError``. Keys are
    filtered in Python, not by ``--get-regexp``: under a UTF-8 locale git's
    regex skips a non-UTF-8 subsection. ``run`` must decode with
    ``surrogateescape`` so such a key round-trips to argv. ``check`` is an
    optional integrity check (it raises RuntimeError) run first. Pass ``env``
    (for example ``child_env(policy, agent=True)``) or ``run``; with neither it
    raises ``ValueError``, so this module never imports ``env`` (import cycle).
    """
    if run is None and env is None:
        raise ValueError("merge_driver_pins needs env or run")
    if check is not None:
        check(cwd, run)
    argv = list(_MERGE_DRIVER_KEYS)
    try:
        if run is not None:
            rc, out = run(argv)
        else:
            proc = subprocess.run(  # noqa: S603 - fixed argv
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                errors="surrogateescape",
                env=dict(env or {}),
                timeout=60,
                check=False,
            )
            rc, out = proc.returncode, proc.stdout
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise RuntimeError(f"git config failed: {exc}") from exc
    if rc != 0:
        raise RuntimeError(f"git config exited {rc}")
    pins: list[str] = []
    for key in out.split("\0"):
        if not (
            (
                key.startswith("merge.")
                and key.endswith(".driver")
                and len(key) >= len("merge..driver")
            )
            or (key.startswith("filter.") and key.endswith((".clean", ".smudge", ".process")))
        ):
            continue
        if "=" in key:
            raise RuntimeError(f"git config key contains '=': {key!r}")
        pins.extend(("-c", f"{key}="))
    return pins
