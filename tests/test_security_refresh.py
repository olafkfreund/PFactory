"""Every cached build of the root Dockerfile must bust the security layer.

CFactory#440: Trivy failed on CVE-2026-14456 (openssl) in an image whose
Dockerfile already ran an upgrade. Both workflows build with `cache-from`, so
that layer was served from cache and never re-ran -- the upgrade was real, its
result frozen at whatever the distro shipped the day the layer was first built.

A build arg that changes daily is what lets the upgrade actually land. This
test fails if any cached build of the root Dockerfile is missing it, which is
how the bug would otherwise creep back in via a new workflow.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOWS = _ROOT / ".github/workflows"
_STEP = re.compile(r"^(\s*)- (?:name|id|uses):.*$", re.M)
_ROOT_DOCKERFILE = re.compile(r"^\s*file:\s*Dockerfile\s*$", re.M)


def _cached_root_builds() -> list[tuple[str, int]]:
    """Return (workflow, line) for every cached build of the root Dockerfile."""
    found = []
    for wf in sorted(_WORKFLOWS.glob("*.yml")):
        text = wf.read_text()
        steps = list(_STEP.finditer(text))
        for i, m in enumerate(steps):
            end = steps[i + 1].start() if i + 1 < len(steps) else len(text)
            body = text[m.start() : end]
            if not _ROOT_DOCKERFILE.search(body):
                continue
            if "cache-from" not in body:
                continue  # an uncached build is always fresh
            if "SECURITY_REFRESH" not in body:
                found.append((wf.name, text[: m.start()].count("\n") + 1))
    return found


def test_no_cached_root_build_ships_a_frozen_security_layer():
    unwired = _cached_root_builds()

    assert not unwired, (
        "cached build(s) of the root Dockerfile without SECURITY_REFRESH -- the "
        f"upgrade layer will be served from cache forever: {unwired}"
    )


def test_the_dockerfile_consumes_the_arg():
    """The build arg is inert unless the Dockerfile declares and uses it."""
    body = (_ROOT / "Dockerfile").read_text()

    assert "ARG SECURITY_REFRESH" in body
    assert "${SECURITY_REFRESH}" in body, "declared but never referenced -- no cache bust"


def test_the_detector_can_actually_fail():
    """Guard against a vacuous pass: the scan must find real build steps."""
    total = 0
    for wf in _WORKFLOWS.glob("*.yml"):
        total += len(_ROOT_DOCKERFILE.findall(wf.read_text()))

    assert total > 0, "no root-Dockerfile builds found at all -- the check is empty"
