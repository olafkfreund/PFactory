"""WIQL injection via the tenant-supplied ADO project name (Factory#721).

The provider layer under ``runners/github/`` is vendored byte-exact from the
Factory hub canonical and policed by ``factory-github-drift.yml``. The escaping
fix and its full test live there. This test is the BEHAVIOURAL half of the
propagation check: the drift gate proves the bytes arrived, and this proves the
bytes that arrived actually do the thing -- a gate comparing a service copy to
a canonical would stay green if the canonical itself regressed.

The assertion is not "the output contains two quotes"; an off-by-one doubling
contains two quotes as well. The remainder of the WHERE clause after the project
literal must be byte-identical to the remainder produced by a benign project
name: the tenant may fill the literal and may contribute nothing to the query's
structure.

OWASP: A03:2021 Injection.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from runners.github.providers.azure_devops_provider import AzureDevOpsProvider

httpx = pytest.importorskip("httpx")

# Not a credential: an opaque literal, so assertions stay meaningful.
_FAKE_PAT = "azure-pat-placeholder"

# Benign names first -- an apostrophe is ordinary in a real project name, and an
# escaping that mangled "O'Brien Analytics" would break paying tenants -- then
# the attacks, each trying to reach query structure through the WHERE clause.
_PROJECTS = [
    "Contoso",
    "O'Brien Analytics",
    "' OR 1=1 --",
    "x' AND [System.State] = 'Closed",
    "' OR [System.AssignedTo] = @Me OR [System.TeamProject] <> '",
]

_MARKER = "[System.TeamProject] = "


def _read_wiql_literal(text: str) -> tuple[str, str]:
    """Decode a leading WIQL single-quoted literal; return (value, remainder).

    Written independently of the escaping helper so the test cannot pass by
    sharing its bug: doubled quotes decode to one, the first undoubled quote
    ends the literal.
    """
    assert text.startswith("'"), text
    out: list[str] = []
    i = 1
    while i < len(text):
        if text[i] != "'":
            out.append(text[i])
            i += 1
        elif text[i : i + 2] == "''":
            out.append("'")
            i += 2
        else:
            return "".join(out), text[i + 1 :]
    msg = f"unterminated literal: {text!r}"
    raise AssertionError(msg)


def _query_for(project: str) -> str:
    captured: list[str] = []

    def handler(request: Any) -> Any:
        if request.url.path.endswith("/_apis/wit/wiql"):
            captured.append(json.loads(request.content)["query"])
            return httpx.Response(200, json={"workItems": []})
        return httpx.Response(200, json={})

    provider = AzureDevOpsProvider(
        _repo="widgets",
        _pat=_FAKE_PAT,
        _organization="acme",
        _project=project,
    )
    transport = httpx.MockTransport(handler)
    provider._client = lambda: httpx.AsyncClient(transport=transport, timeout=30.0)

    asyncio.run(provider.fetch_issues())
    assert captured, "fetch_issues did not POST a WIQL query"
    return captured[0]


def _split_at_project(query: str) -> tuple[str, str]:
    assert _MARKER in query, query
    return _read_wiql_literal(query.split(_MARKER, 1)[1])


@pytest.mark.parametrize("project", _PROJECTS)
def test_project_name_cannot_escape_the_where_clause(project: str) -> None:
    value, remainder = _split_at_project(_query_for(project))
    _, benign_remainder = _split_at_project(_query_for("Contoso"))
    assert value == project
    assert remainder == benign_remainder
