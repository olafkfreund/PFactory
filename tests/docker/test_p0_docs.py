"""P0.11 — image-mirroring documentation."""

from pathlib import Path

import pytest

DOC = Path(__file__).resolve().parents[2] / "guides" / "operations" / "image-mirroring.md"

# Doc not shipped with closed epic #27. Tracked at #160.
_MISSING_DOC_REASON = "image-mirroring.md not shipped — tracked at #160"


@pytest.mark.docker
@pytest.mark.xfail(not DOC.exists(), reason=_MISSING_DOC_REASON, strict=False)
def test_mirroring_doc_exists() -> None:
    """The image-mirroring guide is present at the expected path."""
    assert DOC.exists(), f"{DOC} not found"


@pytest.mark.docker
@pytest.mark.xfail(not DOC.exists(), reason=_MISSING_DOC_REASON, strict=False)
def test_mirroring_doc_covers_cosign_copy() -> None:
    """The guide explains `cosign copy` and digest verification post-mirror."""
    if not DOC.exists():
        pytest.fail(f"{DOC} not found")
    content = DOC.read_text().lower()
    assert "cosign copy" in content, \
        "guide must explain `cosign copy` for signature preservation"
    assert "sha256" in content, \
        "guide should mention digest verification post-mirror"
