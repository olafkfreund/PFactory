"""Evidence file layout helpers — Task 16 / #32 sub-task 16.1/16.2.

Pure-Python helpers for the evidence directory layout:

    <spec_dir>/findings/evidence/<test_id>/
        screenshots/   — one or more *.png files
        video.webm     — full-run video (retain-on-failure)
        trace.zip      — Playwright trace archive
        network.har    — HTTP Archive for API / Integration lanes

No I/O other than ``Path`` operations and directory walking.  The
Executor (docker_runner.py) calls ``evidence_dir_for_test`` to know
where to copy artefacts after a test run; the portal endpoint
(pfactory_tasks.py) calls ``evidence_urls_for_test`` to build the
JSON payload served to the frontend.

Usage::

    from agents.evidence.layout import evidence_dir_for_test, evidence_urls_for_test
    from pathlib import Path

    spec_dir = Path("/tmp/pfactory/specs/my-spec")
    ev_dir = evidence_dir_for_test(spec_dir, "ac1-login-flow")
    # → /tmp/pfactory/specs/my-spec/findings/evidence/ac1-login-flow

    urls = evidence_urls_for_test("my-spec", "ac1-login-flow", ev_dir)
    # → {
    #       "screenshots": [
    #           "/api/pfactory/tasks/my-spec/evidence/ac1-login-flow/screenshots/0001.png"
    #       ],
    #       "video": "/api/pfactory/tasks/my-spec/evidence/ac1-login-flow/video.webm",
    #       "trace": "/api/pfactory/tasks/my-spec/evidence/ac1-login-flow/trace.zip",
    #   }
"""

from __future__ import annotations

import json
from pathlib import Path

# ─── Directory layout ────────────────────────────────────────────────────────


def evidence_dir_for_test(spec_dir: Path, test_id: str) -> Path:
    """Return the canonical evidence directory for *test_id* under *spec_dir*.

    The directory is **not** created by this function — callers that need
    it to exist should call ``.mkdir(parents=True, exist_ok=True)`` on the
    returned path.

    Args:
        spec_dir: The PFactory workspace spec directory
            (e.g. ``~/.pfactory/workspaces/<pid>/specs/<sid>``).
        test_id: The unique test identifier, e.g. ``"ac1-login-flow"``.
            Must not contain path separators or null bytes — callers are
            responsible for validating this before storage.

    Returns:
        Absolute ``Path`` to
        ``<spec_dir>/findings/evidence/<test_id>``.
    """
    return spec_dir / "findings" / "evidence" / test_id


# ─── Extension-to-content-type map ───────────────────────────────────────────

