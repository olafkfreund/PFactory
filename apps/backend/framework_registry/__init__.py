"""Framework descriptor registry — Task 1 (#17).

Provides the public API for loading and querying per-framework descriptors.

Quick start::

    from framework_registry import load_registry, get_descriptor

    # Load all descriptors from frameworks/ at repo root
    registry = load_registry()
    print(list(registry.keys()))  # ['playwright', 'jest', 'pytest']

    # Look up a single descriptor by name
    desc = get_descriptor("playwright")
    print(desc.coverage_strategy)  # 'skip'

    # Validate a single descriptor dict (e.g. in a test)
    from framework_registry import validate_descriptor
    fd = validate_descriptor({"name": "pytest", ...})

Public names
------------
``FrameworkDescriptor``
    The frozen dataclass representing one framework's configuration.

``RuntimeSpec``
    Sub-dataclass for the Docker image + entrypoint.

``load_registry``
    Walk ``frameworks/*/descriptor.yaml`` and return a
    ``dict[str, FrameworkDescriptor]``.

``get_descriptor``
    Convenience wrapper: ``load_registry`` + ``dict.__getitem__``.

``validate_descriptor``
    Convert a raw YAML ``dict`` → ``FrameworkDescriptor``, raising
    ``FrameworkDescriptorError`` on schema violations.

``FrameworkDescriptorError``
    Raised by ``validate_descriptor`` when a field is missing or malformed.
    Carries ``.field`` and ``.reason`` attributes.

``FrameworkRegistryError``
    Raised by ``load_registry`` for duplicate descriptor names or a missing
    ``frameworks/`` directory.
"""

from .descriptor import FrameworkDescriptor, RuntimeSpec
from .loader import FrameworkRegistryError, get_descriptor, load_registry
from .validator import FrameworkDescriptorError, validate_descriptor

__all__ = [
    "FrameworkDescriptor",
    "RuntimeSpec",
    "load_registry",
    "get_descriptor",
    "validate_descriptor",
    "FrameworkDescriptorError",
    "FrameworkRegistryError",
]
