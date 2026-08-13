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

3. **Workspace pack/unpack (RFC-0017 §2.3, stdlib-only).** ``pack_workspace`` /
   ``unpack_workspace`` tar.gz a worktree into the job's ``workspace`` role key and
   safely restore it (path-traversal-guarded). This is the mechanism that lets a
   Phase-2 Job get its worktree from object storage and write outputs back, so
   workspaces no longer need a shared RWO PVC co-mount — the last blocker to
   multi-node scheduling. The Job reads the packed URI from its ``WORKSPACE_URI``
   env (see scripts/job_dispatch.py / apis/concurrency-conventions.md §2).

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

import gzip
import io
import os
import sys
import tarfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

# apis/concurrency-conventions.md §2 — one artifacts bucket per environment.
DEFAULT_BUCKET = "factory-artifacts"
# service / role enums match job-state.schema.json (artifacts[].role).
SERVICES = ("pfactory", "aifactory", "tfactory")
ROLES = ("workspace", "build", "test-report", "evidence", "log", "doc")

# RFC-0017 §2.3 — the packed-workspace object's file name under the workspace
# role prefix. tar.gz (stdlib only) keeps the client dependency-light; zstd would
# need a third-party wheel in every consumer image for no correctness gain here.
WORKSPACE_ARCHIVE = "workspace.tar.gz"


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
    role: str  # workspace | build | test-report | evidence | log | doc
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
        # Tag every object with its role so MinIO lifecycle rules (which filter by
        # the `role=<role>` object tag) actually match — untagged uploads leave
        # evidence retention inert (Factory#329, factory-gitops#67).
        extra["Tagging"] = f"role={ref.role}"
        self._client().put_object(  # type: ignore[attr-defined]
            Bucket=self.config.bucket, Key=key, Body=_as_bytes(data), **extra
        )
        return f"s3://{self.config.bucket}/{key}"

    def get_artifact(self, ref: ArtifactRef) -> bytes:
        """Fetch one artifact's bytes by its ref."""
        obj = self._client().get_object(  # type: ignore[attr-defined]
            Bucket=self.config.bucket, Key=ref.key()
        )
        # boto3's client is untyped, so .read() is Any; coerce to satisfy strict.
        return cast(bytes, obj["Body"].read())

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

    def put_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
        role: str | None = None,
    ) -> str:
        """Upload raw bytes to an explicit key (no ``ArtifactRef``). Used by the
        workspace packer, which already has the ref-derived key. Returns the URI.

        Pass ``role`` for objects the MinIO lifecycle rules govern so they carry
        the matching ``role=<role>`` tag; keys with no governed role (e.g. the
        graphify cache) leave it unset (Factory#329)."""
        extra = {"ContentType": content_type} if content_type else {}
        if role:
            extra["Tagging"] = f"role={role}"
        self._client().put_object(  # type: ignore[attr-defined]
            Bucket=self.config.bucket, Key=key, Body=data, **extra
        )
        return f"s3://{self.config.bucket}/{key}"

    def get_bytes(self, key: str) -> bytes:
        """Fetch one object's bytes by explicit key. The complement to
        ``put_bytes`` for the workspace unpacker (which may key off a URI env var,
        not a reconstructed ``ArtifactRef``)."""
        obj = self._client().get_object(  # type: ignore[attr-defined]
            Bucket=self.config.bucket, Key=key
        )
        # boto3's client is untyped, so .read() is Any; coerce to satisfy strict.
        return cast(bytes, obj["Body"].read())


def parse_uri(uri: str) -> tuple[str, str]:
    """Split an ``s3://<bucket>/<key>`` URI into ``(bucket, key)``.

    Lets the unpacker fetch a workspace straight from the ``WORKSPACE_URI`` env
    the dispatcher sets (apis/concurrency-conventions.md §2) without rebuilding an
    ``ArtifactRef`` from its parts."""
    if not uri.startswith("s3://"):
        raise ValueError(f"not an s3:// URI: {uri!r}")
    rest = uri[len("s3://") :]
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        raise ValueError(f"URI must be s3://<bucket>/<key>: {uri!r}")
    return bucket, key


def _workspace_key(ref: ArtifactRef) -> str:
    """The packed-workspace object key: the ``workspace`` role prefix +
    ``workspace.tar.gz``. Forces ``role='workspace'`` so callers can pass a ref
    built for any role and still land on the canonical workspace object."""
    coords = ArtifactRef(
        service=ref.service,
        job_id=ref.job_id,
        role="workspace",
        correlation_key=ref.correlation_key,
        path=WORKSPACE_ARCHIVE,
        bucket=ref.bucket,
    )
    return coords.key()


