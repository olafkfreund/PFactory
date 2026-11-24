"""P0.6 — pinned bases resolve for the amd64 release platform.

The upstream Node bases must also resolve on arm64. The PFactory
runtime base follows the amd64 release workflow.
"""

import json
import re
import shutil
import subprocess

import pytest

from tests.docker.helpers import (
    DOCKERFILE_PATH,
    RegistryUnavailableError,
    inspect_raw_manifest,
)


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
    """P0.6 — all bases support amd64 and the Node bases also support arm64.

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
        # A registry that never answers (timeout, refused, 5xx, throttle) is an
        # environment that cannot answer the question, like docker being absent
        # above — skip, don't fail a required gate on someone else's outage
        # (#744). A registry that ANSWERS with a refusal still fails, via
        # ManifestInspectError.
        try:
            raw = inspect_raw_manifest(ref)
        except RegistryUnavailableError as exc:
            pytest.skip(f"registry unavailable, multi-arch not verified: {exc}")
        manifest = json.loads(raw)
        arches = {
            entry.get("platform", {}).get("architecture")
            for entry in manifest.get("manifests", [])
        }
        assert "amd64" in arches, \
            f"{ref} does not include linux/amd64 (found arches: {sorted(a for a in arches if a)})"
        if not ref.startswith("ghcr.io/olafkfreund/pfactory-base:"):
            assert "arm64" in arches, \
                f"{ref} does not include linux/arm64 (found arches: {sorted(a for a in arches if a)})"