_CONTENT_TYPE_MAP: dict[str, str] = {
    ".png": "image/png",
    ".webm": "video/webm",
    ".zip": "application/zip",
    ".har": "application/json",
    ".jsonl": "application/json",
    ".mp4": "video/mp4",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

# Artefact names that represent a *single* file (not a list)
_SINGLE_FILE_STEMS: frozenset[str] = frozenset({"video", "trace", "network"})


def evidence_urls_for_test(
    spec_id: str,
    test_id: str,
    evidence_dir: Path,
) -> dict[str, str | list[str]]:
    """Build a URL dictionary for all evidence files in *evidence_dir*.

    Walks *evidence_dir* and maps each known file/sub-directory to a
    portal URL of the form::

        /api/pfactory/tasks/<spec_id>/evidence/<test_id>/<artifact>

    Where ``<artifact>`` is the filename (e.g. ``video.webm``) or a
    sub-path under a subdirectory (e.g. ``screenshots/0001.png``).

    Directory rules:

    * ``screenshots/`` — yields a list of URLs, one per ``*.png`` /
      ``*.jpg`` / ``*.jpeg`` file inside, sorted by filename.
    * ``video.webm`` / ``trace.zip`` / ``network.har`` — each yields a
      single URL string under the key ``"video"`` / ``"trace"`` /
      ``"network"``.
    * Any other files at the top level are included with their stem as
      the key and a single URL string as the value.

    If *evidence_dir* does not exist or is empty the returned dict is
    empty — callers must not assume any key is present.

    Args:
        spec_id: The PFactory spec/task identifier.
        test_id: The unique test identifier.
        evidence_dir: ``Path`` to the evidence directory for this test
            (returned by ``evidence_dir_for_test``).

    Returns:
        Mapping from artefact key to portal URL or list of portal URLs.
    """
    _portal_base = f"/api/pfactory/tasks/{spec_id}/evidence/{test_id}"

    if not evidence_dir.exists() or not evidence_dir.is_dir():
        return {}

    urls: dict[str, str | list[str]] = {}

    # Handle screenshots/ subdirectory
    screenshots_dir = evidence_dir / "screenshots"
    if screenshots_dir.exists() and screenshots_dir.is_dir():
        shot_urls: list[str] = []
        for f in sorted(screenshots_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                shot_urls.append(f"{_portal_base}/screenshots/{f.name}")
        if shot_urls:
            urls["screenshots"] = shot_urls

    # Handle known single-file artefacts and any other top-level files
    for entry in sorted(evidence_dir.iterdir()):
        if not entry.is_file():
            continue  # skip subdirectories (screenshots/ handled above)
        suffix = entry.suffix.lower()
        if suffix not in _CONTENT_TYPE_MAP:
            continue  # skip unknown file types

        # Determine the key name from the stem
        stem = entry.stem.lower()
        if stem in _SINGLE_FILE_STEMS or entry.name.lower() in {
            "video.webm",
            "trace.zip",
            "network.har",
            "network.jsonl",
        }:
            key = stem  # "video", "trace", "network"
        else:
            key = entry.name  # use full name as key

        urls[key] = f"{_portal_base}/{entry.name}"

    return urls


# ─── Content-type lookup ─────────────────────────────────────────────────────


def render_playwright_config(
    output_dir: Path,
    base_url: str,
    *,
    screenshot_policy: str = "only-on-failure",
    video_policy: str = "retain-on-failure",
    trace_policy: str = "on-first-retry",
    requires_auth: bool = False,
    storage_state_path: str = "state.json",
) -> str:
    """Render the Playwright config template with the given substitutions.

    Reads the bundled ``playwright.config.tmpl.ts`` and replaces the
    ``@@...@@`` placeholders.

    Args:
        output_dir: Absolute path where Playwright should write evidence
            files (maps to ``@@OUTPUT_DIR@@``).
        base_url: The target base URL (maps to ``@@BASE_URL@@``).
        screenshot_policy: Playwright screenshot capture mode.
        video_policy: Playwright video capture mode.
        trace_policy: Playwright trace capture mode.
        requires_auth: When True (a ``ref``-auth target + ``requires_auth``
            subtask, #107 task 5), add a ``setup`` project that runs
            ``auth.setup.ts`` first and make the chromium project depend on it +
            reuse its ``storageState`` — so tests log in once, not per-test.
        storage_state_path: Where ``auth.setup.ts`` writes / tests read the
            saved session (maps to ``storageState``).

    Returns:
        Rendered TypeScript config file contents as a string.
    """
    tmpl_path = Path(__file__).with_name("playwright.config.tmpl.ts")
    tmpl = tmpl_path.read_text(encoding="utf-8")

    if requires_auth:
        storage_state_use = f'\n    storageState: "{storage_state_path}",'
        setup_project = '\n    { name: "setup", testMatch: /auth\\.setup\\.ts/ },'
        chromium_deps = '\n      dependencies: ["setup"],'
    else:
        # No auth → all three render empty so the config is unchanged (and the
        # no-placeholder-leakage invariant holds).
        storage_state_use = setup_project = chromium_deps = ""

    return (
        tmpl.replace("@@OUTPUT_DIR@@", str(output_dir))
        .replace("@@BASE_URL@@", base_url)
        .replace("@@SCREENSHOT_POLICY@@", screenshot_policy)
        .replace("@@VIDEO_POLICY@@", video_policy)
        .replace("@@TRACE_POLICY@@", trace_policy)
        .replace("@@STORAGE_STATE_USE@@", storage_state_use)
        .replace("@@SETUP_PROJECT@@", setup_project)
        .replace("@@CHROMIUM_DEPS@@", chromium_deps)
    )


def render_auth_setup(
    *,
    login_url: str,
    username_selector: str,
    password_selector: str,
    submit_selector: str,
    success_url_pattern: str,
    username_env: str,
    secret_env: str,
    storage_state_path: str = "state.json",
) -> str:
    """Render the ``auth.setup.ts`` form-login template (#107 task 5).

    Produces the Playwright ``setup`` script that logs in once and saves the
    authenticated session to ``storage_state_path``. Credentials are read from
    the injected env vars (``username_env`` / ``secret_env``) at run time — never
    baked into the file. The selectors / URLs come from the target's
    ``auth: { type: ref }`` block in ``.pfactory.yml``.

    Returns:
        Rendered TypeScript ``auth.setup.ts`` contents as a string.
    """
    tmpl_path = Path(__file__).with_name("auth.setup.tmpl.ts")
    tmpl = tmpl_path.read_text(encoding="utf-8")
    return (
        tmpl.replace("@@LOGIN_URL@@", login_url)
        .replace("@@USERNAME_SELECTOR@@", username_selector)
        .replace("@@PASSWORD_SELECTOR@@", password_selector)
        .replace("@@SUBMIT_SELECTOR@@", submit_selector)
        .replace("@@SUCCESS_URL_PATTERN@@", success_url_pattern)
        .replace("@@USERNAME_ENV@@", username_env)
        .replace("@@SECRET_ENV@@", secret_env)
        .replace("@@STORAGE_STATE_PATH@@", storage_state_path)
    )


# Where the saved authenticated session lands (relative to the test workspace).
_STORAGE_STATE_PATH = ".auth/state.json"


def scaffold_auth_setup(spec_dir: Path | str) -> bool:
    """Write ``auth.setup.ts`` + a ``requires_auth`` Playwright config (#107 task 5).

    When the task's snapshotted ``.pfactory.yml`` (``context/pfactory_yml.json``)
    declares a target with ``auth: { type: ref }`` and the referenced
    ``test_credentials`` entry maps to env vars, render the login-once
    ``auth.setup.ts`` (selectors from the RefAuth block, credentials read from the
    injected env vars — never baked in) into ``tests/`` and a config whose
    chromium project depends on the ``setup`` project + reuses its
    ``storageState``. Gen-Functional calls this once a browser subtask
    ``requires_auth``.

    Returns True if scaffolding was written; False when there is no ref-auth
    target / not enough info (so the default no-auth path is untouched).
    """
    spec_dir = Path(spec_dir)
    snap = spec_dir / "context" / "pfactory_yml.json"
    if not snap.is_file():
        return False
    try:
        cfg = json.loads(snap.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return False

    targets = cfg.get("targets") or []
    creds = cfg.get("test_credentials") or {}
    target = next(
        (t for t in targets if (t.get("auth") or {}).get("type") == "ref"), None
    )
    if not target:
        return False
    auth = target["auth"]
    cred = creds.get(auth.get("ref")) or {}
    username_env = cred.get("as_username")
    secret_env = cred.get("as_secret")
    base_url = target.get("base_url")
    # A form login needs a login URL + both credential env vars to be useful.
    if not (auth.get("login_url") and username_env and secret_env and base_url):
        return False

    setup_ts = render_auth_setup(
        login_url=auth["login_url"],
        username_selector=auth.get("username_selector") or "input[name='username']",
        password_selector=auth.get("password_selector") or "input[name='password']",
        submit_selector=auth.get("submit_selector") or "button[type='submit']",
        success_url_pattern=auth.get("success_url_pattern") or "",
        username_env=username_env,
        secret_env=secret_env,
        storage_state_path=_STORAGE_STATE_PATH,
    )
    config_ts = render_playwright_config(
        output_dir=spec_dir / "findings" / "evidence",
        base_url=base_url,
        requires_auth=True,
        storage_state_path=_STORAGE_STATE_PATH,
    )

    tests_dir = spec_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "auth.setup.ts").write_text(setup_ts, encoding="utf-8")
    (spec_dir / "playwright.config.ts").write_text(config_ts, encoding="utf-8")
    return True


def content_type_for_artifact(artifact_name: str) -> str:
    """Return the MIME content-type for *artifact_name*.

    Looks up by file extension.  Returns ``"application/octet-stream"``
    for unrecognised extensions.

    Args:
        artifact_name: Filename (with extension), e.g. ``"video.webm"``.

    Returns:
        MIME content-type string.
    """
    suffix = Path(artifact_name).suffix.lower()
    return _CONTENT_TYPE_MAP.get(suffix, "application/octet-stream")
