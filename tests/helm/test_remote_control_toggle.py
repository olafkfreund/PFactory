"""Helm chart acceptance tests for the Claude Code Remote Control toggle.

When ``remoteControl.enabled=false`` (default):
  • No ``PFACTORY_REMOTE_CONTROL_SUPPORTED`` env var
  • No ``claude-remote-credentials`` volume or volumeMount
  • Chart renders identically to the bank-pilot path

When ``remoteControl.enabled=true``:
  • Operator MUST set ``credentialsSecretName`` — the chart errors
    out with a clear message otherwise
  • ``PFACTORY_REMOTE_CONTROL_SUPPORTED=true`` env var injected
  • ``claude-remote-credentials`` Secret volume mounted at the
    configured ``mountPath`` (default /home/nonroot/.claude/.credentials.json)
  • Mount uses ``subPath: credentials.json`` so the file lands at
    the exact path, not a directory of projected files
  • ``readOnly: true`` on the mount + ``defaultMode: 0400`` on the
    volume — Claude Code refuses credentials with looser perms
"""

from __future__ import annotations

import subprocess

import pytest
import yaml


def _render(chart_dir, set_values: list[str] | None = None) -> list[dict]:
    cmd = ["helm", "template", "test-release", str(chart_dir)]
    for kv in set_values or []:
        cmd.extend(["--set", kv])
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def _render_expect_error(chart_dir, set_values: list[str] | None = None) -> str:
    cmd = ["helm", "template", "test-release", str(chart_dir)]
    for kv in set_values or []:
        cmd.extend(["--set", kv])
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert out.returncode != 0, (
        f"expected helm template to fail; got rc=0 stdout={out.stdout[:200]}"
    )
    return out.stderr


def _find_deployment(docs: list[dict]) -> dict:
    for d in docs:
        if d.get("kind") == "Deployment":
            return d
    raise AssertionError("no Deployment in rendered chart")


@pytest.mark.helm
class TestRemoteControlOff:
    """Default state — no Remote Control wiring, no operational footprint."""

    def test_no_env_var(self, helm_available, chart_dir) -> None:
        docs = _render(chart_dir, ["postgres.externalSecretName=test-pg"])
        dep = _find_deployment(docs)
        env = dep["spec"]["template"]["spec"]["containers"][0].get("env", [])
        names = [e["name"] for e in env]
        assert "PFACTORY_REMOTE_CONTROL_SUPPORTED" not in names, (
            "PFACTORY_REMOTE_CONTROL_SUPPORTED leaked when toggle is off"
        )

    def test_no_secret_volume(self, helm_available, chart_dir) -> None:
        docs = _render(chart_dir, ["postgres.externalSecretName=test-pg"])
        dep = _find_deployment(docs)
        volumes = dep["spec"]["template"]["spec"].get("volumes", [])
        names = [v["name"] for v in volumes]
        assert "claude-remote-credentials" not in names

        mounts = dep["spec"]["template"]["spec"]["containers"][0].get(
            "volumeMounts", []
        )
        mount_names = [m["name"] for m in mounts]
        assert "claude-remote-credentials" not in mount_names


@pytest.mark.helm
class TestRemoteControlOn:
    """Toggle on + secretName set — the volume + env var both render."""

    def _docs_on(self, chart_dir) -> list[dict]:
        return _render(
            chart_dir,
            [
                "postgres.externalSecretName=test-pg",
                "remoteControl.enabled=true",
                "remoteControl.credentialsSecretName=claude-remote-credentials",
            ],
        )

    def test_env_var_injected(self, helm_available, chart_dir) -> None:
        dep = _find_deployment(self._docs_on(chart_dir))
        env = dep["spec"]["template"]["spec"]["containers"][0].get("env", [])
        matching = [e for e in env if e["name"] == "PFACTORY_REMOTE_CONTROL_SUPPORTED"]
        assert len(matching) == 1
        assert matching[0]["value"] == "true"

    def test_secret_volume_present_with_default_mode_0400(
        self, helm_available, chart_dir
    ) -> None:
        dep = _find_deployment(self._docs_on(chart_dir))
        volumes = dep["spec"]["template"]["spec"]["volumes"]
        rc_vol = next(
            (v for v in volumes if v["name"] == "claude-remote-credentials"), None
        )
        assert rc_vol is not None, "claude-remote-credentials volume missing"
        secret = rc_vol["secret"]
        assert secret["secretName"] == "claude-remote-credentials"
        # 0400 in YAML serializes as integer 256
        assert secret["defaultMode"] == 256
        # items projection — only credentials.json key, lands at that path
        assert secret["items"] == [{"key": "credentials.json", "path": "credentials.json"}]

    def test_volume_mount_uses_subpath_and_is_readonly(
        self, helm_available, chart_dir
    ) -> None:
        dep = _find_deployment(self._docs_on(chart_dir))
        mounts = dep["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
        rc_mount = next(
            (m for m in mounts if m["name"] == "claude-remote-credentials"), None
        )
        assert rc_mount is not None
        # File path, not directory — subPath is mandatory
        assert rc_mount["subPath"] == "credentials.json"
        assert rc_mount["mountPath"] == "/home/nonroot/.claude/.credentials.json"
        assert rc_mount["readOnly"] is True

    def test_custom_mount_path_honoured(self, helm_available, chart_dir) -> None:
        docs = _render(
            chart_dir,
            [
                "postgres.externalSecretName=test-pg",
                "remoteControl.enabled=true",
                "remoteControl.credentialsSecretName=claude-remote-credentials",
                "remoteControl.mountPath=/custom/path/credentials.json",
            ],
        )
        dep = _find_deployment(docs)
        mounts = dep["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
        rc_mount = next(m for m in mounts if m["name"] == "claude-remote-credentials")
        assert rc_mount["mountPath"] == "/custom/path/credentials.json"


@pytest.mark.helm
class TestRemoteControlValidation:
    """Operator misconfiguration — enabled=true without a secret name —
    fails the template render with a clear error message."""

    def test_missing_secret_name_errors(self, helm_available, chart_dir) -> None:
        stderr = _render_expect_error(
            chart_dir,
            [
                "postgres.externalSecretName=test-pg",
                "remoteControl.enabled=true",
                # No credentialsSecretName!
            ],
        )
        assert "credentialsSecretName" in stderr, (
            f"error message should mention the missing field; got: {stderr[:300]}"
        )
        assert "claude auth login" in stderr or "kubectl create secret" in stderr, (
            "error message should hint at the operator setup; "
            f"got: {stderr[:300]}"
        )
