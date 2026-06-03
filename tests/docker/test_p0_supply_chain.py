"""P0.7 / P0.8 / P0.9 / P0.10 — supply-chain hardening:
digest pinning, Trivy scan, SBOM attestation, cosign signing."""

import json
import os
import subprocess

import pytest

from tests.docker.helpers import DOCKERFILE_PATH, REPO_ROOT

IN_CI = os.environ.get("CI", "").lower() == "true"


@pytest.mark.docker
def test_base_images_pinned_by_digest() -> None:
    """P0.7 — every `FROM` line uses `@sha256:...`, not a floating tag."""
    content = DOCKERFILE_PATH.read_text()
    from_lines = [
        line.strip() for line in content.splitlines()
        if line.strip().upper().startswith("FROM ")
    ]
    assert from_lines, "no FROM lines found in Dockerfile"
    for line in from_lines:
        assert "@sha256:" in line, \
            f"FROM line is not digest-pinned: {line!r}"


@pytest.mark.docker
@pytest.mark.slow
@pytest.mark.skipif(not IN_CI, reason="Trivy scan enforced only in CI (needs trivy CLI on PATH)")
def test_trivy_no_high_critical(built_image: str) -> None:
    """P0.8 — Trivy scan reports zero HIGH/CRITICAL vulnerabilities."""
    result = subprocess.run(
        ["trivy", "image", "--severity", "HIGH,CRITICAL", "--format", "json", built_image],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, f"trivy failed: {result.stderr}"
    report = json.loads(result.stdout)
    findings = []
    for target in report.get("Results", []) or []:
        findings.extend(target.get("Vulnerabilities", []) or [])
    assert not findings, (
        f"Trivy found {len(findings)} HIGH/CRITICAL vulns: "
        f"{[(v.get('VulnerabilityID'), v.get('Severity')) for v in findings[:5]]}"
    )


@pytest.mark.docker
@pytest.mark.slow
def test_sbom_generates_valid_spdx(built_image: str) -> None:
    """P0.9 — Syft generates a valid SPDX-JSON SBOM for the image.

    Verifies the *deliverable* (a parseable SBOM exists with the components
    we expect) rather than the *delivery mechanism* (cosign attestation in
    a registry). The latter is a release-time concern that lives in
    release.yml and is verified post-publish, not on every PR.

    Skipped locally when Syft isn't installed; CI installs it via
    `anchore/sbom-action`.
    """
    import shutil
    if shutil.which("syft") is None:
        pytest.skip("syft not installed on this host (CI installs it via anchore/sbom-action)")

    result = subprocess.run(
        ["syft", "scan", built_image, "-o", "spdx-json"],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, f"syft failed: {result.stderr[-1000:]}"

    sbom = json.loads(result.stdout)

    # Sanity check: SPDX format markers
    assert sbom.get("spdxVersion", "").startswith("SPDX-"), \
        f"unexpected SPDX version: {sbom.get('spdxVersion')!r}"
    assert isinstance(sbom.get("packages"), list), "no packages array in SBOM"
    assert len(sbom["packages"]) > 0, "SBOM contains zero packages"

    # Verify our key components are catalogued. Match against package names
    # case-insensitively to survive ecosystem-specific naming differences.
    pkg_names = {pkg.get("name", "").lower() for pkg in sbom["packages"]}
    assert "fastapi" in pkg_names, \
        f"fastapi not catalogued (have {sorted(pkg_names)[:10]}...)"


@pytest.mark.docker
def test_release_workflow_signs_with_cosign() -> None:
    """P0.10 — release.yml is configured to sign + attest + self-verify.

    The original test invoked `cosign verify` on `built_image`, which can
    only work post-publish (cosign queries the registry's tlog). PR-time
    CI doesn't push, so the only meaningful PR-side gate is a static check
    of release.yml. The actual signature verification is enforced inside
    release.yml itself (the 'Verify signature (release self-test)' step
    fails the release if the signature doesn't verify).
    """
    release_yml = REPO_ROOT / ".github" / "workflows" / "release.yml"
    content = release_yml.read_text()

    assert "sigstore/cosign-installer" in content, \
        "release.yml does not install cosign"

    assert "cosign sign" in content, \
        "release.yml does not invoke `cosign sign`"

    # Keyless = no --key argument anywhere on the sign line(s).
    sign_command_lines = [
        line for line in content.splitlines()
        if "cosign sign" in line and "--key" in line
    ]
    assert not sign_command_lines, \
        f"cosign sign appears to use a key (not keyless): {sign_command_lines}"

    assert "cosign verify" in content, \
        "release.yml does not verify the signature post-sign (self-test)"

    assert "id-token: write" in content, \
        "release.yml lacks `id-token: write` permission required for cosign keyless"

    assert "cosign attest" in content, \
        "release.yml does not attach an SBOM attestation"
