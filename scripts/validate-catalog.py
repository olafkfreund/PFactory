#!/usr/bin/env python3
"""Validate the Backstage catalog + TechDocs wiring for PFactory.

Checks (no network, no Backstage install required):
  * catalog-info.yaml parses and every entity has the required envelope.
  * providesApis / consumesApis reference APIs defined in the catalog.
  * The API entity that uses `$text: ./openapi.yaml` resolves to a real file
    that itself parses as OpenAPI 3.x.
  * mkdocs.yml nav targets all exist under the techdocs/ docs dir.

Run via `catalog-validate` in the Nix devShell. Exits non-zero on any failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:
    sys.exit("PyYAML not available — run inside the Nix devShell (`nix develop`).")

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def load_catalog() -> list[dict]:
    path = ROOT / "catalog-info.yaml"
    docs = [d for d in yaml.safe_load_all(path.read_text()) if d]
    api_names = {d["metadata"]["name"] for d in docs if d.get("kind") == "API"}

    for d in docs:
        for key in ("apiVersion", "kind", "metadata", "spec"):
            if key not in d:
                err(f"catalog entity missing '{key}': {d!r:.80}")
        meta, spec = d.get("metadata", {}), d.get("spec", {})
        if "name" not in meta:
            err("catalog entity missing metadata.name")
        if "owner" not in spec:
            err(f"{meta.get('name', '?')}: spec.owner is required")

        for rel in ("providesApis", "consumesApis"):
            for ref in spec.get(rel, []):
                if ref not in api_names:
                    err(f"{meta.get('name')}: {rel} -> '{ref}' not defined in catalog")

        if d.get("kind") == "API":
            definition = spec.get("definition")
            if isinstance(definition, dict) and "$text" in definition:
                target = (ROOT / definition["$text"]).resolve()
                if not target.exists():
                    err(f"{meta['name']}: $text -> {definition['$text']} missing")
                else:
                    api_doc = yaml.safe_load(target.read_text())
                    if not str(api_doc.get("openapi", "")).startswith("3"):
                        err(f"{target.name}: not an OpenAPI 3.x document")
            elif not definition:
                err(f"{meta['name']}: API spec.definition is required")
    return docs


def check_mkdocs() -> None:
    mk = yaml.safe_load((ROOT / "mkdocs.yml").read_text())
    docs_dir = ROOT / mk.get("docs_dir", "docs")
    if not docs_dir.is_dir():
        err(f"mkdocs docs_dir '{mk.get('docs_dir')}' does not exist")
        return

    def walk(nav) -> None:
        if isinstance(nav, str):
            if nav.endswith(".md") and not (docs_dir / nav).exists():
                err(f"mkdocs nav -> {nav} missing under {docs_dir.name}/")
        elif isinstance(nav, list):
            for item in nav:
                walk(item)
        elif isinstance(nav, dict):
            for v in nav.values():
                walk(v)

    walk(mk.get("nav", []))


def main() -> int:
    docs = load_catalog()
    check_mkdocs()

    if errors:
        print("✗ catalog validation failed:")
        for e in errors:
            print(f"   - {e}")
        return 1

    kinds = ", ".join(f"{d['kind']}:{d['metadata']['name']}" for d in docs)
    print(f"✓ catalog-info.yaml: {len(docs)} entities ({kinds})")
    print("✓ openapi.yaml resolves and is OpenAPI 3.x")
    print("✓ mkdocs.yml nav targets all exist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
