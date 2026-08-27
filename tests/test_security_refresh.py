"""Any image that upgrades packages must be able to re-run that upgrade.

CFactory#440 hit this twice, one level apart:

1. The backend image ran `apt-get upgrade`, but every workflow built it with
   `cache-from`, so the layer was served from cache and never re-ran. The
   upgrade was real; its result was frozen at whatever the distro shipped the
   day the layer was first built. Trivy failed on CVE-2026-14456 (openssl).
2. Fixing that left the FRONTEND image still failing -- it had no upgrade
   layer at all. A guard that only looked at the root Dockerfile could not see
   it.

So the rule is per-build, not per-repo: follow each cached build step to the
Dockerfile it names, and if that Dockerfile upgrades packages, require the
cache-bust arg on both sides. Images with no upgrade layer are out of scope
here (a separate policy question) rather than silently passing as "fine".
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOWS = _ROOT / ".github/workflows"
_STEP = re.compile(r"^(\s*)- (?:name|id|uses):.*$", re.M)
_FILE = re.compile(r"^\s*file:\s*(\S+)\s*$", re.M)
_UPGRADE = re.compile(r"(apt-get|apk)\s+upgrade")


def _cached_builds():
    """Yield (workflow, dockerfile_path, step_body) for cached image builds."""
    for wf in sorted(_WORKFLOWS.glob("*.yml")):
        text = wf.read_text()
        steps = list(_STEP.finditer(text))
        for i, m in enumerate(steps):
            end = steps[i + 1].start() if i + 1 < len(steps) else len(text)
            body = text[m.start() : end]
            fm = _FILE.search(body)
            if not fm or "cache-from" not in body:
                continue
            yield wf.name, _ROOT / fm.group(1), body


def test_every_cached_upgrade_layer_can_be_rebuilt():
    offenders = []
    for wf, dockerfile, body in _cached_builds():
        if not dockerfile.is_file():
            continue
        if not _UPGRADE.search(dockerfile.read_text()):
            continue  # no upgrade layer to freeze
        if "SECURITY_REFRESH" not in body:
            offenders.append(f"{wf} -> {dockerfile.relative_to(_ROOT)} (build arg missing)")

    assert not offenders, (
        "cached build of an image that upgrades packages, with no cache-bust: "
        f"the upgrade will never re-run: {offenders}"
    )


def test_the_arg_is_consumed_where_it_is_declared():
    """Declared but never referenced busts nothing."""
    inert = []
    for _wf, dockerfile, _body in _cached_builds():
        if not dockerfile.is_file():
            continue
        body = dockerfile.read_text()
        if "ARG SECURITY_REFRESH" in body and "${SECURITY_REFRESH}" not in body:
            inert.append(str(dockerfile.relative_to(_ROOT)))

    assert not inert, f"SECURITY_REFRESH declared but never referenced: {inert}"


def test_the_scan_is_not_vacuous():
    """A checker that inspects nothing looks identical to one that finds nothing."""
    assert list(_cached_builds()), "no cached image builds found at all"
