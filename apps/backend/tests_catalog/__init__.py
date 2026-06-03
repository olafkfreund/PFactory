"""tests_catalog — persistent cross-run test catalog for PFactory v0.2.

The catalog (``.pfactory/tests-catalog.json`` in the AIFactory repo) tracks
every test that PFactory has ever generated.  The Triager (Task 11 / #29)
reads it to decide whether to UPDATE an existing test file in place or CREATE
a new one, and operators can set ``operator_locked=True`` to pin hand-crafted
tests against regeneration.

Public API
----------
.. code-block:: python

    from tests_catalog import (
        CatalogEntry,
        TestsCatalog,
        load_catalog,
        save_catalog,
        lookup_by_ac,
        migrate_v0_1_workspace,
        CatalogError,
    )

Modules
-------
schema
    ``CatalogEntry`` and ``TestsCatalog`` frozen dataclasses with
    ``to_dict()`` / ``from_dict()`` round-trip methods.
io
    ``load_catalog`` / ``save_catalog`` with atomic-write (tmp-then-rename).
lookup
    ``lookup_by_ac`` — 3-step exact/prefix/empty algorithm.
migration
    ``migrate_v0_1_workspace`` — walks v0.1 ``spec_dir/tests/`` to populate
    catalog entries for previously-generated pytest files.
"""

from .io import load_catalog, save_catalog
from .lookup import lookup_by_ac
from .migration import migrate_v0_1_workspace
from .schema import CatalogEntry, CatalogError, TestsCatalog

__all__ = [
    "CatalogEntry",
    "CatalogError",
    "TestsCatalog",
    "load_catalog",
    "lookup_by_ac",
    "migrate_v0_1_workspace",
    "save_catalog",
]
