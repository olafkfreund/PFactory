"""P0.7 / P0.8 / P0.9 / P0.10 — supply-chain hardening:
digest pinning, Trivy scan, SBOM attestation, cosign signing."""

import json
import os
import subprocess

import pytest

from tests.docker.helpers import DOCKERFILE_PATH, REPO_ROOT, docker_run

IN_CI = os.environ.get("CI", "").lower() == "true"


def _findings(report: dict, what: str) -> list:
    """HIGH/CRITICAL vulnerabilities in a Trivy report, having first proved it read something.

    A Trivy run that matches nothing -- a renamed lockfile, a wrong path, an
    image whose package manifests never reached a scannable layer -- exits 0
    with an empty ``Results`` and therefore zero vulnerabilities. That is
    byte-identical to a genuinely clean scan, so the gate cannot tell "no
    vulnerabilities" from "no scan" (PFactory#595).

    The package count is what separates them. Measured against trivy 0.74.0:

        real package-lock.json      rc=0  Results=1  pkgs=335
        the same file renamed       rc=0  Results=0  pkgs=0
        an empty directory          rc=0  Results=0  pkgs=0
        alpine:3.19 image scan      rc=0  Results=1  pkgs=15

    ``Packages`` is populated without ``--list-all-pkgs`` for both ``fs`` and
    ``image``, so asserting on it costs nothing and does not weaken the
    vulnerability assertion that follows.
    """
    results = report.get("Results") or []
    assert results, (
        f"Trivy returned no Results for {what}. It exited 0 having scanned "
        "nothing, which is not the same as finding nothing."
    )
    packages = sum(len(t.get("Packages") or []) for t in results)
    assert packages > 0, (
        f"Trivy resolved 0 packages for {what} across {len(results)} result(s). "
        "A scan that examined no packages cannot evidence the absence of "
        "vulnerabilities in them."
    )
    return [v for t in results for v in (t.get("Vulnerabilities") or [])]


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
    """P0.8 — Trivy scan reports zero *fixable* HIGH/CRITICAL vulnerabilities.

    `--ignore-unfixed` gates only on CVEs with an upstream patch; the image's
    `apk upgrade` + digest pin clear every fixable HIGH/CRITICAL on rebuild. An
    unfixable finding isn't actionable here and must not wedge CI.
    """
    result = subprocess.run(
        ["trivy", "image", "--severity", "HIGH,CRITICAL", "--ignore-unfixed",
         "--format", "json", built_image],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, f"trivy failed: {result.stderr}"
    report = json.loads(result.stdout)
    findings = _findings(report, "the runtime image")
    assert not findings, (
        f"Trivy found {len(findings)} HIGH/CRITICAL vulns: "
        f"{[(v.get('VulnerabilityID'), v.get('Severity')) for v in findings[:5]]}"
    )


@pytest.mark.docker
@pytest.mark.slow
@pytest.mark.skipif(
    not IN_CI, reason="Trivy scan enforced only in CI (needs trivy CLI on PATH)"
)
def test_frontend_lockfile_no_high_critical() -> None:
    """P0.8b — the shipped frontend's dependencies carry no fixable HIGH/CRITICAL.

    **The blind spot this closes, and why the image scan above cannot.** The
    frontend is built in a separate stage and only the *built assets* are copied
    into the runtime image, so ``node_modules`` — and every package manifest in
    it — never reaches a layer Trivy can see. ``test_trivy_no_high_critical``
    therefore returns green while vulnerable JavaScript is bundled into what
    browsers actually execute. Scanning the lockfile directly covers the
    dependencies that reach users.

    This is a structural consequence of multi-stage builds, not a Trivy
    misconfiguration, so every service with a bundled frontend needs it.

    **It is not hypothetical (Factory#386).** ``GHSA-qwww-vcr4-c8h2`` (HIGH, in
    ``react-router`` 7.18.x) published on 2026-07-24 and turned AIFactory red
    within two days, because AIFactory had this test. This repo carried the
    identical vulnerable dependency and stayed green, and nobody found out until
    someone happened to look. The finding was invisible in two of three services.

    Same ``--ignore-unfixed`` policy as the image scan: gate on what upstream has
    patched, and never wedge CI on something nobody can act on.
    """
    result = subprocess.run(
        [
            "trivy",
            "fs",
            "--scanners",
            "vuln",
            "--severity",
            "HIGH,CRITICAL",
            "--ignore-unfixed",
            "--format",
            "json",
            str(REPO_ROOT / "package-lock.json"),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, f"trivy failed: {result.stderr}"
    report = json.loads(result.stdout)
    findings = _findings(report, "the frontend lockfile")
    assert not findings, (
        f"Trivy found {len(findings)} HIGH/CRITICAL vulns in the frontend lockfile: "
        f"{[(v.get('PkgName'), v.get('VulnerabilityID')) for v in findings[:5]]}"
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


@pytest.mark.docker
def test_pip_absent_from_final_image(built_image: str) -> None:
    """PFactory#679 — pip must not ship in the runtime image.

    The two HIGH Trivy findings this pins (msgpack 1.1.2 GHSA-6v7p-g79w-8964,
    setuptools 70.3.0 CVE-2025-47273) were pip 26.2.1's own vendored copies
    (``pip/_vendor/vendor.txt``), not project dependencies — no requirements
    pin can reach them and no released pip clears them. pip is build-time-only
    in this image, so the remediation is removal, and this test is the pin
    that stops a future base-image bump silently reintroducing it.
    """
    probe = (
        "pips=$(find /usr/lib /home/projects/MagesticAI/.venv "
        "-maxdepth 4 -name 'pip' -type d 2>/dev/null | wc -l); "
        'echo "pip_dirs=$pips"; '
        "/home/projects/MagesticAI/.venv/bin/python -m pip --version 2>&1; "
        'echo "venv_pip_rc=$?"'
    )
    result = docker_run(built_image, "sh", "-c", probe, timeout=30)
    assert result.returncode == 0, f"probe container failed: {result.stderr}"
    # The probe must have measured something: both markers present, or the
    # shell died and this test would otherwise pass on empty output.
    assert "pip_dirs=" in result.stdout and "venv_pip_rc=" in result.stdout, (
        f"probe produced no measurement:\n{result.stdout}\n{result.stderr}"
    )
    assert "pip_dirs=0" in result.stdout, (
        f"a pip package directory still ships in the image:\n{result.stdout}"
    )
    assert "venv_pip_rc=0" not in result.stdout, (
        f"`python -m pip` still works in the venv:\n{result.stdout}"
    )