def _tar_workspace(src_dir: Path) -> bytes:
    """tar.gz a directory's contents into an in-memory archive (pure: no temp
    files). Members are stored relative to ``src_dir`` so unpack re-creates the
    tree under any ``dest_dir``.

    Byte-stable for one tree: pack the same directory twice and the two archives
    are identical. That took an explicit ``mtime=0`` on the GZIP layer
    (Factory#538). The old comment here claimed "mtime=0 + sorted walk" while
    setting no mtime anywhere — ``mode="w:gz"`` hands tarfile's gzip writer no
    timestamp, so it stamps the CURRENT TIME into the gzip header. Two calls in
    the same second matched; two calls either side of a second boundary did not,
    which is why the self-test failed roughly once per however-many runs and
    always passed on a re-run. The tar payload was byte-identical the whole time
    — measured; only the ten-byte header differed.

    What this deliberately does NOT do is normalise per-member metadata. Member
    mtimes/uids stay as they are on disk, so "deterministic" here means the same
    tree packs the same way, not that two machines with the same file contents
    agree. Zeroing member mtimes would make the stronger claim true, and would
    also land every unpacked workspace in 1970 — these archives are unpacked and
    then BUILT IN, and build tools compare mtimes. No caller needs the stronger
    property today (the object key comes from the ref, not a content hash).
    """
    if not src_dir.is_dir():
        raise NotADirectoryError(f"workspace src is not a directory: {src_dir}")
    buf = io.BytesIO()
    # mtime=0: no wall clock in the output. GzipFile takes no name from a
    # BytesIO, so the header carries no filename either.
    with (
        gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar,
    ):
        for child in sorted(src_dir.rglob("*"), key=lambda p: p.relative_to(src_dir).as_posix()):
            tar.add(child, arcname=child.relative_to(src_dir).as_posix(), recursive=False)
    return buf.getvalue()


def _is_within(base: Path, target: Path) -> bool:
    """True iff ``target`` resolves to a path inside ``base`` (path-traversal guard)."""
    try:
        target.resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return True


def _vet_member(member: tarfile.TarInfo, dest_dir: Path) -> None:
    """Reject a tar member that would escape ``dest_dir``: absolute paths, ``..``
    traversal, or a link whose target points outside the destination."""
    name = PurePosixPath(member.name)
    if name.is_absolute() or ".." in name.parts:
        raise ValueError(f"unsafe tar member (path traversal): {member.name!r}")
    if not _is_within(dest_dir, dest_dir / member.name):
        raise ValueError(f"tar member escapes destination: {member.name!r}")
    if member.islnk() or member.issym():
        link_target = (dest_dir / member.name).parent / member.linkname
        if not _is_within(dest_dir, link_target):
            raise ValueError(f"unsafe tar link target: {member.linkname!r}")


