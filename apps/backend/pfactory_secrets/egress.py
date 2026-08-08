"""
Honest-egress accounting for the credential broker (epic #62, issue #8).

- ``egress_enabled(project_dir)`` reads the per-project ``.pfactory.yml``
  ``egress.enabled`` gate (default OFF). The ``PFACTORY_EGRESS_ENABLED`` env
  var force-enables it for ad-hoc runs.
- ``build_manifest(...)`` produces an honest, secret-free manifest: for each
  configured credential, which backend it comes from, that backend's egress
  class + badge, and the declared destinations — so an operator can see exactly
  what would leave their network before anything runs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from byo_llm import _BADGE, EgressClass


def egress_enabled(project_dir: Path | str | None) -> bool:
    """True when egress is opted into for this project (or forced via env)."""
    if os.environ.get("PFACTORY_EGRESS_ENABLED", "").strip().lower() in ("1", "true"):
        return True
    if project_dir is None:
        return False
    try:
        from pfactory_yml.parser import load_pfactory_yml

        cfg = load_pfactory_yml(Path(project_dir))
    except Exception:  # noqa: BLE001 - missing/invalid config => egress stays off
        return False
    return bool(cfg and getattr(cfg, "egress", None) and cfg.egress.enabled)


def badge_for(egress: EgressClass) -> str:
    return _BADGE[egress]


@dataclass
class ManifestRow:
    name: str  # credential name (the key in `credentials:`)
    backend: str  # canonical backend
    egress_class: str  # local | self_hosted | managed_cloud
    badge: str
    as_var: str  # env var it is exposed as
    kind: str  # env | file
    # NOTE: never contains the secret value or the resolvable ref fragment.


@dataclass
class EgressManifest:
    enabled: bool
    rows: list[ManifestRow] = field(default_factory=list)
    destinations: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "credentials": [r.__dict__ for r in self.rows],
            "destinations": self.destinations,
        }

    def render_markdown(self) -> str:
        if not self.enabled:
            return "## Egress manifest\n\nEgress DISABLED - no credentials are resolved.\n"
        lines = [
            "## Egress manifest",
            "",
            f"Egress **ENABLED** - {len(self.rows)} credential(s).",
            "",
        ]
        if self.rows:
            lines += [
                "| Credential | Backend | Egress | Exposed as |",
                "|---|---|---|---|",
            ]
            for r in self.rows:
                lines.append(f"| {r.name} | {r.backend} | {r.badge} | `{r.as_var}` ({r.kind}) |")
            lines.append("")
        if self.destinations:
            lines += ["**Declared destinations:**", ""]
            lines += [f"- {d.get('name', '?')} → `{d.get('host', '?')}`" for d in self.destinations]
            lines.append("")
        return "\n".join(lines)


def build_manifest(credentials: dict | None, egress_cfg) -> EgressManifest:
    """Build a secret-free egress manifest from the parsed ``.pfactory.yml``
    ``credentials:`` map + ``egress:`` block."""
    from pfactory_secrets.factory import get_secrets_backend
    from pfactory_secrets.refs import infer_backend_from_ref

    enabled = bool(egress_cfg and getattr(egress_cfg, "enabled", False))
    rows: list[ManifestRow] = []
    for cred_name, entry in (credentials or {}).items():
        ref = getattr(entry, "ref", None) or (entry.get("ref") if isinstance(entry, dict) else None)
        as_var = getattr(entry, "as_", None) or (entry.get("as") if isinstance(entry, dict) else "")
        kind = getattr(entry, "kind", None) or (
            entry.get("kind", "env") if isinstance(entry, dict) else "env"
        )
        try:
            backend_name = infer_backend_from_ref(ref) if ref else "?"
            ec = (
                get_secrets_backend(backend_name).egress_class()
                if ref
                else EgressClass.MANAGED_CLOUD
            )
        except Exception:  # noqa: BLE001 - unknown ref => assume worst case for honesty
            backend_name, ec = "?", EgressClass.MANAGED_CLOUD
        rows.append(
            ManifestRow(
                name=cred_name,
                backend=backend_name,
                egress_class=ec.value,
                badge=_BADGE[ec],
                as_var=as_var or "",
                kind=kind,
            )
        )
    destinations = []
    for d in getattr(egress_cfg, "destinations", []) or []:
        destinations.append(
            {
                "name": getattr(d, "name", None) or d.get("name"),
                "host": getattr(d, "host", None) or d.get("host"),
            }
        )
    return EgressManifest(enabled=enabled, rows=rows, destinations=destinations)


__all__ = [
    "EgressManifest",
    "ManifestRow",
    "badge_for",
    "build_manifest",
    "egress_enabled",
]
