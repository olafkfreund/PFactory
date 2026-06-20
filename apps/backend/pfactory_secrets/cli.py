"""
``python -m pfactory_secrets.cli`` — operator tooling for the credential broker.

Subcommands:
  - ``audit [project_dir]``  — print the honest egress manifest for a project
    (what would leave the network, secret-free). Exit 0 if egress is disabled
    or all destinations are declared; never resolves secret values.
  - ``doctor``               — report which secrets backends are available here.
  - ``resolve <ref>``        — resolve a single ref and print a REDACTED summary
    (length only; never the value). Requires egress for non-local backends.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _cmd_audit(args) -> int:
    from pfactory_secrets.egress import build_manifest, egress_enabled
    from pfactory_yml.parser import load_pfactory_yml

    project = Path(args.project_dir).resolve()
    cfg = load_pfactory_yml(project)
    creds = getattr(cfg, "credentials", None) if cfg else None
    egress_cfg = getattr(cfg, "egress", None) if cfg else None
    manifest = build_manifest(creds, egress_cfg)
    if args.json:
        print(json.dumps({"enabled": egress_enabled(project), **manifest.to_dict()}, indent=2))
    else:
        print(manifest.render_markdown())
    return 0


def _cmd_doctor(_args) -> int:
    from pfactory_secrets.factory import _BACKEND_REGISTRY, get_secrets_backend

    print("Secrets backends:")
    for name in sorted(_BACKEND_REGISTRY):
        try:
            ok = get_secrets_backend(name).available()
        except Exception as exc:  # noqa: BLE001
            ok = f"error: {exc}"
        mark = "✅" if ok is True else ("—" if ok is False else "⚠️")
        print(f"  {mark} {name:22} available={ok}")
    return 0


def _cmd_resolve(args) -> int:
    from pfactory_secrets.broker import CredentialBroker

    with CredentialBroker(egress_allowed=args.allow_egress) as broker:
        try:
            val = broker.resolve_ref(args.ref)
        except Exception as exc:  # noqa: BLE001
            print(f"resolve failed: {exc}", file=sys.stderr)
            return 1
    # Never print the value — only a redacted summary.
    print(
        f"resolved {args.ref}  →  backend={val.backend} "
        f"source={val.source} value=<{len(val.value)} chars>"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pfactory_secrets.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_audit = sub.add_parser("audit", help="print the egress manifest for a project")
    p_audit.add_argument("project_dir", nargs="?", default=".")
    p_audit.add_argument("--json", action="store_true")
    p_audit.set_defaults(func=_cmd_audit)

    sub.add_parser("doctor", help="report backend availability").set_defaults(func=_cmd_doctor)

    p_res = sub.add_parser("resolve", help="resolve a single ref (redacted output)")
    p_res.add_argument("ref")
    p_res.add_argument(
        "--allow-egress", action="store_true", help="permit non-local backends to egress"
    )
    p_res.set_defaults(func=_cmd_resolve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
