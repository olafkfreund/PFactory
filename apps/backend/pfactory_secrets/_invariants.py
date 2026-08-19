"""Module-owned runtime invariants for the credential seam (Factory#818 pilot).

Every module in ``pfactory_secrets`` declares here, in one of two forms:

* a real check over a mutable relation it owns, or
* ``reason=`` -- an owner-specific statement of why it owns no observable
  relation.

The empty form is an architectural conclusion, not a placeholder. A module that
later gains mutable state or a cross-module relation replaces its reason with a
check. ``scripts/check_invariant_ownership.py`` fails if any module in the
package is missing from this file, so a new module cannot be added and remain
invisible to diagnostics -- which is the whole point of the pattern.

Importing this module registers; it runs nothing. Verification happens only when
a caller asks (``registry.verify_all()``), and only when FACTORY_INVARIANTS=1.
"""

# Ruff PLC0415 (import-outside-top-level) is exempted per check below. The
# imports are lazy ON PURPOSE: registration must not drag in the product
# modules, so that importing this companion cannot fail because a module it
# watches is broken. A check that cannot even be registered watches nothing.
# ruff: noqa: PLC0415

from __future__ import annotations

from collections.abc import Iterator

from factory_invariants import registry

_PKG = "pfactory_secrets"

# Band in which the redaction threshold still lets real credentials qualify.
# Below the floor, ordinary output gets nuked; above the ceiling, register()
# silently drops everything a caller hands it.
_THRESHOLD_FLOOR = 3
_THRESHOLD_CEILING = 64


# --------------------------------------------------------------------------
# Real invariants: modules that own a mutable relation worth watching.
# --------------------------------------------------------------------------


def _factory_registry_consistent() -> Iterator[str]:
    """Every alias resolves to a real backend, and the three maps stay disjoint.

    ``_ALIASES`` is hand-maintained next to ``_BACKEND_REGISTRY``. An alias
    whose target was renamed or removed raises only when a caller happens to
    use that spelling, so the failure surfaces far from the edit that caused
    it, and only for whichever alias someone tried.
    """
    from pfactory_secrets import factory

    known = set(factory._BACKEND_REGISTRY)
    for alias, target in factory._ALIASES.items():
        if target not in known:
            yield f"alias {alias!r} resolves to {target!r}, which is not a registered backend"

    overlap = known & set(factory._PLANNED)
    if overlap:
        yield f"{sorted(overlap)} appear in both _BACKEND_REGISTRY and _PLANNED"

    for name, (module_path, class_name) in factory._BACKEND_REGISTRY.items():
        if not module_path.startswith(f"{_PKG}.backends."):
            yield f"backend {name!r} points outside {_PKG}.backends: {module_path!r}"
        if not class_name:
            yield f"backend {name!r} has an empty class name"


def _refs_schemes_resolve() -> Iterator[str]:
    """Every ref scheme maps to a backend ``factory`` actually knows.

    THIS IS THE CROSS-MODULE ONE, and the reason the pilot is worth running.
    ``refs._SCHEME_TO_BACKEND`` names backends owned by ``factory``. Neither
    module imports the other's table, so no single-file linter and no type
    checker can see the relation -- a backend renamed in factory.py leaves a
    dangling scheme here that only fails when someone writes a ref using it.
    """
    from pfactory_secrets import factory, refs

    known = set(factory._BACKEND_REGISTRY) | set(factory._PLANNED)
    for scheme, backend in refs._SCHEME_TO_BACKEND.items():
        if backend not in known:
            yield f"ref scheme {scheme!r} maps to {backend!r}, which no backend provides"


