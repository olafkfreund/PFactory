"""Mermaid diagram generation (#133/#136).

A tiny, dependency-free Mermaid ``graph`` builder plus a cloud-topology renderer
that turns a normalized discovery **inventory** into a service diagram with
findings flagged in red. Pure string generation — no network, no Mermaid CLI
(rendering to an image is the viewer's job; we emit ``.mmd`` source).

Inventory shape (duck-typed dict, all keys optional)::

    {
      "provider": "aws",
      "account": "533267307120",
      "identity": "Olaf.Freund",
      "global": {
        "s3":  {"count": 12, "ok": true, "note": "all PAB-blocked + AES256"},
        "iam": {"users": 18, "roles": 121, "policies": 113},
      },
      "regions": {
        "us-east-1": {"vpcs": 3, "instances": 3, "lambdas": 10},
        "eu-west-2": {"vpcs": 1, "instances": 11},
      },
      "findings": [
        {"severity": "high", "title": "15/18 IAM users without MFA", "scope": "global"},
        {"severity": "high", "title": "5 SGs open to 0.0.0.0/0", "scope": "us-east-1"},
      ],
    }
"""

from __future__ import annotations

import re

__all__ = ["MermaidGraph", "render_cloud_topology"]

# Severities that flag a node red. Others (low/info) render neutral.
_FLAG_SEVERITIES = frozenset({"critical", "high", "medium"})

_ID_SANITISE = re.compile(r"[^A-Za-z0-9_]")


def _node_id(raw: str) -> str:
    """A safe Mermaid node id (alnum/underscore); never empty."""
    cleaned = _ID_SANITISE.sub("_", raw).strip("_")
    return cleaned or "n"


def _esc(label: str) -> str:
    """Escape a label for a Mermaid ``["..."]`` node.

    Double quotes break the bracket syntax → HTML-entity them; newlines become
    Mermaid ``<br/>``; the chars Mermaid parses inside labels are neutralised.
    """
    out = str(label).replace('"', "&quot;").replace("\n", "<br/>")
    return out.translate(str.maketrans({"[": "(", "]": ")", "{": "(", "}": ")"}))


class MermaidGraph:
    """Minimal ``graph TD`` builder with two CSS classes: ``bad`` and ``ok``.

    Node ids are de-duplicated; adding the same id twice keeps the first label.
    """

    def __init__(self, direction: str = "TD") -> None:
        self.direction = direction
        self._nodes: dict[str, str] = {}  # id → rendered "id[\"label\"]"
        self._classes: dict[str, str] = {}  # id → "bad" | "ok"
        self._edges: list[tuple[str, str]] = []

    def node(self, node_id: str, label: str, *, cls: str | None = None) -> str:
        nid = _node_id(node_id)
        if nid not in self._nodes:
            self._nodes[nid] = f'{nid}["{_esc(label)}"]'
        if cls:
            self._classes[nid] = cls
        return nid

    def edge(self, a: str, b: str) -> None:
        self._edges.append((_node_id(a), _node_id(b)))

    def render(self) -> str:
        lines = [f"graph {self.direction}"]
        lines += [f"  {decl}" for decl in self._nodes.values()]
        lines += [f"  {a} --> {b}" for a, b in self._edges]
        lines.append("  classDef bad fill:#fde2e1,stroke:#c0392b,color:#900;")
        lines.append("  classDef ok fill:#e6f5ea,stroke:#27ae60,color:#060;")
        for cls in ("bad", "ok"):
            ids = sorted(i for i, c in self._classes.items() if c == cls)
            if ids:
                lines.append(f"  class {','.join(ids)} {cls};")
        return "\n".join(lines) + "\n"


def render_cloud_topology(inventory: dict) -> str:
    """Render a cloud discovery ``inventory`` as Mermaid topology source.

    Account → global services + per-region resources, with each finding attached
    to its scope (``global`` or a region name) and flagged red at/above medium
    severity. Missing sections are simply omitted.

    Laid out left-to-right (``LR``) so each region's findings stack vertically
    rather than fanning out into an unreadably wide row.
    """
    g = MermaidGraph("LR")
    provider = str(inventory.get("provider", "cloud")).upper()
    account = inventory.get("account", "?")
    identity = inventory.get("identity")
    acc_label = f"{provider} Account {account}"
    if identity:
        acc_label += f"<br/>{identity}"
    acc = g.node("acct", acc_label)

    # ── global services ──────────────────────────────────────────────────────
    glob = inventory.get("global") or {}
    if glob:
        gnode = g.node("global", "🌐 Global services")
        g.edge(acc, gnode)
        s3 = glob.get("s3")
        if s3:
            note = f"<br/>{s3['note']}" if s3.get("note") else ""
            g.node(
                "s3",
                f"S3 · {s3.get('count', '?')} buckets{note}",
                cls="ok" if s3.get("ok") else None,
            )
            g.edge(gnode, "s3")
        iam = glob.get("iam")
        if iam and "users" in iam:  # AWS IAM account summary
            g.node(
                "iam",
                f"IAM · {iam.get('users', '?')} users · "
                f"{iam.get('roles', '?')} roles · {iam.get('policies', '?')} policies",
            )
            g.edge(gnode, "iam")
        # Generic global services (GCP storage/iam, Azure storage/rg/compute, …).
        _rendered = {"s3"}
        if iam and "users" in iam:
            _rendered.add("iam")
        for key, val in glob.items():
            if key in _rendered or not isinstance(val, dict):
                continue
            if "count" in val:
                summary = str(val["count"])
            else:
                summary = " · ".join(f"{k} {v}" for k, v in val.items()) or "?"
            label = key.replace("_", " ").upper()
            nid = g.node(
                f"glob_{key}",
                f"{label} · {summary}",
                cls="ok" if val.get("ok") else None,
            )
            g.edge(gnode, nid)

    # ── per-region resources ─────────────────────────────────────────────────
    for region, res in (inventory.get("regions") or {}).items():
        rid = g.node(f"region_{region}", f"📍 {region}")
        g.edge(acc, rid)
        bits = []
        for key, label in (
            ("vpcs", "VPCs"),
            ("instances", "EC2"),
            ("lambdas", "Lambda"),
        ):
            if res.get(key) is not None:
                bits.append(f"{label} {res[key]}")
        if bits:
            sid = g.node(f"res_{region}", " · ".join(bits))
            g.edge(rid, sid)

    # ── findings (flagged) ───────────────────────────────────────────────────
    for i, f in enumerate(inventory.get("findings") or []):
        sev = str(f.get("severity", "")).lower()
        scope = f.get("scope", "global")
        parent = "global" if scope == "global" else f"region_{scope}"
        # Attach to scope node if it exists, else to the account.
        parent_id = _node_id(parent) if _node_id(parent) in g._nodes else "acct"
        icon = (
            "🔴" if sev in {"critical", "high"} else "🟠" if sev == "medium" else "🟡"
        )
        fid = g.node(
            f"finding_{i}",
            f"{icon} {f.get('title', 'finding')} ({sev or 'n/a'})",
            cls="bad" if sev in _FLAG_SEVERITIES else None,
        )
        g.edge(parent_id, fid)

    return g.render()
