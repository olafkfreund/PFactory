"""Helm chart acceptance tests for the Alembic migration Job."""

from __future__ import annotations

import subprocess

import pytest
import yaml


def _render(chart_dir, set_values: list[str] | None = None) -> list[dict]:
    cmd = ["helm", "template", "test-release", str(chart_dir)]
    for kv in set_values or []:
        cmd.extend(["--set", kv])
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)  # noqa: S603 - fixed helm argv
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def _migration_jobs(docs: list[dict]) -> list[dict]:
    return [
        doc
        for doc in docs
        if doc.get("kind") == "Job"
        and doc.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component")
        == "migrations"
    ]


@pytest.mark.helm
@pytest.mark.usefixtures("helm_available")
class TestMigrationsJob:
    def test_default_job(self, chart_dir) -> None:
        jobs = _migration_jobs(_render(chart_dir))
        assert len(jobs) == 1

        job = jobs[0]
        assert job["metadata"]["annotations"]["helm.sh/hook"] == "pre-install,pre-upgrade"
        pod = job["spec"]["template"]["spec"]
        assert pod["automountServiceAccountToken"] is False
        container = pod["containers"][0]
        assert container["command"][-2:] == ["upgrade", "head"]
        database_url = next(env for env in container["env"] if env["name"] == "DATABASE_URL")
        assert database_url["valueFrom"]["secretKeyRef"] == {
            "name": "pfactory-db",
            "key": "database-url",
        }

    def test_auto_apply_suppresses_job(self, chart_dir) -> None:
        assert _migration_jobs(_render(chart_dir, ["migrations.autoApply=true"])) == []

    def test_job_image_override(self, chart_dir) -> None:
        jobs = _migration_jobs(_render(chart_dir, ["migrations.jobImage=x/y:z"]))
        assert len(jobs) == 1
        assert jobs[0]["spec"]["template"]["spec"]["containers"][0]["image"] == "x/y:z"
