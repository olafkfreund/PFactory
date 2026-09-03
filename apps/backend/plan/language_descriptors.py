#!/usr/bin/env python3
"""language_descriptors — declarative language onboarding, shared across the fleet.

One YAML file per language turns "add a language" from a five-site, three-repo
edit into a single descriptor drop. The canonical descriptors live in the hub at
``contracts/languages/*.yaml`` (reachable from this module via the
``scripts/languages`` symlink); each consumer vendors this module byte-exact
PLUS the ``languages/`` directory beside it, exactly like ``nix_provisioner.py``
(same drift gate, same pin discipline — see
``scripts/check_verification_core_drift.py``).

The schema's load-bearing rule: **a lane that is not available MUST say why.**
``available: false`` without a non-empty ``reason`` fails the load. That makes
honesty machine-readable — the reason flows into RFC-0006 VAL-0 evidence instead
of a silent omission or, worse, a fake pass. The complementary rule holds too:
``available: true`` MUST name the ``tool`` and the ``command`` that actually
runs, because a lane in a registry that never ran is worth nothing.

Consumers:
  - ``nix_provisioner.generate_flake`` — nix attrs + shell env per language.
  - TFactory ``tools/runners/lang_registry`` — (language, lane) -> ToolSpec rows
    and the unavailable-lane reasons.
  - PFactory ``plan/detect/target_classifier`` — detection signals from aliases.
  - PFactory ``plan/emit/tfactory_block`` / ``environment_block`` — lane
    commands, proof command, and network class for the contract manifest.

Discovery is a glob over ``<this module's directory>/languages/*.yaml`` — the
plan_types pattern (PFactory ``plan/plan_types/loader.py``): a new language is a
file, not a code change. Requires PyYAML (present in every consumer backend and
in the hub test env); the import is lazy so merely importing this module stays
dependency-free.

Run directly for the self-tests: ``python3 scripts/language_descriptors.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# Lane keys a descriptor may declare. Superset of TFactory's modality spine
# (unit/browser/api/integration/mutation): ``ui`` exists so a NATIVE UI lane
# (XCUITest, Espresso) can be declared honestly unavailable with its reason,
# even though TFactory has no ui lane to run — the reason is the deliverable.
KNOWN_LANES: tuple[str, ...] = ("unit", "browser", "api", "integration", "mutation", "ui")

_DESCRIPTOR_DIR = Path(__file__).resolve().parent / "languages"


class DescriptorError(ValueError):
    """A descriptor that cannot be honoured. Always names the file and the field."""


@dataclass(frozen=True)
class LaneSupport:
    """One lane's declared support: either runnable (tool + command) or an
    explicit refusal with a reason. Never both, never neither."""

    available: bool
    tool: str = ""
    command: str = ""
    reason: str = ""
    # Constraints that do NOT gate availability but a consumer (or a human)
    # should know: e.g. Swift's unit lane runs only with an explicit
    # LinuxMain.swift because nixpkgs swiftpm test discovery is broken upstream.
    notes: str = ""


@dataclass(frozen=True)
class LanguageDescriptor:
    """A language, as declared by its ``languages/<name>.yaml`` descriptor."""

    name: str
    aliases: tuple[str, ...]
    detect_weight: int
    nix_packages: tuple[str, ...]
    # Extra devShell environment the toolchain needs, emitted verbatim into the
    # generated flake (values are Nix string expressions, so ``${pkgs...}``
    # interpolation works). Swift on Linux is the motivating case: swiftpm's
    # manifest-compile helper links libdispatch/Foundation at RUNTIME, so the
    # corelibs must be on LD_LIBRARY_PATH or `swift test` dies before reading
    # Package.swift (proven on this substrate 2026-09-03).
    nix_shell_env: dict[str, str] = field(default_factory=dict)
    lanes: dict[str, LaneSupport] = field(default_factory=dict)
    proof_command: str = ""
    # Minimum network class the toolchain needs to run its declared commands.
    # ``none`` = hermetic; ``restricted`` = the lane fetches at run time (gradle
    # pulls plugins + deps from Maven Central / the Gradle plugin portal).
    network: str = "none"

    def lane(self, key: str) -> LaneSupport | None:
        return self.lanes.get(key)

    def unavailable_reason(self, key: str) -> str | None:
        """The declared reason a lane cannot run, or None when it can (or the
        descriptor says nothing about it)."""
        spec = self.lanes.get(key)
        if spec is not None and not spec.available:
            return spec.reason
        return None


def _require(condition: bool, path: Path, message: str) -> None:
    if not condition:
        raise DescriptorError(f"{path.name}: {message}")


_ALLOWED_TOP_KEYS = frozenset(
    {"name", "aliases", "detect_weight", "nix", "lanes", "proof_command", "network"}
)
_ALLOWED_LANE_KEYS = frozenset({"available", "tool", "command", "reason", "notes"})
_ALLOWED_NETWORK = ("none", "restricted")


def _parse_lane(path: Path, lane_key: str, raw: object) -> LaneSupport:
    _require(isinstance(raw, dict), path, f"lanes.{lane_key} must be a mapping")
    assert isinstance(raw, dict)  # noqa: S101 — narrowing for the type checker
    unknown = set(raw) - _ALLOWED_LANE_KEYS
    _require(not unknown, path, f"lanes.{lane_key} has unknown keys {sorted(unknown)}")
    _require(
        isinstance(raw.get("available"), bool),
        path,
        f"lanes.{lane_key}.available must be an explicit true/false",
    )
    available = bool(raw["available"])
    tool = str(raw.get("tool") or "").strip()
    command = str(raw.get("command") or "").strip()
    reason = str(raw.get("reason") or "").strip()
    if available:
        # A runnable lane must say HOW it runs — a row with no command is the
        # pass-shaped empty measurement waiting to happen.
        _require(bool(tool), path, f"lanes.{lane_key}: available lane must name its tool")
        _require(bool(command), path, f"lanes.{lane_key}: available lane must name its command")
        _require(
            not reason,
            path,
            f"lanes.{lane_key}: a reason belongs only on an unavailable lane",
        )
    else:
        # THE rule this module exists for: unavailability carries its reason or
        # the descriptor does not load.
        _require(
            bool(reason),
            path,
            f"lanes.{lane_key}: available: false REQUIRES a non-empty reason — "
            "that reason is what feeds RFC-0006 VAL-0 instead of a silent omission",
        )
        _require(
            not (tool or command),
            path,
            f"lanes.{lane_key}: an unavailable lane must not name a tool/command",
        )
    return LaneSupport(
        available=available,
        tool=tool,
        command=command,
        reason=reason,
        notes=str(raw.get("notes") or "").strip(),
    )


def _parse_descriptor(path: Path) -> LanguageDescriptor:
    # Lazy, deliberately (see the module docstring): importing this module must
    # stay dependency-free; only actually LOADING descriptors needs PyYAML.
    import yaml  # noqa: PLC0415

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    _require(isinstance(data, dict), path, "descriptor must be a mapping")
    unknown = set(data) - _ALLOWED_TOP_KEYS
    _require(not unknown, path, f"unknown top-level keys {sorted(unknown)}")

    name = str(data.get("name") or "").strip().lower()
    _require(bool(name), path, "name is required")
    _require(
        name == path.stem,
        path,
        f"name {name!r} must match the filename stem {path.stem!r} "
        "(discovery is by glob; a mismatch makes the file unfindable by name)",
    )

    aliases = tuple(str(a).strip().lower() for a in (data.get("aliases") or []) if str(a).strip())
    _require(name in aliases, path, "aliases must include the language's own name")

    detect_weight = data.get("detect_weight", 1)
    weight_ok = (
        isinstance(detect_weight, int)
        and not isinstance(detect_weight, bool)
        and detect_weight >= 1
    )
    _require(weight_ok, path, "detect_weight must be an integer >= 1")

    nix = data.get("nix") or {}
    _require(isinstance(nix, dict), path, "nix must be a mapping")
    nix_packages = tuple(str(p).strip() for p in (nix.get("packages") or []) if str(p).strip())
    _require(bool(nix_packages), path, "nix.packages must name at least one nixpkgs attr")
    shell_env_raw = nix.get("shell_env") or {}
    _require(isinstance(shell_env_raw, dict), path, "nix.shell_env must be a mapping")
    nix_shell_env = {str(k): str(v) for k, v in shell_env_raw.items()}
    nix_unknown = set(nix) - {"packages", "shell_env"}
    _require(not nix_unknown, path, f"nix has unknown keys {sorted(nix_unknown)}")

    lanes_raw = data.get("lanes") or {}
    _require(
        isinstance(lanes_raw, dict) and bool(lanes_raw), path, "lanes must be a non-empty mapping"
    )
    unknown_lanes = set(lanes_raw) - set(KNOWN_LANES)
    _require(
        not unknown_lanes, path, f"unknown lanes {sorted(unknown_lanes)}; known: {KNOWN_LANES}"
    )
    lanes = {key: _parse_lane(path, key, raw) for key, raw in lanes_raw.items()}
    unit = lanes.get("unit")
    _require(
        unit is not None,
        path,
        "a unit lane entry is required (available or honestly unavailable)",
    )

    network = str(data.get("network") or "none").strip().lower()
    _require(network in _ALLOWED_NETWORK, path, f"network must be one of {_ALLOWED_NETWORK}")

    return LanguageDescriptor(
        name=name,
        aliases=aliases,
        detect_weight=detect_weight,
        nix_packages=nix_packages,
        nix_shell_env=nix_shell_env,
        lanes=lanes,
        proof_command=str(data.get("proof_command") or "").strip(),
        network=network,
    )


@lru_cache(maxsize=1)
def _load_default() -> dict[str, LanguageDescriptor]:
    if not _DESCRIPTOR_DIR.is_dir():
        raise DescriptorError(
            f"descriptor directory missing: {_DESCRIPTOR_DIR} — this module must be "
            "vendored together with its languages/ directory (see the drift gate)"
        )
    return _load_dir(_DESCRIPTOR_DIR)


def load_languages(directory: Path | None = None) -> dict[str, LanguageDescriptor]:
    """All language descriptors, keyed by canonical name.

    The default directory (``languages/`` beside this module) is cached; an
    explicit *directory* bypasses the cache (tests use this). A missing default
    directory raises rather than returning ``{}`` — this module is only ever
    vendored TOGETHER with its descriptors, so an empty result here means the
    vendoring is broken, and an empty registry must not read as a clean one.
    """
    if directory is None:
        return _load_default()
    return _load_dir(directory)


def _load_dir(directory: Path) -> dict[str, LanguageDescriptor]:
    out: dict[str, LanguageDescriptor] = {}
    # alias (lowercased) -> the filename that claimed it. Aliases must be
    # unique ACROSS descriptors, or resolve_language() becomes ambiguous and
    # the winner is whatever `sorted(glob(...))` yields first — stable enough
    # to look deliberate, arbitrary enough to be wrong, and silent either way.
    # (Duplicate NAMES cannot happen here: name must equal the filename stem,
    # and one directory cannot hold two files with the same stem — but the
    # claim is asserted rather than assumed, so a future loosening of the
    # stem rule cannot quietly reopen the hole.)
    claimed: dict[str, str] = {}
    for path in sorted(directory.glob("*.yaml")):
        descriptor = _parse_descriptor(path)
        if descriptor.name in out:
            raise DescriptorError(
                f"{path.name}: language {descriptor.name!r} is already declared by "
                f"{descriptor.name}.yaml — two descriptors for one language would "
                "make the registry depend on glob order"
            )
        for alias in descriptor.aliases:
            if alias in claimed:
                raise DescriptorError(
                    f"{path.name}: alias {alias!r} is already claimed by {claimed[alias]} — "
                    "aliases must be unique across descriptors or resolve_language() "
                    "is ambiguous (which language wins would depend on filename sort order)"
                )
            claimed[alias] = path.name
        out[descriptor.name] = descriptor
    if not out:
        raise DescriptorError(
            f"no *.yaml descriptors under {directory} — an empty language registry "
            "would make every consumer silently fall back to its hardcoded tables"
        )
    return out


def resolve_language(token: str) -> LanguageDescriptor | None:
    """The descriptor whose name or alias matches *token* (case-insensitive)."""
    low = token.strip().lower()
    for descriptor in load_languages().values():
        if low == descriptor.name or low in descriptor.aliases:
            return descriptor
    return None


def nix_attrs_for_language(token: str) -> tuple[str, ...] | None:
    """nixpkgs attrs for a descriptor-declared language, or None if unknown."""
    descriptor = resolve_language(token)
    return descriptor.nix_packages if descriptor else None


def nix_shell_env_for_language(token: str) -> dict[str, str]:
    """Extra devShell env for a descriptor-declared language ({} if none)."""
    descriptor = resolve_language(token)
    return dict(descriptor.nix_shell_env) if descriptor else {}


def detect_signals() -> dict[str, int]:
    """alias -> detect_weight for every descriptor, for keyword classifiers."""
    signals: dict[str, int] = {}
    for descriptor in load_languages().values():
        for alias in descriptor.aliases:
            signals[alias] = max(signals.get(alias, 0), descriptor.detect_weight)
    return signals


# --------------------------------------------------------------------------- #
_SELF_TEST_OK = """\
name: fake
aliases: [fake, faketest]
detect_weight: 1
proof_command: fake --version
network: none
nix:
  packages: [fakepkg]
  shell_env:
    FAKE_HOME: "${pkgs.fakepkg}"
