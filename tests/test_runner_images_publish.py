"""A runner-image build that publishes nothing is not coverage (#449).

`runner-images.yml` built six images, loaded them into the local daemon for a
hello-world smoke, and threw them away. Meanwhile the consumers resolve those
images by BARE tag -- `DockerRunner.DEFAULT_IMAGE`, and `runtime.image` in every
`frameworks/*/descriptor.yaml` -- and `guides/shipping.md` tells the operator to
`docker build -t pfactory-runner-pytest:latest` by hand. So nothing tied the tag
a lane executes to a commit: CI built an image, proved it worked, and discarded
it, while the lane ran whatever was last built on that machine.

That is the shape of TFactory#886 (a merged fix that never reaches the image the
lane runs) with the publish step missing rather than the workflow, and it is why
Factory#524 listed this workflow as unsignable -- there was nothing to sign.

These tests hold the three properties that make the build meaningful:
  1. every runner directory is in the build matrix (nothing silently unbuilt),
  2. what is built on main is published,
  3. what is published is signed, in the same workflow.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[1]
_WORKFLOW = _REPO / ".github" / "workflows" / "runner-images.yml"
_DOCKER_DIR = _REPO / "docker"


def _workflow() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _workflow()["jobs"]["build"]["steps"]


def _runner_dirs() -> set[str]:
    return {
        p.name.removeprefix("pfactory-runner-")
        for p in _DOCKER_DIR.glob("pfactory-runner-*")
        if p.is_dir()
    }


pytestmark = pytest.mark.skipif(not _WORKFLOW.is_file(), reason="runner-images.yml not present")

# What the push step must publish, exactly. The owner and the runner come from
# workflow expressions so the same two lines cover all six matrix entries.
_IMAGE = "ghcr.io/${{ github.repository_owner }}/pfactory-runner-${{ matrix.runner }}"
_EXPECTED_PUSH_TAGS = [f"{_IMAGE}:${{{{ github.sha }}}}", f"{_IMAGE}:latest"]


def test_the_matrix_covers_every_runner_directory() -> None:
    """A directory outside the matrix is a Dockerfile nobody ever builds."""
    matrix = set(_workflow()["jobs"]["build"]["strategy"]["matrix"]["runner"])
    dirs = _runner_dirs()
    assert dirs - matrix == set(), (
        f"docker/pfactory-runner-* dirs missing from the matrix: {sorted(dirs - matrix)}"
    )
    assert matrix - dirs == set(), f"matrix entries with no directory: {sorted(matrix - dirs)}"


def test_images_are_published_on_main() -> None:
    """Built-and-discarded is worse than absent: it reads as coverage."""
    pushes = [s for s in _steps() if s.get("with", {}).get("push") is True]
    assert pushes, (
        "runner-images.yml has no step with `push: true`. It builds images and "
        "publishes none, so the lanes keep running whatever was last built by "
        "hand (#449)."
    )
    for step in pushes:
        tags = [t.strip() for t in step["with"]["tags"].splitlines() if t.strip()]
        # Exact set equality, not a substring or prefix check on each tag.
        # Stronger (a bare tag alongside a qualified one cannot slip through, and
        # neither can a missing commit pin), and it does not read as URL host
        # validation, which is what CodeQL's py/incomplete-url-substring-
        # sanitization exists to catch and correctly flagged the first version of
        # this assertion.
        assert tags == _EXPECTED_PUSH_TAGS, (
            f"push tags are not exactly the registry-qualified commit-pinned pair.\n"
            f"  expected: {_EXPECTED_PUSH_TAGS}\n"
            f"  found:    {tags}\n"
            "Both are load-bearing: the :<sha> tag is what ties a lane's image to "
            "a commit, and :latest is what the consumers resolve."
        )
        assert "refs/heads/main" in step.get("if", ""), (
            "publishing must be gated on push-to-main; a PR (including from a "
            "fork) must not write to the registry"
        )


def test_permissions_allow_publishing_and_keyless_signing() -> None:
    perms = _workflow()["permissions"]
    assert perms.get("packages") == "write", "cannot publish to GHCR without packages: write"
    assert perms.get("id-token") == "write", "cosign keyless needs id-token: write (Factory#524)"


def test_published_images_are_signed_and_self_tested() -> None:
    """Factory#524: do not land the push without the signature."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "cosign sign" in text, (
        "images are pushed but never signed -- exactly the state Factory#524 exists to clean up"
    )
    assert "cosign verify" in text, (
        "no self-test that the signature is the identity a gate will pin"
    )

    # Anchored at BOTH ends, and naming this workflow. A self-test looser than
    # the gate it models passes on signatures the gate rejects (Factory#522).
    identity = re.search(r'--certificate-identity-regexp\s+"([^"]+)"', text)
    assert identity, "the verify self-test does not pin a certificate identity"
    pattern = identity.group(1)
    assert pattern.startswith("^"), f"identity pattern is not anchored at the start: {pattern!r}"
    assert pattern.endswith("$"), f"identity pattern is not anchored at the end: {pattern!r}"
    assert "runner-images" in pattern, f"identity does not name this workflow: {pattern!r}"


def test_signing_is_by_digest_not_tag() -> None:
    """A tag can be moved; a digest cannot. cosign signatures are digest-keyed."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert 'DIGEST: "${{ steps.push.outputs.digest }}"' in text
    assert 'cosign sign --yes "${IMAGE}@${DIGEST}"' in text
