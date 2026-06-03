"""P7 — Evidence / compliance / drill acceptance tests.

Verifies the closing-deliverables of Epic #26 v1.0 exist + contain
the mandatory structural contract. Document quality is reviewed in
the PR; tests gate structure + drill executability.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Doc deliverables not shipped with closed epic #34. Tracked at #160.
_MISSING_DOC_REASON = "doc not yet shipped — tracked at #160"


def _doc_missing(rel: str) -> bool:
    return not (_REPO_ROOT / rel).is_file()


def _read(repo_root: Path, rel: str) -> str:
    p = repo_root / rel
    assert p.is_file(), f"missing required file: {rel}"
    return p.read_text(encoding="utf-8")


@pytest.mark.evidence
@pytest.mark.xfail(
    _doc_missing("guides/compliance/soc2-evidence.md"),
    reason=_MISSING_DOC_REASON,
    strict=False,
)
def test_soc2_evidence_doc_exists(repo_root) -> None:
    """soc2-evidence.md exists + covers CC1..CC9 + A1 + C1."""
    body = _read(repo_root, "guides/compliance/soc2-evidence.md")
    required_headings = [
        "## CC1:", "## CC2:", "## CC3:", "## CC4:", "## CC5:",
        "## CC6:", "## CC7:", "## CC8:", "## CC9:",
        "## A1:", "## C1:",
        "## Documented limitations",
    ]
    for h in required_headings:
        assert h in body, f"missing heading: {h!r}"
    # Must cross-reference at least 3 other guides (the audit trail,
    # the threat model, and the KMS rotation runbook).
    cross_refs = ["audit-trail.md", "threat-model.md", "kms-rotation-runbook.md"]
    for ref in cross_refs:
        assert ref in body, f"SOC2 doc must reference {ref}"


@pytest.mark.evidence
@pytest.mark.xfail(
    _doc_missing("guides/compliance/dpia-data-flow.md"),
    reason=_MISSING_DOC_REASON,
    strict=False,
)
def test_dpia_doc_exists(repo_root) -> None:
    """dpia-data-flow.md exists with PII inventory + lawful-basis matrix
    + mermaid data-flow diagram."""
    body = _read(repo_root, "guides/compliance/dpia-data-flow.md")
    required = [
        "Lawful basis",
        "Data inventory",
        "Data-flow diagram",
        "mermaid",  # the embedded diagram block
        "Art. 17",  # right to erasure
        "Art. 30",  # records of processing
    ]
    for token in required:
        assert token in body, f"DPIA missing required token: {token!r}"


@pytest.mark.evidence
@pytest.mark.xfail(
    _doc_missing("guides/security/threat-model.md"),
    reason=_MISSING_DOC_REASON,
    strict=False,
)
def test_threat_model_doc_exists(repo_root) -> None:
    """threat-model.md exists with STRIDE pass + documented limitations."""
    body = _read(repo_root, "guides/security/threat-model.md")
    stride = ["Spoofing", "Tampering", "Repudiation", "Information disclosure",
              "Denial of service", "Elevation of privilege"]
    for t in stride:
        assert t in body, f"STRIDE category missing: {t}"
    # Each category should have at least one threat row.
    assert "### S — Spoofing" in body
    assert "### T — Tampering" in body
    assert "Documented limitations" in body


@pytest.mark.evidence
@pytest.mark.xfail(
    _doc_missing("guides/deployment/runbook.md"),
    reason=_MISSING_DOC_REASON,
    strict=False,
)
def test_deployment_runbook_exists(repo_root) -> None:
    """runbook.md exists with EKS / AKS / GKE / vanilla install paths."""
    body = _read(repo_root, "guides/deployment/runbook.md")
    for path in ["EKS", "AKS", "GKE", "Vault"]:
        assert path in body, f"runbook missing path: {path}"
    # Must include a verification gate section.
    assert "Verification gate" in body or "verification gate" in body.lower()


@pytest.mark.evidence
@pytest.mark.xfail(
    _doc_missing("guides/deployment/upgrade.md"),
    reason=_MISSING_DOC_REASON,
    strict=False,
)
def test_upgrade_guide_exists(repo_root) -> None:
    """upgrade.md exists with v0.x→v1.0 procedure + rollback."""
    body = _read(repo_root, "guides/deployment/upgrade.md")
    required = [
        "v0.x", "v1.0",
        "forward-only",
        "pg_dump",
        "Rollback",
    ]
    for token in required:
        assert token in body, f"upgrade guide missing: {token!r}"


@pytest.mark.evidence
def test_backup_restore_drill_script(repo_root) -> None:
    """backup-restore.sh exists, is executable, --help works."""
    path = repo_root / "scripts/drills/backup-restore.sh"
    assert path.is_file()
    assert os.access(path, os.X_OK), "backup-restore.sh must be executable"
    result = subprocess.run(
        [str(path), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "USAGE" in result.stdout or "Usage" in result.stdout


@pytest.mark.evidence
def test_upgrade_in_place_drill_script(repo_root) -> None:
    path = repo_root / "scripts/drills/upgrade-in-place.sh"
    assert path.is_file()
    assert os.access(path, os.X_OK)
    result = subprocess.run(
        [str(path), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "USAGE" in result.stdout


@pytest.mark.evidence
def test_image_mirroring_drill_script(repo_root) -> None:
    path = repo_root / "scripts/drills/image-mirroring.sh"
    assert path.is_file()
    assert os.access(path, os.X_OK)
    result = subprocess.run(
        [str(path), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "USAGE" in result.stdout
    # Must mention cosign.
    assert "cosign" in result.stdout.lower()


@pytest.mark.evidence
@pytest.mark.xfail(
    _doc_missing("guides/compliance/soc2-evidence.md"),
    reason=_MISSING_DOC_REASON,
    strict=False,
)
def test_guides_readme_indexes_all_new_docs(repo_root) -> None:
    """guides/README.md links to the 5 new P7 docs."""
    body = _read(repo_root, "guides/README.md")
    required_links = [
        "compliance/soc2-evidence.md",
        "compliance/dpia-data-flow.md",
        "security/threat-model.md",
        "deployment/runbook.md",
        "deployment/upgrade.md",
    ]
    for link in required_links:
        assert link in body, f"guides/README.md must link to {link}"


@pytest.mark.evidence
def test_backup_restore_drill_dry_run(repo_root, tmp_path) -> None:
    """All 3 drill scripts succeed in --dry-run mode (CI-safe)."""
    drills = [
        "scripts/drills/backup-restore.sh",
        "scripts/drills/upgrade-in-place.sh",
        "scripts/drills/image-mirroring.sh",
    ]
    for rel in drills:
        path = repo_root / rel
        result = subprocess.run(
            [str(path), "--dry-run"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, (
            f"{rel} --dry-run failed: rc={result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "DRY-RUN" in result.stdout, f"{rel} should print [DRY-RUN] markers"
        assert "drill complete" in result.stdout.lower()