lanes:
  unit:
    available: true
    tool: faketool
    command: fake test
  ui:
    available: false
    reason: needs an emulator the fleet does not have
"""


def _self_test() -> None:
    import tempfile  # noqa: PLC0415 — test-only dependency of the __main__ path

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)

        def write(name: str, text: str) -> None:
            (d / name).write_text(text, encoding="utf-8")

        def expect_error(fragment: str) -> None:
            try:
                load_languages(d)
            except DescriptorError as exc:
                assert fragment in str(exc), (fragment, exc)  # noqa: S101
            else:
                raise AssertionError(f"expected DescriptorError containing {fragment!r}")

        # 1. A well-formed descriptor loads with every field intact.
        write("fake.yaml", _SELF_TEST_OK)
        langs = load_languages(d)
        fake = langs["fake"]
        assert fake.aliases == ("fake", "faketest"), fake.aliases  # noqa: S101
        assert fake.nix_packages == ("fakepkg",), fake.nix_packages  # noqa: S101
        assert fake.nix_shell_env == {"FAKE_HOME": "${pkgs.fakepkg}"}  # noqa: S101
        lane_unit = fake.lane("unit")
        assert lane_unit is not None and lane_unit.available and lane_unit.command == "fake test"  # noqa: S101
        # 2. THE rule: an unavailable lane surfaces its reason, machine-readably.
        assert fake.unavailable_reason("ui") == "needs an emulator the fleet does not have"  # noqa: S101
        assert fake.unavailable_reason("unit") is None  # noqa: S101

        # 3. available: false with no reason MUST refuse to load.
        write(
            "bad.yaml",
            "name: bad\naliases: [bad]\nnix: {packages: [x]}\nlanes:\n  unit: {available: false}\n",
        )
        expect_error("REQUIRES a non-empty reason")
        (d / "bad.yaml").unlink()

        # 4. available: true with no command is the pass-shaped empty row — refused.
        write(
            "bad2.yaml",
            "name: bad2\naliases: [bad2]\nnix: {packages: [x]}\n"
            "lanes:\n  unit: {available: true, tool: t}\n",
        )
        expect_error("must name its command")
        (d / "bad2.yaml").unlink()

        # 5. name/filename mismatch makes a language unfindable — refused.
        write(
            "mismatch.yaml",
            "name: other\naliases: [other]\nnix: {packages: [x]}\n"
            "lanes:\n  unit: {available: true, tool: t, command: c}\n",
        )
        expect_error("must match the filename stem")
        (d / "mismatch.yaml").unlink()

        # 6. An empty directory is a broken vendoring, not a clean registry.
        (d / "fake.yaml").unlink()
        expect_error("no *.yaml descriptors")

    print("language_descriptors self-tests: passed")  # noqa: T201


if __name__ == "__main__":
    _self_test()
