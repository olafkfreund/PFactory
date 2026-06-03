"""Framework registry loader — Task 1 (#17).

Walks ``frameworks/*/descriptor.yaml`` at the repo root, validates each
descriptor, and returns a ``dict[str, FrameworkDescriptor]``.

Usage::

    from framework_registry.loader import load_registry, get_descriptor

    registry = load_registry()
    # {'playwright': <FrameworkDescriptor>, 'jest': <...>, 'pytest': <...>}

    playwright = get_descriptor("playwright")
    # raises KeyError if the descriptor is not found

The path to the ``frameworks/`` directory defaults to
``<repo_root>/frameworks/`` where ``<repo_root>`` is computed as
``Path(__file__).resolve().parents[3]`` (i.e., four levels up from this
file: ``loader.py`` → ``framework_registry/`` → ``backend/`` →
``apps/`` → repo root).

Override via the ``frameworks_dir`` parameter::

    from pathlib import Path
    registry = load_registry(frameworks_dir=Path("/tmp/my-frameworks"))
"""

from __future__ import annotations

from pathlib import Path

import yaml  # PyYAML — already in apps/backend/requirements.txt

from .descriptor import FrameworkDescriptor
from .validator import validate_descriptor

# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------


class FrameworkRegistryError(RuntimeError):
    """Raised by :func:`load_registry` for top-level registry failures.

    Distinct from :class:`~framework_registry.validator.FrameworkDescriptorError`
    which covers per-descriptor schema violations.

    Current causes:
    * The ``frameworks/`` directory does not exist.
    * Two descriptor files declare the same ``name`` field (duplicate).
    """


# ---------------------------------------------------------------------------
# Default path resolution
# ---------------------------------------------------------------------------

# apps/backend/framework_registry/loader.py
#   → apps/backend/framework_registry/    (parent 0 — file's dir)
#   → apps/backend/                       (parent 1)
#   → apps/                               (parent 2)
#   → <repo_root>/                        (parent 3)
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_FRAMEWORKS_DIR = _REPO_ROOT / "frameworks"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_registry(
    frameworks_dir: Path | None = None,
) -> dict[str, FrameworkDescriptor]:
    """Load all framework descriptors from ``frameworks_dir``.

    Walks ``{frameworks_dir}/*/descriptor.yaml``, validates each file via
    :func:`~framework_registry.validator.validate_descriptor`, and returns a
    mapping from descriptor name to :class:`~framework_registry.descriptor.FrameworkDescriptor`.

    Args:
        frameworks_dir: Path to the directory containing per-framework
            sub-directories.  Defaults to ``<repo_root>/frameworks/``.

    Returns:
        A ``dict`` mapping ``descriptor.name`` → ``FrameworkDescriptor`` for
        every valid descriptor found.  The dict is ordered by discovery order
        (filesystem glob result).

    Raises:
        FrameworkRegistryError: If ``frameworks_dir`` does not exist, or if
            two descriptor files declare the same ``name`` value.
        FrameworkDescriptorError: If a descriptor file fails schema
            validation.  The exception's ``field`` attribute names the
            offending YAML key; its ``reason`` explains what went wrong.
        yaml.YAMLError: If a descriptor file contains malformed YAML.
    """
    base_dir = frameworks_dir if frameworks_dir is not None else _DEFAULT_FRAMEWORKS_DIR

    if not base_dir.exists():
        raise FrameworkRegistryError(f"frameworks directory does not exist: {base_dir}")
    if not base_dir.is_dir():
        raise FrameworkRegistryError(f"frameworks path is not a directory: {base_dir}")

    registry: dict[str, FrameworkDescriptor] = {}

    for yaml_path in sorted(base_dir.glob("*/descriptor.yaml")):
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        descriptor = validate_descriptor(raw)

        if descriptor.name in registry:
            raise FrameworkRegistryError(
                f"duplicate framework name {descriptor.name!r}: "
                f"found in both {registry[descriptor.name].__class__.__name__} "
                f"and {yaml_path}"
            )

        registry[descriptor.name] = descriptor

    return registry


def get_descriptor(
    name: str,
    frameworks_dir: Path | None = None,
) -> FrameworkDescriptor:
    """Load the registry and return the descriptor with the given ``name``.

    A convenience wrapper around :func:`load_registry` that raises
    ``KeyError`` (with a helpful message) when ``name`` is not found.

    Args:
        name: Framework name to look up (e.g. ``"playwright"``).
        frameworks_dir: Passed through to :func:`load_registry`.

    Returns:
        The matching :class:`~framework_registry.descriptor.FrameworkDescriptor`.

    Raises:
        KeyError: If no descriptor with ``name`` was found in the registry.
        FrameworkRegistryError: Propagated from :func:`load_registry`.
        FrameworkDescriptorError: Propagated from :func:`load_registry`.
    """
    registry = load_registry(frameworks_dir=frameworks_dir)
    if name not in registry:
        available = sorted(registry.keys())
        raise KeyError(
            f"framework {name!r} not found in registry; available: {available}"
        )
    return registry[name]
