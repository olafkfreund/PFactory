#!/usr/bin/env python3
"""RFC-0016 — shared S3-compatible artifact-store client reference library (#190).

Pure, dependency-light client the PARR services vendor to write their outputs
(build artifacts, test reports, evidence, logs) to object storage as **URIs, not
blobs** — replacing the RWO ``local-path`` PVCs that pin a service to one node.
It is the single source of truth the per-service consumers vendor (AIFactory,
TFactory, PFactory), mirroring how ``job_dispatch.py`` / ``nix_provisioner.py``
are shared.

Two layers, deliberately split so the key contract is testable without a live S3:

1. **Key layout (pure, always available).** An ``ArtifactRef`` value object and
   its ``key`` / ``uri`` build the stable, joinable key from
   apis/concurrency-conventions.md §2:
   ``<service>/<correlation_key>/<job_id>/<role>[/<path>]`` in bucket
   ``factory-artifacts``. The job-state ``artifacts[]`` (apis/job-state.schema.json)
   carries the resulting ``s3://`` URIs.

2. **Transport (boto3 if present).** ``ArtifactStore`` wraps an S3 endpoint with
   ``put_artifact`` / ``get_artifact`` / ``list_artifacts``. boto3 is imported
   lazily, so importing this module — and the key-layout layer + self-test — needs
   no third-party deps and no live S3. A consumer that actually moves bytes adds
   ``boto3`` to its own image.

Config is environment-driven (the MinIO deploy in factory-gitops sets these on the
pods, see apis/concurrency-conventions.md §2):
  - ``S3_ENDPOINT``     e.g. http://minio.factory.svc.cluster.local:9000
  - ``S3_BUCKET``       default ``factory-artifacts``
  - ``S3_ACCESS_KEY``   MinIO access key
  - ``S3_SECRET_KEY``   MinIO secret key
  - ``S3_REGION``       default ``us-east-1`` (MinIO ignores it but boto3 wants one)

Run ``python3 scripts/artifact_store.py`` for the self-test (no S3 needed).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

# apis/concurrency-conventions.md §2 — one artifacts bucket per environment.
DEFAULT_BUCKET = "factory-artifacts"
# service / role enums match job-state.schema.json (artifacts[].role).
SERVICES = ("pfactory", "aifactory", "tfactory")
ROLES = ("workspace", "build", "test-report", "evidence", "log")


def _clean_segment(value: str | int) -> str:
    """A single key segment: stringified, stripped, no surrounding slashes, non-empty."""
    seg = str(value).strip().strip("/")
    if not seg:
        raise ValueError("artifact key segment must be non-empty")
    return seg


@dataclass(frozen=True)
class ArtifactRef:
    """Addresses one artifact (or a job's prefix) in the object store.

    Bundling the coordinates in a value object keeps the store/key API at a small,
    reviewable arity and gives callers one thing to thread through job-state.
    ``correlation_key`` may be None before the upstream issue number is known; it
    is then recorded as ``_`` so the key stays well-formed and re-keyable later.
    """

    service: str  # pfactory | aifactory | tfactory
    job_id: str
    role: str  # workspace | build | test-report | evidence | log
    correlation_key: str | int | None = None
    path: str | None = None  # optional sub-path under the role prefix
    bucket: str = DEFAULT_BUCKET

    def __post_init__(self) -> None:
        if self.service not in SERVICES:
            raise ValueError(f"service must be one of {SERVICES}, got {self.service!r}")
        if self.role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}, got {self.role!r}")

    @property
    def _corr(self) -> str:
        return "_" if self.correlation_key is None else _clean_segment(self.correlation_key)

    def prefix(self) -> str:
        """The job's key prefix (no role): ``<service>/<corr>/<job_id>``."""
        return "/".join((_clean_segment(self.service), self._corr, _clean_segment(self.job_id)))

    def role_prefix(self) -> str:
        """The role prefix: ``<service>/<corr>/<job_id>/<role>``."""
        return f"{self.prefix()}/{self.role}"

    def key(self) -> str:
        """The full object key per apis/concurrency-conventions.md §2:
        ``<service>/<correlation_key>/<job_id>/<role>[/<path>]``."""
        key = self.role_prefix()
        if self.path:
            # A sub-path may contain slashes (e.g. dist/bin/app); normalise each
            # component but keep the hierarchy.
            extra = [_clean_segment(p) for p in str(self.path).split("/") if p.strip("/")]
            if extra:
                key = key + "/" + "/".join(extra)
        return key

    def uri(self) -> str:
        """``s3://<bucket>/<key>`` for the job-state ``artifacts[].uri`` field."""
        return f"s3://{self.bucket}/{self.key()}"


@dataclass(frozen=True)
class StoreConfig:
    """Env-driven S3 connection config. ``from_env`` is the normal constructor."""

    endpoint: str | None
    bucket: str
    access_key: str | None
    secret_key: str | None
    region: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> StoreConfig:
        e = os.environ if env is None else env
        return cls(
            endpoint=e.get("S3_ENDPOINT") or None,
            bucket=e.get("S3_BUCKET") or DEFAULT_BUCKET,
            access_key=e.get("S3_ACCESS_KEY") or None,
            secret_key=e.get("S3_SECRET_KEY") or None,
            region=e.get("S3_REGION") or "us-east-1",
        )


def _as_bytes(data: bytes | str | os.PathLike[str]) -> bytes:
    """Coerce upload input to bytes: raw bytes pass through; a str/PathLike naming
    an existing file is read; any other str is treated as literal text content
    (handy for small reports/logs)."""
    if isinstance(data, bytes):
        return data
    if isinstance(data, os.PathLike) or (isinstance(data, str) and Path(data).is_file()):
        return Path(data).read_bytes()
    if isinstance(data, str):
        return data.encode("utf-8")
    raise TypeError(f"unsupported artifact data type: {type(data)!r}")


class ArtifactStore:
    """Thin S3-compatible client over a configured endpoint.

    boto3 is imported lazily in ``_client`` so this module (and its key-layout
    layer + self-test) import with zero third-party deps. Consumers that move
    bytes add ``boto3`` to their image; everything else just builds keys/URIs.
    """

    def __init__(self, config: StoreConfig | None = None) -> None:
        self.config = config or StoreConfig.from_env()
        self._s3: object = None  # lazily created boto3 client

    def _client(self) -> object:
        if self._s3 is None:
            try:
                import boto3  # noqa: PLC0415 — lazy import keeps the module dep-free
            except ImportError as exc:  # pragma: no cover - only without boto3
                raise RuntimeError(
                    "ArtifactStore transport needs boto3; add it to the consumer image "
                    "(key-layout helpers work without it)"
                ) from exc
            if not self.config.endpoint:
                raise RuntimeError("S3_ENDPOINT is not set; cannot open the object store")
            self._s3 = boto3.client(
                "s3",
                endpoint_url=self.config.endpoint,
                aws_access_key_id=self.config.access_key,
                aws_secret_access_key=self.config.secret_key,
                region_name=self.config.region,
            )
        return self._s3

    def put_artifact(
        self,
        ref: ArtifactRef,
        data: bytes | str | os.PathLike[str],
        content_type: str | None = None,
    ) -> str:
        """Upload ``data`` and return its ``s3://`` URI for recording in job-state
        ``artifacts[]``. References, not blobs: only the URI ever goes back into
        Postgres / the contract."""
        key = ref.key()
        extra = {"ContentType": content_type} if content_type else {}
        self._client().put_object(  # type: ignore[attr-defined]
            Bucket=self.config.bucket, Key=key, Body=_as_bytes(data), **extra
        )
        return f"s3://{self.config.bucket}/{key}"

    def get_artifact(self, ref: ArtifactRef) -> bytes:
        """Fetch one artifact's bytes by its ref."""
        obj = self._client().get_object(  # type: ignore[attr-defined]
            Bucket=self.config.bucket, Key=ref.key()
        )
        return obj["Body"].read()

    def list_artifacts(self, ref: ArtifactRef, role_scoped: bool = False) -> list[str]:
        """List object keys under a job's prefix. By default lists the whole job;
        with ``role_scoped=True`` narrows to ``ref``'s role (e.g. to enumerate
        every evidence file a VAL claim references)."""
        prefix = ref.role_prefix() if role_scoped else ref.prefix()
        paginator = self._client().get_paginator("list_objects_v2")  # type: ignore[attr-defined]
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self.config.bucket, Prefix=f"{prefix}/"):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return keys