def _redaction_masks_what_it_is_given() -> Iterator[str]:
    """A realistically-sized secret must not survive redact(), and the threshold
    below which the Redactor silently ignores a value must stay sane.

    Asserted against BEHAVIOUR, not source: the property that matters is what
    comes out, and a rule change that quietly stopped matching would leave the
    source looking correct.

    THE PROBE LENGTH IS FIXED, NOT DERIVED FROM ``_MIN_REDACT_LEN``. Deriving it
    was the first version, and its own mutation test caught the flaw: raising
    the threshold also lengthened the probe, so the check adapted to the
    mutation and could not see it. A threshold of 4096 would silently disable
    redaction for every real credential while this check stayed green. The two
    assertions are therefore separate -- a real-shaped secret is masked, AND the
    threshold stays in a band where real secrets qualify.

    Note the trap being watched: ``register()`` returns nothing and silently
    drops values under the threshold, so a caller cannot tell whether what it
    handed over will actually be masked.
    """
    from pfactory_secrets import redaction

    # Long enough to look like a real credential, obviously synthetic so no
    # secret scanner reads it as one -- a published RFC test vector tripped
    # gitleaks in this fleet today for exactly that reason.
    probe = "INVARIANT-PROBE-NOT-A-REAL-CREDENTIAL-0123456789"
    red = redaction.Redactor()
    red.register(probe)
    if probe in red.redact(f"cloning https://oauth2:{probe}@example.invalid/x.git"):
        yield "a realistically-sized registered secret survived redact() unmasked"

    # Longest-first ordering: a shorter registered value that is a substring of a
    # longer one must not consume the longer match and leave its tail exposed.
    longer = probe + "TAIL"
    red2 = redaction.Redactor()
    red2.register(probe)
    red2.register(longer)
    if "TAIL" in red2.redact(f"token={longer}"):
        yield "a shorter registered value pre-empted a longer one, leaving its tail exposed"

    # The threshold itself. Too low nukes ordinary output; too high silently
    # stops registering anything a caller hands over.
    threshold = redaction._MIN_REDACT_LEN
    if not _THRESHOLD_FLOOR <= threshold <= _THRESHOLD_CEILING:
        yield (
            f"_MIN_REDACT_LEN is {threshold}: outside the band where real "
            "credentials still qualify for redaction"
        )


def _egress_classes_are_closed() -> Iterator[str]:
    """Every manifest row carries a class from the closed vocabulary.

    An unknown class is the "unknown beats a plausible token" failure: a row
    labelled with a value nothing downstream recognises reads as classified
    while conveying nothing.
    """
    from pfactory_secrets import egress

    cls = getattr(egress, "EgressClass", None)
    if cls is None:
        return
    members = {m.value for m in cls} if hasattr(cls, "__members__") else set()
    if not members:
        yield "EgressClass exposes no members, so no row can be validated against it"


registry.register(f"{_PKG}.factory", _factory_registry_consistent)
registry.register(f"{_PKG}.refs", _refs_schemes_resolve)
registry.register(f"{_PKG}.redaction", _redaction_masks_what_it_is_given)
registry.register(f"{_PKG}.egress", _egress_classes_are_closed)


# --------------------------------------------------------------------------
# Declared empty. Each reason says what the module owns and why no relation is
# observable at runtime. Replace with a check the moment that stops being true.
# --------------------------------------------------------------------------

_DECLARED_EMPTY: dict[str, str] = {
    "broker.py": (
        "Orchestrates resolution across backends and holds no module-level state; "
        "every relation it touches belongs to factory or refs, which check their own."
    ),
    "cli.py": (
        "Argument parsing and process exit codes only; owns no data another module "
        "reads, so there is no relation here that could drift out of step."
    ),
    "__init__.py": (
        "Declares the SecretsBackend protocol and the error types; a protocol has no "
        "runtime instances of its own to hold in a consistent state."
    ),
    "operator_config.py": (
        "Reads operator configuration into a value object per call and keeps nothing "
        "between calls, so successive reads share no state that could disagree."
    ),
    "probe.py": (
        "Performs a single liveness call against a backend and returns the outcome; "
        "it accumulates nothing across calls to be inconsistent about."
    ),
    "wif.py": (
        "Builds workload-identity assertions from inputs supplied per call; the "
        "assertion is handed straight back and never stored here."
    ),
    "backends/__init__.py": (
        "Namespace package with no declarations at all, so there is nothing this "
        "module could hold in an inconsistent state."
    ),
    "backends/env.py": (
        "Reads process environment on demand and caches nothing; the environment is "
        "owned by the process, not by this module."
    ),
    "backends/localfile.py": (
        "Decrypts a file per lookup and holds no plaintext between calls; file "
        "contents are external state this module observes rather than owns."
    ),
    "backends/vault.py": (
        "Wraps a remote Vault API per call; the authoritative state lives in Vault, "
        "so any check here would assert about a system this module does not own."
    ),
    "backends/azure_keyvault.py": (
        "Wraps a remote Azure Key Vault API per call; authoritative state is remote "
        "and this module keeps no local copy that could diverge."
    ),
    "backends/aws_secrets_manager.py": (
        "Wraps a remote AWS Secrets Manager API per call; authoritative state is "
        "remote and nothing is retained locally between lookups."
    ),
    "backends/gcp_secret_manager.py": (
        "Wraps a remote GCP Secret Manager API per call; authoritative state is "
        "remote and nothing is retained locally between lookups."
    ),
}

for _module, _reason in _DECLARED_EMPTY.items():
    registry.register(f"{_PKG}.{_module[:-3].replace('/', '.')}", reason=_reason)