def _safe_extract(blob: bytes, dest_dir: Path) -> None:
    """Extract a tar.gz blob into ``dest_dir``, rejecting any member that would
    escape it (absolute paths, ``..`` traversal, or links pointing outside).

    Two independent guards, neither relying on the other: an explicit per-member
    containment check BEFORE anything is written, then tarfile's ``data`` filter
    (CVE-2007-4559 hardening) on the extraction itself."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            _vet_member(member, dest_dir)
    # Re-open: getmembers() above consumed the stream. `filter="data"` is not
    # conditional on the runtime: it has shipped since 3.9.17/3.10.12/3.11.4/3.12
    # and every consumer of this module is well past that. The old
    # `hasattr(tarfile, "data_filter")` fallback called extractall() with NO
    # filter, which is an unguarded extraction sink on paper (CodeQL py/tarslip,
    # Factory#1) even though _vet_member had already vetted the members.
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        tar.extractall(dest_dir, filter="data")


def pack_workspace(store: ArtifactStore, ref: ArtifactRef, src_dir: str | os.PathLike[str]) -> str:
    """RFC-0017 §2.3 — tar.gz ``src_dir`` and store it under the job's ``workspace``
    role key; return the ``s3://`` URI a Phase-2 Job records / later unpacks.

    Replaces co-mounting an RWO worktree PVC: the dispatcher packs (or the prior
    stage packs) the worktree, the Job fetches it by URI."""
    blob = _tar_workspace(Path(src_dir))
    # `workspace` is a governed role, so the archive must carry the tag too.
    # #399 added tagging to put_artifact/put_bytes but missed this caller, which
    # is the only one that reaches put_bytes from inside the library — packed
    # workspaces would have uploaded untagged and outlived their lifecycle rule.
    return store.put_bytes(
        _workspace_key(ref), blob, content_type="application/gzip", role="workspace"
    )


def unpack_workspace(
    store: ArtifactStore, uri_or_ref: str | ArtifactRef, dest_dir: str | os.PathLike[str]
) -> Path:
    """RFC-0017 §2.3 — fetch a packed workspace (by ``s3://`` URI — e.g. the Job's
    ``WORKSPACE_URI`` env — or by ``ArtifactRef``) and SAFELY extract it into
    ``dest_dir`` (e.g. ``/work``). Returns the destination path.

    Safe extraction: every member is checked for absolute paths / ``..`` traversal
    / link targets escaping ``dest_dir`` before anything is written."""
    if isinstance(uri_or_ref, ArtifactRef):
        blob = store.get_bytes(_workspace_key(uri_or_ref))
    else:
        _bucket, key = parse_uri(uri_or_ref)
        blob = store.get_bytes(key)
    dest = Path(dest_dir)
    _safe_extract(blob, dest)
    return dest


def _require(cond: bool, msg: str) -> None:
    """Assert helper for the self-test below.

    Factory#488. This used to be ``from job_dispatch import _require``, on the
    grounds that a second copy would cost jscpd clone budget (Factory#161). It
    resolved in the hub, where both files sit flat in ``scripts/``, and NOWHERE
    ELSE: the two modules are vendored to different, unrelated paths per service,
    and a service is free to vendor one and not the other. PFactory vendors this
    module and will never vendor ``job_dispatch`` — it runs a bounded pooled-worker
    model and builds no Kubernetes Job at all (RFC-0016 §5(c)) — so running
    ``python apps/backend/runners/artifact_store.py`` there raised ImportError,
    permanently. A canonical that ships a self-test its consumers cannot execute
    is the always-green gate one level up from the drift it exists to catch.

    The budget was never actually at stake: ``.jscpd.json`` sets ``minTokens: 50``
    and this is roughly fifteen. The import bought nothing and cost the self-test
    its portability.
    """
    if not cond:
        raise AssertionError(msg)


def _selftest() -> None:
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

    # Uploads carry role as an object tag so MinIO lifecycle rules match. Without
    # this the rules filter on `role=<role>` and never hit, so evidence retention
    # silently never fires (Factory#329, promoted to the canonical in #399).
    tagged = _fake_store()
    calls = cast("list[dict[str, object]]", tagged._s3.calls)  # type: ignore[attr-defined]
    tagged.put_artifact(ArtifactRef("tfactory", "j1", "evidence", 7), b"proof")
    tagged.put_artifact(ArtifactRef("tfactory", "j1", "log", 7), "line")
    _require(calls[0].get("Tagging") == "role=evidence", f"evidence tag: {calls[0]}")
    _require(calls[1].get("Tagging") == "role=log", f"log tag: {calls[1]}")

    # put_bytes tags only when a governed role is given: an ungoverned key (the
    # graphify cache) must NOT be tagged, or it matches a lifecycle rule that was
    # never meant for it.
    tagged.put_bytes("aifactory/_/j/build/pack.tgz", b"x", role="build")
    tagged.put_bytes("graphify-cache/blob", b"x")
    _require(calls[2].get("Tagging") == "role=build", f"put_bytes role: {calls[2]}")
    _require("Tagging" not in calls[3], f"ungoverned key must be untagged: {calls[3]}")

    # Transport stays lazy: constructing the store must not need boto3 or an endpoint.
    store = ArtifactStore(StoreConfig.from_env({}))
    _require(store.config.bucket == DEFAULT_BUCKET, "store builds without S3")

    _selftest_workspace()

    sys.stdout.write(
        "artifact_store self-test: PASS — key layout, uri, null/int corr, nested path, "
        "validation, env config, bytes coercion, lazy transport, workspace round-trip, "
        "path-traversal guard, role tagging\n"
    )


class _FakeS3:
    """Minimal in-memory stand-in for the boto3 S3 client: just the put/get the
    workspace pack/unpack exercise. Keeps the round-trip self-test off live S3."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        # Every put's kwargs, so the self-test can assert on the object TAGS and
        # not just the bytes. The old signature swallowed them into `**_`, which
        # is why nothing here could have caught untagged uploads (Factory#399).
        self.calls: list[dict[str, object]] = []

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **extra: object) -> None:  # noqa: N803 — boto3 kwarg names
        self.objects[(Bucket, Key)] = Body
        self.calls.append({"Bucket": Bucket, "Key": Key, **extra})

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, io.BytesIO]:  # noqa: N803 — boto3 kwarg names
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


def _fake_store() -> ArtifactStore:
    """An ArtifactStore whose transport is the in-memory _FakeS3 (no boto3, no S3)."""
    store = ArtifactStore(StoreConfig.from_env({"S3_BUCKET": DEFAULT_BUCKET}))
    store._s3 = _FakeS3()
    return store


