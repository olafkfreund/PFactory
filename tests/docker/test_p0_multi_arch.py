"""P0.6 — Dockerfile is multi-arch-capable (amd64 + arm64).

Design note: this test deliberately does NOT cross-build the full image
on every PR. Cross-arch builds via QEMU emulation take 10+ minutes
because every native Python wheel + npm install runs under emulation,
and the value on every PR is marginal — the real multi-arch artifact
emission happens in release.yml at tag push time, where 15 minutes is
acceptable.

What we DO verify here:
  1. `docker buildx` is available
  2. Every base image referenced in the Dockerfile is itself multi-arch
     (its manifest list contains both amd64 and arm64 entries)

That's the bank-grade contract: "this Dockerfile's base layers can be
resolved on both architectures." Combined with our policy that the
Dockerfile contains no arch-specific RUN steps (apk picks the right
arch package automatically), this proves multi-arch buildability
without paying the cross-emulation cost.
"""

import json
import re
import shutil
import subprocess

import pytest

from tests.docker.helpers import DOCKERFILE_PATH


def _extract_base_image_digests() -> list[str]:
    """Pull every `FROM <image>@sha256:...` reference from the Dockerfile."""
    text = DOCKERFILE_PATH.read_text()
    refs: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("FROM "):
            continue
        # `FROM image:tag@sha256:abc AS stage` — capture up to the first
        # whitespace after the image reference.
        match = re.match(r"FROM\s+(\S+)", stripped, re.IGNORECASE)
        if match:
            refs.append(match.group(1))
    return refs


@pytest.mark.docker
def test_multi_arch_buildable() -> None:
    """P0.6 — base images we pin support both linux/amd64 and linux/arm64.

    Proves multi-arch capability without cross-building. Fast (~2 s).
    """
    if shutil.which("docker") is None:
        pytest.skip("docker not available")
    bx = subprocess.run(
        ["docker", "buildx", "version"],
        capture_output=True, text=True, timeout=10,
    )
    if bx.returncode != 0:
        pytest.skip("docker buildx not installed")

    refs = _extract_base_image_digests()
    assert refs, "no FROM lines found in Dockerfile"

    for ref in refs:
        # `docker buildx imagetools inspect --raw` returns the image-index
        # manifest list as JSON. Multi-arch images have `manifests[]` with
        # one entry per platform.
        result = subprocess.run(
            ["docker", "buildx", "imagetools", "inspect", "--raw", ref],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, (
            f"`docker buildx imagetools inspect --raw {ref}` failed:\n"
            f"--- stderr ---\n{result.stderr[-1000:]}"
        )
        manifest = json.loads(result.stdout)
        arches = {
            entry.get("platform", {}).get("architecture")
            for entry in manifest.get("manifests", [])
        }
        assert "amd64" in arches, \
            f"{ref} does not include linux/amd64 (found arches: {sorted(a for a in arches if a)})"
        assert "arm64" in arches, \
            f"{ref} does not include linux/arm64 (found arches: {sorted(a for a in arches if a)})"
