"""P0.1 — Chainguard Dockerfile builds cleanly."""

import pytest


@pytest.mark.docker
@pytest.mark.slow
def test_image_builds_clean(built_image: str) -> None:
    """`docker build` against the project Dockerfile exits 0.

    The `built_image` session fixture performs the build; if it returned
    a tag, the build succeeded.
    """
    assert built_image, "built_image fixture returned no tag"