def _evil_archive(name: str) -> bytes:
    """A one-member tar.gz whose member name is ``name`` (used to exercise the
    path-traversal guard with absolute / ``..`` members)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name=name)
        payload = b"pwned"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def _selftest_workspace() -> None:
    import tempfile  # noqa: PLC0415 — self-test-only

    # Factory#488, second site. The issue named only the import in _selftest();
    # this one is why running the module in a tree without job_dispatch.py still
    # died after that one was fixed. Found by executing the module from a
    # directory holding nothing else, which is what a consumer's tree looks like.
    req = _require

    ref = ArtifactRef("aifactory", "9d2c", "build", correlation_key=482)
    # The packed object lands under the WORKSPACE role, regardless of ref.role.
    req(
        _workspace_key(ref) == "aifactory/482/9d2c/workspace/workspace.tar.gz",
        f"workspace key: {_workspace_key(ref)}",
    )

    with tempfile.TemporaryDirectory() as tmp:
        _selftest_roundtrip(req, ref, Path(tmp))
        _selftest_safety(req, Path(tmp))


_Require = Callable[[bool, str], None]  # the _require signature above


def _selftest_roundtrip(req: _Require, ref: ArtifactRef, tmp: Path) -> None:
    src = tmp / "src"
    (src / "pkg" / "sub").mkdir(parents=True)
    (src / "top.txt").write_text("hello")
    (src / "pkg" / "mod.py").write_text("print(1)\n")
    (src / "pkg" / "sub" / "deep.bin").write_bytes(b"\x00\x01\x02")
    (src / "empty").mkdir()

    store = _fake_store()
    uri = pack_workspace(store, ref, src)
    req(uri == f"s3://{DEFAULT_BUCKET}/{_workspace_key(ref)}", f"pack uri: {uri}")

    # The packed workspace is itself a governed-role object: without the tag it
    # matches no lifecycle rule and outlives its retention window (Factory#399
    # tagged the upload primitives but missed this caller — Factory#400).
    packed = cast("list[dict[str, object]]", store._s3.calls)[-1]  # type: ignore[attr-defined]
    req(packed.get("Tagging") == "role=workspace", f"packed workspace tag: {packed}")

    # Determinism: packing the same tree twice yields byte-identical archives.
    #
    # The equality alone is a WEAK check, and was itself the Factory#538 flake:
    # the two calls run microseconds apart, so the gzip header's clock agreed
    # almost every time and disagreed only when they straddled a second
    # boundary. A defect that shows up in ~1 run in N trains people to hit
    # re-run, which is how a real determinism regression gets waved through.
    #
    # So assert the CAUSE, not just the symptom: the gzip mtime field (header
    # bytes 4:8, little-endian) must be zero. That fails 100% of the time
    # against the old code instead of occasionally, and it does not need two
    # calls to notice.
    blob_a = _tar_workspace(src)
    req(blob_a == _tar_workspace(src), "pack is deterministic")
    stamped = int.from_bytes(blob_a[4:8], "little")
    req(stamped == 0, f"gzip header carries a wall clock ({stamped}), so packing is time-dependent")

    # Round-trip by URI (the WORKSPACE_URI path) → trees match.
    dest = tmp / "dest"
    unpack_workspace(store, uri, dest)
    src_tree = {p.relative_to(src).as_posix(): p for p in src.rglob("*")}
    dst_tree = {p.relative_to(dest).as_posix(): p for p in dest.rglob("*")}
    req(set(src_tree) == set(dst_tree), f"tree mismatch: {set(src_tree) ^ set(dst_tree)}")
    for rel, sp in src_tree.items():
        if sp.is_file():
            req(sp.read_bytes() == dst_tree[rel].read_bytes(), f"content mismatch: {rel}")

    # Round-trip by ArtifactRef (no URI in hand) lands the same tree.
    dest2 = tmp / "dest2"
    unpack_workspace(store, ref, dest2)
    req((dest2 / "pkg" / "mod.py").read_text() == "print(1)\n", "ref round-trip")


def _selftest_safety(req: _Require, tmp: Path) -> None:
    import functools  # noqa: PLC0415 — self-test-only

    def rejects(fn: Callable[[], object], why: str) -> None:
        try:
            fn()
        except ValueError:
            return
        raise AssertionError(why)

    # Path-traversal + absolute-path members are rejected; nothing written outside dest.
    rejects(
        lambda: _safe_extract(_evil_archive("../escape.txt"), tmp / "victim"),
        "path-traversal member was not rejected",
    )
    req(not (tmp / "escape.txt").exists(), "traversal wrote outside dest")
    rejects(
        lambda: _safe_extract(_evil_archive("/etc/pwned"), tmp / "victim2"),
        "absolute-path member was not rejected",
    )

    # Bad URIs are rejected by the parser; a well-formed one parses cleanly.
    for bad_uri in ("http://x/y", "s3://only-bucket", "s3:///no-bucket"):
        rejects(functools.partial(parse_uri, bad_uri), f"expected ValueError for {bad_uri!r}")
    req(parse_uri("s3://b/a/c") == ("b", "a/c"), "uri parse ok")


if __name__ == "__main__":
    _selftest()
