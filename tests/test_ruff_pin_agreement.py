"""The ruff pin must be the same everywhere it is written (#417).

`cq-ratchet.yml` calls `RUFF_VERSION` "single source of truth ... MUST match",
and nothing enforced it. It drifted: pre-commit ran `v0.14.10` while CI ran
`0.15.17`. Two ruff minors apart is not the same tool -- formatting rules change
between them, so a file formatted clean locally can arrive red in CI and vice
versa, and the local hook stops being a preview of the gate it exists to
preview.

Section 4.1 of the shared standard is explicit that pre-commit is the single
local+CI entrypoint with the same config in both places. A version is part of
that config.

These read the files rather than trusting the comment, because the comment was
already there and was already wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _ci_ruff_versions() -> dict[str, str]:
    """`RUFF_VERSION` as declared by each workflow that declares one."""
    found: dict[str, str] = {}
    for wf in (_REPO / ".github" / "workflows").glob("*.yml"):
        m = re.search(
            r'^\s*RUFF_VERSION:\s*"?([0-9][^"\s]*)"?',
            wf.read_text(encoding="utf-8"),
            re.M,
        )
        if m:
            found[wf.name] = m.group(1)
    return found


def _precommit_ruff_version() -> str | None:
    """The ruff-pre-commit `rev`, normalised to bare digits for comparison."""
    text = (_REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    m = re.search(r"ruff-pre-commit\s*\n\s*rev:\s*v?([0-9][^\s]*)", text)
    return m.group(1) if m else None


def test_every_ci_workflow_declares_the_same_ruff() -> None:
    versions = _ci_ruff_versions()
    assert versions, "expected at least one workflow to declare RUFF_VERSION"
    assert len(set(versions.values())) == 1, f"workflows disagree about the ruff pin: {versions}"


def test_precommit_runs_the_same_ruff_as_ci() -> None:
    """The local hook must be a preview of the gate, not a different tool."""
    pre = _precommit_ruff_version()
    assert pre is not None, "could not find the ruff-pre-commit rev"
    ci = set(_ci_ruff_versions().values())
    assert pre in ci, (
        f"pre-commit pins ruff {pre} while CI pins {sorted(ci)}. A file formatted "
        "clean by the hook can arrive red in CI, which is the failure mode "
        "section 4.1 exists to prevent."
    )


def test_the_format_gate_is_named_after_what_it_checks() -> None:
    """A gate's name must not overstate its scope (#417).

    The job was called "ruff format --check (whole repo, blocking)" while running
    `ruff format --check apps/backend`. `tests/`, `apps/web-server/` and
    `scripts/` were never checked -- 384 files at the time of writing -- so the
    name promised a guarantee nobody had.
    """
    wf = (_REPO / ".github" / "workflows" / "cq-ratchet.yml").read_text(encoding="utf-8")
    if "whole repo" not in wf:
        return  # named honestly; nothing to reconcile
    raise AssertionError(
        "cq-ratchet.yml still claims a 'whole repo' format check. Either widen "
        "the `ruff format --check` scope to match, or keep the name honest."
    )


def _cq_ratchet() -> str:
    return (_REPO / ".github" / "workflows" / "cq-ratchet.yml").read_text(encoding="utf-8")


def _ratchet_packages() -> set[str]:
    """The trees `ratchet_lint.py` is invoked over, with `${PACKAGE_DIR}` resolved."""
    wf = _cq_ratchet()
    m = re.search(r'^\s*PACKAGE_DIR:\s*"?([^"\n]+)"?', wf, re.M)
    package_dir = m.group(1).strip() if m else ""
    cmd = re.search(r"^\s*apps/backend/\.venv/bin/python scripts/ratchet_lint\.py .*$", wf, re.M)
    assert cmd, "could not find the ratchet invocation in cq-ratchet.yml"
    found = set(re.findall(r'--package\s+"?([^"\s]+)"?', cmd.group(0)))
    return {package_dir if p == "${PACKAGE_DIR}" else p for p in found}


def _format_check_trees() -> set[str]:
    """The trees `ruff format --check` is run over."""
    m = re.search(r"^\s*run:\s*ruff format --check (.+)$", _cq_ratchet(), re.M)
    assert m, "could not find the ruff format --check step in cq-ratchet.yml"
    return set(m.group(1).split())


def test_the_format_gate_covers_every_tree_the_ratchet_covers() -> None:
    """Rule scope and formatting scope must not diverge (#479, #471).

    They diverged twice. `scripts/` was under the ratchet but under no format
    bar until #473; `apps/web-server` was under the ratchet but under no format
    bar until #479, by which point 122 of its 190 files -- 64% of the tree --
    were unformatted and the sweep had to land as its own 122-file commit.

    The failure is silent in both directions: a tree held to the ruff RULE bar
    looks gated, so nobody notices it is held to no FORMATTING bar, and the drift
    only surfaces when someone touches one file and the gate they widen buries
    their change. Asserting the containment makes the next divergence fail here
    instead of accumulating for months.
    """
    ratchet, formatted = _ratchet_packages(), _format_check_trees()
    missing = ratchet - formatted
    assert not missing, (
        f"cq-ratchet.yml runs the ratchet over {sorted(ratchet)} but "
        f"`ruff format --check` over only {sorted(formatted)}. "
        f"Ungated for formatting: {sorted(missing)}. Widen the format check "
        "(after sweeping the tree -- the gate is whole-tree and grandfathers nothing)."
    )


# ── the hub pin lives in one file, fleet-wide (#512) ────────────────────────


def test_hub_pin_lives_in_the_one_pin_file() -> None:
    """Rule 5.2: `standards/.hub-sha` is the one pin filename fleet-wide.

    The SHA used to be hardcoded in cq-ratchet.yml's env, which the standard
    rules out and which was already inconsistent with that workflow's own error
    message -- it told you to "bump standards/.hub-sha", a file that did not
    exist. Tooling reading the pin found nothing where every sibling keeps it.
    """
    pin = _REPO / "standards" / ".hub-sha"
    assert pin.is_file(), "standards/.hub-sha must exist (coding-standards.md 5.2)"
    sha = pin.read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"[0-9a-f]{40}", sha), f"not a full commit sha: {sha!r}"


# The factory-github drift gate pins a DIFFERENT vendored set (a file-granular
# mirror, not the standards/ directory) and carries its own SHA. Rule 5.2's last
# sentence generalises the one-pin-filename rule to "any other directory
# vendored from the hub", which does not cleanly cover a file-granular mirror --
# Factory#514 is open on exactly that ambiguity and names these gates as the
# exceptions. Excluding it here rather than forcing a shape the standard has not
# decided on yet; when #514 settles, delete this exemption.
# ci.yml is the same case again: it pins ONE vendored file
# (apps/backend/plan/emit/contracts/task-contract.schema.json) and says so --
# "Same convention as factory-github-drift.yml". So PFactory has TWO
# file-granular pins, not one; noted on Factory#514.
_PIN_EXEMPT = {"factory-github-drift.yml", "ci.yml"}


def test_the_standards_gate_does_not_hardcode_a_hub_sha() -> None:
    """A second copy of the pin is a second thing to forget to bump."""
    offenders = []
    for wf in (_REPO / ".github" / "workflows").glob("*.yml"):
        if wf.name in _PIN_EXEMPT:
            continue
        text = wf.read_text(encoding="utf-8")
        if re.search(r'HUB_SHA:\s*"?[0-9a-f]{40}', text):
            offenders.append(wf.name)
    assert not offenders, (
        f"these workflows hardcode a hub SHA instead of reading standards/.hub-sha: {offenders}"
    )
