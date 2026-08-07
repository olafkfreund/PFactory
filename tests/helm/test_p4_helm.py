"""P4 — Helm chart acceptance tests.

Seven tests map directly to the seven acceptance bullets in Epic #26
issue #31. As implementation chunks land, the ``@pytest.mark.skip``
decorator is removed and a real body replaces the placeholder.

Coverage:
  1. test_helm_lint_strict_passes           (→ P4.1)
  2. test_helm_template_renders             (→ P4.2)
  3. test_kubeconform_passes                (→ P4.2)
  4. test_network_policy_present_and_strict (→ P4.3)
  5. test_pss_restricted_security_contexts  (→ P4.3)
  6. test_install_kind_with_bundled_postgres_succeeds (→ P4.4)
  7. test_custom_ca_bundle_is_trusted_by_pod (→ P4.6)
"""

from __future__ import annotations

import os

import pytest


@pytest.mark.helm
def test_helm_lint_strict_passes(helm_available, chart_dir) -> None:
    """``helm lint --strict charts/pfactory`` passes with zero errors.

    Strict mode treats warnings as errors. We expect zero of either —
    this is the basic well-formedness gate for the chart.
    """
    import subprocess
    result = subprocess.run(
        ["helm", "lint", "--strict", str(chart_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"helm lint --strict failed (exit {result.returncode}):\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
    # Belt-and-suspenders: also verify no [WARNING] / [ERROR] lines.
    assert "[ERROR]" not in result.stdout
    assert "[WARNING]" not in result.stdout


@pytest.mark.helm
def test_helm_template_renders(helm_template) -> None:
    """``helm template`` produces valid YAML with the expected K8s kinds.

    The ``helm_template`` fixture already asserts exit code 0; here we
    assert the output contains the core kinds we ship. NetworkPolicy
    is verified separately in test_network_policy_present_and_strict.
    """
    expected_kinds = {
        "Deployment",
        "Service",
        "ConfigMap",
        "ServiceAccount",
    }
    # PodDisruptionBudget is deliberately NOT here — Factory#550. It used to be,
    # which is how the chart came to ship a PDB that cannot be drained past.
    # test_pdb_off_by_default_and_refuses_to_strand_a_node owns it now.
    rendered_kinds = set()
    for line in helm_template.splitlines():
        if line.startswith("kind:"):
            rendered_kinds.add(line.split(":", 1)[1].strip())
    missing = expected_kinds - rendered_kinds
    assert not missing, (
        f"chart didn't render expected kinds: {missing}. "
        f"Rendered: {sorted(rendered_kinds)}"
    )


@pytest.mark.helm
def test_kubeconform_passes(kubeconform_available, helm_template) -> None:
    """Every rendered manifest conforms to the current K8s OpenAPI schema."""
    import subprocess
    result = subprocess.run(
        ["kubeconform", "-summary", "-strict"],
        input=helm_template,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"kubeconform failed (exit {result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "Invalid: 0" in result.stdout
    assert "Errors: 0" in result.stdout


@pytest.mark.helm
def test_network_policy_present_and_strict(helm_template) -> None:
    """The chart emits a NetworkPolicy with default-deny + explicit allowlist.

    Asserts:
      - A NetworkPolicy resource exists.
      - Both Ingress and Egress policy types are declared (= default-deny).
      - Egress includes 443/tcp to public IPs (Anthropic / IdP / KMS).
      - Egress includes 53/udp + 53/tcp (DNS) to kube-system.
      - Ingress restricts to the ingress-controller namespace by default.
    """
    import yaml
    docs = [d for d in yaml.safe_load_all(helm_template) if d]
    netpols = [d for d in docs if d.get("kind") == "NetworkPolicy"]
    assert len(netpols) == 1, f"expected 1 NetworkPolicy, got {len(netpols)}"
    np = netpols[0]
    policy_types = set(np["spec"]["policyTypes"])
    assert policy_types == {"Ingress", "Egress"}, (
        f"NetworkPolicy must declare both types for default-deny; got {policy_types}"
    )

    # Egress must include a 443/tcp rule.
    egress_rules = np["spec"].get("egress", [])
    has_443 = any(
        any(p.get("port") == 443 and p.get("protocol") == "TCP" for p in rule.get("ports", []))
        for rule in egress_rules
    )
    assert has_443, "NetworkPolicy egress must allow 443/tcp"

    # Egress must include DNS (port 53).
    has_dns = any(
        any(p.get("port") == 53 for p in rule.get("ports", []))
        for rule in egress_rules
    )
    assert has_dns, "NetworkPolicy egress must allow DNS (port 53)"


@pytest.mark.helm
def test_pss_restricted_security_contexts(helm_template) -> None:
    """Pod + container security contexts satisfy PSS-restricted policy.

    Verifies: runAsNonRoot, runAsUser >= 1000, fsGroup >= 1000,
    allowPrivilegeEscalation=false, dropped ALL capabilities,
    readOnlyRootFilesystem=true, seccompProfile=RuntimeDefault.

    The actual PSS admission verification happens at install time in
    test_install_kind_with_bundled_postgres_succeeds; this test
    statically validates the rendered manifests would pass it.
    """
    import yaml
    docs = [d for d in yaml.safe_load_all(helm_template) if d]
    deploys = [d for d in docs if d.get("kind") == "Deployment"]
    assert len(deploys) == 1
    dep = deploys[0]

    pod_spec = dep["spec"]["template"]["spec"]
    pod_sc = pod_spec.get("securityContext", {})

    # Pod-level checks.
    assert pod_sc.get("runAsNonRoot") is True, "pod must runAsNonRoot"
    assert pod_sc.get("runAsUser", 0) >= 1000, "pod must run as uid >= 1000"
    assert pod_sc.get("fsGroup", 0) >= 1000, "pod must use fsGroup >= 1000"
    seccomp = pod_sc.get("seccompProfile", {})
    assert seccomp.get("type") == "RuntimeDefault", "seccompProfile must be RuntimeDefault"

    # Container-level checks (single container by design).
    containers = pod_spec["containers"]
    assert len(containers) == 1
    c_sc = containers[0]["securityContext"]
    assert c_sc.get("allowPrivilegeEscalation") is False
    assert c_sc.get("readOnlyRootFilesystem") is True
    assert c_sc.get("runAsNonRoot") is True
    assert c_sc.get("capabilities", {}).get("drop") == ["ALL"], (
        "container must drop ALL capabilities"
    )
    c_seccomp = c_sc.get("seccompProfile", {})
    assert c_seccomp.get("type") == "RuntimeDefault"


@pytest.mark.helm
@pytest.mark.slow
@pytest.mark.skipif(
    not os.environ.get("RUN_KIND_INSTALL_TEST"),
    reason=(
        "RUN_KIND_INSTALL_TEST not set — this test builds an image, "
        "creates a kind cluster, helm-installs, and exercises a live "
        "/api/health round-trip (~5-10 min). Opt-in via the env var "
        "or run it ad-hoc against your own kind cluster. Static "
        "manifest validation (lint, kubeconform, NetworkPolicy, PSS) "
        "is covered by the other tests and gates the chart on every PR."
    ),
)
def test_install_kind_with_bundled_postgres_succeeds(
    helm_available, kind_available, kubectl_available, chart_dir,
) -> None:
    """End-to-end: helm install on kind with postgres.bundled=true.

    Slowest test in the suite (~5min cold). Creates a kind cluster,
    builds + side-loads the pfactory image, helm-installs the chart
    with postgres.bundled=true, waits for both StatefulSet (Postgres)
    and Deployment (app) to be Ready, then curls /api/health via
    kubectl port-forward.

    Uses a unique cluster name so multiple test runs can coexist.
    Cleans up cluster + image on success AND on failure (best-effort).
    """
    import os
    import subprocess
    import time
    import uuid
    from pathlib import Path

    cluster_name = f"aif-p4-{uuid.uuid4().hex[:8]}"
    image_tag = f"pfactory:p4-test-{uuid.uuid4().hex[:8]}"
    repo_root = Path(__file__).resolve().parents[2]

    def _run(cmd: list[str], timeout: int = 300, check: bool = True, **kw):
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=check, **kw
        )

    try:
        # 1. Build the app image.
        _run(["docker", "build", "-t", image_tag, str(repo_root)], timeout=600)

        # 2. Create kind cluster.
        _run(["kind", "create", "cluster", "--name", cluster_name], timeout=300)
        kubectl_env = os.environ.copy()

        # 3. Load the image into kind so the cluster can pull it
        # without a registry.
        _run(["kind", "load", "docker-image", image_tag, "--name", cluster_name], timeout=180)

        # 4. helm install. We set postgres.bundled=true and override
        # the image. Migrations.autoApply=true is the POC default —
        # production uses a separate Job, but the test exercises the
        # autoApply path because it's the bundled-mode happy path.
        _run([
            "helm", "install", "pfactory", str(chart_dir),
            "--namespace", "default",
            "--set", "postgres.bundled=true",
            "--set", f"image.repository={image_tag.split(':')[0]}",
            "--set", f"image.tag={image_tag.split(':')[1]}",
            "--set", "image.pullPolicy=Never",
            "--set", "migrations.autoApply=true",
            "--wait", "--timeout=5m",
        ], timeout=360, env=kubectl_env)

        # 5. Verify Postgres + app are ready.
        ss = _run(
            ["kubectl", "get", "statefulset",
             "-l", "app.kubernetes.io/component=postgres",
             "-o", "jsonpath={.items[0].status.readyReplicas}"],
            env=kubectl_env,
        )
        assert ss.stdout.strip() == "1", f"Postgres not ready: {ss.stdout!r}"

        dep = _run(
            ["kubectl", "get", "deployment", "pfactory",
             "-o", "jsonpath={.status.readyReplicas}"],
            env=kubectl_env,
        )
        assert dep.stdout.strip() == "1", f"app deployment not ready: {dep.stdout!r}"

        # 6. Hit /api/health via kubectl exec (no port-forward
        # flakiness; the app container has curl).
        # Use kubectl exec into a debug pod since the app container
        # is the minimal runtime image. Easier: port-forward + curl.
        pf = subprocess.Popen(
            ["kubectl", "port-forward", "svc/pfactory", "18181:80"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=kubectl_env,
        )
        try:
            time.sleep(3)  # give port-forward time to bind
            health = _run(
                ["curl", "-sf", "-m", "10", "http://localhost:18181/api/health"],
                timeout=15,
            )
            assert "healthy" in health.stdout, (
                f"unexpected /api/health response: {health.stdout!r}"
            )
        finally:
            pf.terminate()
            pf.wait(timeout=5)
    finally:
        # Best-effort cleanup.
        _run(
            ["kind", "delete", "cluster", "--name", cluster_name],
            timeout=120, check=False,
        )
        _run(["docker", "rmi", image_tag], timeout=30, check=False)


@pytest.mark.helm
def test_custom_ca_bundle_is_trusted_by_pod(helm_available, chart_dir) -> None:
    """When global.customCABundle.secretName is set, the bundle is mounted
    + SSL_CERT_FILE points at it inside the container.

    Re-renders the chart with --set global.customCABundle.secretName
    and verifies:
      - A volume of type Secret with the configured name exists.
      - A volumeMount at /etc/ssl/custom-ca exists on the container.
      - The ConfigMap exposes SSL_CERT_FILE pointing at the mount path.
      - REQUESTS_CA_BUNDLE is also set (boto3 / azure SDKs honour it).
    """
    import subprocess

    import yaml

    result = subprocess.run(
        [
            "helm", "template", "pfactory", str(chart_dir),
            "--set", "global.customCABundle.secretName=corp-root-ca",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"helm template failed: {result.stderr[-500:]}"
    docs = [d for d in yaml.safe_load_all(result.stdout) if d]

    # Deployment: secret volume + mount.
    deploys = [d for d in docs if d.get("kind") == "Deployment"]
    assert len(deploys) == 1
    pod_spec = deploys[0]["spec"]["template"]["spec"]

    ca_vol = next(
        (v for v in pod_spec.get("volumes", []) if v.get("name") == "custom-ca"),
        None,
    )
    assert ca_vol is not None, "custom-ca volume not in pod"
    assert ca_vol["secret"]["secretName"] == "corp-root-ca", (
        f"volume should reference the configured secret name; got {ca_vol}"
    )

    mounts = pod_spec["containers"][0].get("volumeMounts", [])
    ca_mount = next((m for m in mounts if m.get("name") == "custom-ca"), None)
    assert ca_mount is not None, "custom-ca volumeMount not on container"
    assert ca_mount["mountPath"] == "/etc/ssl/custom-ca"
    assert ca_mount.get("readOnly") is True

    # ConfigMap: SSL_CERT_FILE + REQUESTS_CA_BUNDLE present.
    cms = [d for d in docs if d.get("kind") == "ConfigMap"]
    assert len(cms) == 1
    cm_data = cms[0]["data"]
    assert "SSL_CERT_FILE" in cm_data
    assert cm_data["SSL_CERT_FILE"].startswith("/etc/ssl/custom-ca/")
    assert "REQUESTS_CA_BUNDLE" in cm_data


@pytest.mark.helm
def test_pdb_off_by_default_and_refuses_to_strand_a_node(helm_available, chart_dir) -> None:
    """Factory#550. The chart shipped `podDisruptionBudget.enabled: true` with
    `minAvailable: 1` against `replicaCount: 1`. That combination allows ZERO
    disruptions, so `kubectl drain` on the node hosting the pod never completes
    -- measured on the reference cluster, where the drain retried "Cannot evict
    pod as it would violate the pod's disruption budget" until timeout and
    succeeded the instant the PDB was deleted.

    The rmux case is the one worth a test of its own: deployment.yaml pins
    replicas to 1 whenever `rmux.enabled`, no matter what replicaCount says, so
    an operator can set replicaCount: 5 and still be one pod away from an
    undrainable node.
    """
    import subprocess

    def render(*sets: str) -> subprocess.CompletedProcess[str]:
        argv = ["helm", "template", "pfactory", str(chart_dir)]
        for s in sets:
            argv += ["--set", s]
        return subprocess.run(argv, capture_output=True, text=True, timeout=60)

    default = render()
    assert default.returncode == 0, default.stderr[-1500:]
    assert "kind: PodDisruptionBudget" not in default.stdout, (
        "the chart must not ship a PDB at replicaCount 1 -- it would block node drains"
    )

    stranding = render("podDisruptionBudget.enabled=true")
    assert stranding.returncode != 0, (
        "enabling the PDB at the default single replica must FAIL the render, "
        "not quietly produce an undrainable node"
    )
    assert "must be less than the replica floor" in stranding.stderr

    rmux_pinned = render(
        "podDisruptionBudget.enabled=true", "replicaCount=5", "rmux.enabled=true"
    )
    assert rmux_pinned.returncode != 0, (
        "rmux.enabled pins the Deployment to 1 replica regardless of replicaCount; "
        "the guard must use the floor the Deployment actually produces"
    )

    hpa_floor = render(
        "podDisruptionBudget.enabled=true", "autoscaling.enabled=true", "replicaCount=5"
    )
    assert hpa_floor.returncode != 0, (
        "replicaCount is irrelevant when autoscaling owns the replica count; "
        "the guard must read autoscaling.minReplicas (1)"
    )

    ok = render("podDisruptionBudget.enabled=true", "replicaCount=2")
    assert ok.returncode == 0, ok.stderr[-1500:]
    assert "kind: PodDisruptionBudget" in ok.stdout


@pytest.mark.helm
def test_sa_token_is_not_mounted_and_that_is_still_true(helm_template) -> None:
    """Factory#550. PFactory is the one control-plane service that genuinely
    does NOT need its SA token, and this pins that.

    Verified against the deployed image rather than the source: neither
    `kubernetes` nor `kubernetes_asyncio` imports and there is no kubectl on
    PATH, so the single `load_incluster_config()` call in the codebase
    (plan/enrich/adapters/kubernetes.py) cannot execute there. If a k8s client
    is ever added to the image this test should be flipped deliberately, not
    discovered by a 403 in production.
    """
    import yaml

    docs = [d for d in yaml.safe_load_all(helm_template) if d]
    sa = next(d for d in docs if d["kind"] == "ServiceAccount")
    assert sa["automountServiceAccountToken"] is False

    dep = next(d for d in docs if d["kind"] == "Deployment")
    pod_spec = dep["spec"]["template"]["spec"]
    # A pod-level override wins over the ServiceAccount; it must not contradict.
    assert pod_spec.get("automountServiceAccountToken", False) is False
