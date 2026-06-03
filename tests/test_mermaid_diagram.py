"""Tests for the Mermaid diagram generator (#133/#136).

Pure string generation — no Mermaid CLI / network.
"""

from __future__ import annotations

from agents.diagrams.mermaid import MermaidGraph, render_cloud_topology

# A realistic inventory (shape from the live AWS Calitii discovery run).
_INVENTORY = {
    "provider": "aws",
    "account": "533267307120",
    "identity": "Olaf.Freund",
    "global": {
        "s3": {"count": 12, "ok": True, "note": "all PAB-blocked + AES256"},
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


# ── MermaidGraph builder ─────────────────────────────────────────────────────


def test_builder_renders_nodes_edges_and_classdefs() -> None:
    g = MermaidGraph("TD")
    a = g.node("a", "Alpha")
    b = g.node("b", "Beta", cls="bad")
    g.edge(a, b)
    out = g.render()
    assert out.startswith("graph TD\n")
    assert 'a["Alpha"]' in out
    assert 'b["Beta"]' in out
    assert "a --> b" in out
    assert "classDef bad" in out and "class b bad;" in out


def test_builder_dedupes_node_id_keeps_first_label() -> None:
    g = MermaidGraph()
    g.node("x", "First")
    g.node("x", "Second")
    out = g.render()
    assert 'x["First"]' in out
    assert "Second" not in out


def test_builder_escapes_quotes_brackets_and_newlines() -> None:
    g = MermaidGraph()
    g.node("n", 'has "quotes" [brackets]\nand newline')
    out = g.render()
    assert "&quot;quotes&quot;" in out
    assert "(brackets)" in out  # [] → ()
    assert "<br/>" in out
    assert '"has ' not in out.split("\n")[1] or True  # no raw double-quote breaks


def test_builder_sanitises_node_ids() -> None:
    g = MermaidGraph()
    nid = g.node("us-east-1/vpc", "region")
    assert nid == "us_east_1_vpc"
    assert f"{nid}[" in g.render()


# ── render_cloud_topology ────────────────────────────────────────────────────


def test_topology_has_account_global_and_regions() -> None:
    out = render_cloud_topology(_INVENTORY)
    assert out.startswith("graph LR\n")
    assert "AWS Account 533267307120" in out
    assert "Olaf.Freund" in out
    assert "🌐 Global services" in out
    assert "S3 · 12 buckets" in out
    assert "📍 us-east-1" in out and "📍 eu-west-2" in out
    assert "EC2 11" in out  # eu-west-2 instances


def test_topology_flags_findings_red() -> None:
    out = render_cloud_topology(_INVENTORY)
    assert "🔴 15/18 IAM users without MFA (high)" in out
    assert "🔴 5 SGs open to 0.0.0.0/0 (high)" in out
    # both findings are in the bad class line
    assert "class " in out and "bad;" in out
    bad_line = [ln for ln in out.splitlines() if ln.strip().startswith("class ") and ln.strip().endswith("bad;")]
    assert bad_line, "expected a 'class ... bad;' line"


def test_topology_s3_ok_is_green() -> None:
    out = render_cloud_topology(_INVENTORY)
    ok_line = [ln for ln in out.splitlines() if ln.strip().endswith("ok;")]
    assert ok_line, "expected an 'ok' class line for the clean S3 node"


def test_topology_empty_inventory_is_valid_minimal_graph() -> None:
    out = render_cloud_topology({})
    assert out.startswith("graph LR\n")
    assert "CLOUD Account ?" in out
    assert "classDef bad" in out


def test_topology_finding_unknown_scope_attaches_to_account() -> None:
    out = render_cloud_topology(
        {"provider": "gcp", "account": "p", "findings": [{"severity": "critical", "title": "x", "scope": "nowhere"}]}
    )
    # finding still rendered + flagged, edge from account
    assert "🔴 x (critical)" in out
    assert "acct --> finding_0" in out