def _selftest() -> None:
    # Reuse the hub's tiny assert helper rather than redefining it (no copy-paste
    # of _require across the shared scripts/ — Factory#161 jscpd budget).
    from job_dispatch import _require  # noqa: PLC0415 — self-test-only import

    # Canonical key layout (apis/concurrency-conventions.md §2 worked example).
    ref = ArtifactRef("aifactory", "9d2c", "build", correlation_key=482, path="app.tar.zst")
    _require(ref.key() == "aifactory/482/9d2c/build/app.tar.zst", f"key layout: {ref.key()}")
    _require(
        ref.uri() == "s3://factory-artifacts/aifactory/482/9d2c/build/app.tar.zst",
        f"uri: {ref.uri()}",
    )

    # No path -> prefix ends at the role.
    no_path = ArtifactRef("tfactory", "j1", "evidence", correlation_key=7)
    _require(no_path.key() == "tfactory/7/j1/evidence", f"no-path: {no_path.key()}")
    _require(no_path.prefix() == "tfactory/7/j1", "prefix without role")

    # Unknown correlation_key -> placeholder, key still well-formed.
    _require(ArtifactRef("pfactory", "s1", "log").key() == "pfactory/_/s1/log", "null corr")

    # Nested sub-path preserves hierarchy, trims stray slashes.
    nested = ArtifactRef("aifactory", "j", "build", correlation_key=1, path="/dist//bin/app/")
    _require(nested.key() == "aifactory/1/j/build/dist/bin/app", f"nested: {nested.key()}")

    # Integer correlation_key normalises to its string form.
    int_uri = ArtifactRef("tfactory", "v", "test-report", 99).uri()
    _require(int_uri.endswith("/99/v/test-report"), f"int corr: {int_uri}")

    # Validation: bad service / role / empty segment are rejected.
    for bad in (
        lambda: ArtifactRef("nope", "j", "build"),
        lambda: ArtifactRef("aifactory", "j", "bogus-role"),
        lambda: ArtifactRef("aifactory", "", "build").key(),
    ):
        try:
            bad()
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for invalid input")

    # Config is env-driven; defaults hold when unset.
    cfg = StoreConfig.from_env({})
    _require(cfg.bucket == DEFAULT_BUCKET and cfg.endpoint is None, "default config")
    cfg2 = StoreConfig.from_env(
        {"S3_ENDPOINT": "http://minio:9000", "S3_BUCKET": "b", "S3_ACCESS_KEY": "a"}
    )
    _require(cfg2.endpoint == "http://minio:9000" and cfg2.bucket == "b", "env config")

    # Bytes coercion: literal text vs raw bytes (no file I/O, no boto3).
    _require(_as_bytes("hello") == b"hello", "str->bytes")
    _require(_as_bytes(b"\x00\x01") == b"\x00\x01", "bytes passthrough")

    # Transport stays lazy: constructing the store must not need boto3 or an endpoint.
    store = ArtifactStore(StoreConfig.from_env({}))
    _require(store.config.bucket == DEFAULT_BUCKET, "store builds without S3")

    sys.stdout.write(
        "artifact_store self-test: PASS — key layout, uri, null/int corr, nested path, "
        "validation, env config, bytes coercion, lazy transport\n"
    )


if __name__ == "__main__":
    _selftest()
