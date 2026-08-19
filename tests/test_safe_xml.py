"""XML entity-expansion DoS on sandbox-produced reports (Factory#722).

``agents.coverage_delta.parse_coverage_xml`` reads a Cobertura coverage.xml
that the SYSTEM UNDER TEST wrote. (TFactory carries two more parsers of the same
shape -- Playwright junit.xml and JaCoCo jacoco.xml -- fixed in the same wave;
this repo has only the one.)

For this fleet the system under test is LLM-generated or user-supplied code
executing inside the verification sandbox, so those bytes are hostile input.
``xml.etree`` is documented by CPython as vulnerable to entity-expansion
("billion laughs", quadratic blowup): a few hundred bytes expand to gigabytes
during parsing and wedge the verifier pod.

The bomb below is SMALL on purpose. A full billion-laughs payload would hang
the test suite for minutes and exhaust the runner before failing, which is a
test that punishes you for running it. Five nested levels amplify ~600x --
enough to prove the mechanism, fast enough to run in milliseconds.

OWASP: A05:2021 Security Misconfiguration.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from agents.coverage_delta import CoverageParseError, parse_coverage_xml
from agents.safe_xml import EntityDeclarationError, fromstring, parse

# Five levels of ten-fold nesting: &lol5; expands to 100,000 "lol"s (300 KB)
# from a 500-byte document. Enough to demonstrate the amplification, small
# enough to parse instantly.
_BOMB = """<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
 <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
 <!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">
]>
{body}"""

# An external-entity declaration. Not the headline exposure -- expansion is --
# but it is declared in the same place and rejected by the same handler.
_EXTERNAL = """<?xml version="1.0"?>
<!DOCTYPE r [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>
{body}"""


# Each bomb body is a VALID report of its format with the entity reference in a
# field the parser actually reads. That matters: a bomb whose payload lands in an
# element the parser ignores is discarded either way, so a test built on one
# passes with the fix reverted and proves nothing.
_PAYLOAD = "&lol5;"
_COVERAGE_BODY = "<coverage line-rate='0.5'><payload>{p}</payload></coverage>"


def _bomb(body: str) -> str:
    return _BOMB.replace("{body}", body.replace("{p}", _PAYLOAD))


def _external(body: str) -> str:
    return _EXTERNAL.replace("{body}", body.replace("{p}", "&xxe;"))


def test_the_bomb_really_is_a_bomb() -> None:
    """The fixture expands under a stock parser -- otherwise it proves nothing.

    A test that feeds harmless XML to a hardened parser and sees no explosion is
    a test that would pass with the fix reverted. This asserts the payload has
    teeth before any of the others rely on it.
    """
    # The UNHARDENED parser is the instrument here: proving the fixture explodes
    # requires something that lets it explode, and the payload is a literal in
    # this file. Hence the suppression on the next line.
    expanded = ET.fromstring(_bomb(_COVERAGE_BODY))  # noqa: S314
    text = expanded.findtext("payload") or ""
    assert len(text) == 3 * 100_000, len(text)
    # The amplification is the attack: 494 bytes becomes 300 KB here, and the
    # same document with four more nesting levels becomes 3 GB.
    assert len(text) > len(_bomb(_COVERAGE_BODY)) * 500


# ─── the helper itself ───────────────────────────────────────────────────


def test_helper_rejects_entity_declarations() -> None:
    with pytest.raises(EntityDeclarationError):
        fromstring(_bomb(_COVERAGE_BODY))


def test_helper_rejects_external_entity_declarations() -> None:
    with pytest.raises(EntityDeclarationError):
        fromstring(_external(_COVERAGE_BODY))


def test_helper_reports_malformed_input_as_parse_error() -> None:
    """Broken XML stays a ParseError, so existing except-clauses still catch it."""
    with pytest.raises(ET.ParseError):
        fromstring("<a><unclosed>")


@pytest.mark.parametrize(
    "doc",
    [
        "<coverage line-rate='0.5'><packages/></coverage>",
        "<t:suite xmlns:t='urn:x'><t:case n='1'>text</t:case>tail</t:suite>",
        "<a><b/>between<c x='1'/></a>",
    ],
)
def test_helper_matches_elementtree_on_ordinary_documents(doc: str) -> None:
    """Hardening must not change how a well-formed report parses.

    Compared structurally against `xml.etree` itself: tags, attributes, text and
    tails, including the `{uri}local` spelling for namespaced names.
    """

    def shape(el: ET.Element, depth: int = 0) -> list[object]:
        out: list[object] = [(depth, el.tag, sorted(el.attrib.items()), el.text, el.tail)]
        for child in el:
            out += shape(child, depth + 1)
        return out

    # `xml.etree` is the reference implementation being matched against here, on
    # three constant well-formed documents declared above. Hence the suppression.
    assert shape(fromstring(doc)) == shape(ET.fromstring(doc))  # noqa: S314


def test_helper_parses_from_a_path(tmp_path: Path) -> None:
    target = tmp_path / "coverage.xml"
    target.write_text("<coverage line-rate='0.5'/>")
    assert parse(target).getroot().get("line-rate") == "0.5"


# ─── external DOCTYPEs, which the real reports actually carry ─────────────

# Real producers emit these. A JaCoCo report opens with the PUBLIC form below,
# verbatim, and coverage.py <= 6.x emitted the SYSTEM form. Both name an
# EXTERNAL DTD and neither declares an entity, so both must keep parsing: the
# hardening rejects entity DECLARATIONS, not doctypes.
#
# This is the regression this file exists to prevent as much as the bomb is.
# expat does not fetch an external subset unless asked (SetParamEntityParsing
# defaults to never), so `ExternalEntityRefHandler` never fires for these -- but
# that is a DEFAULT, and a future edit to `_new_parser` that turned parameter-
# entity parsing on would reject every JaCoCo report in the fleet. Verified
# against genuinely produced artefacts: a 479 KB coverage.py report, a
# Playwright junit.xml, and a jacoco.xml from a real Maven run.
_DOCTYPES = [
    # JaCoCo 0.8.x, byte-for-byte from a real `mvn test` report.
    ("report", '<!DOCTYPE report PUBLIC "-//JACOCO//DTD Report 1.1//EN" "report.dtd">'),
    # coverage.py <= 6.x.
    (
        "coverage",
        "<!DOCTYPE coverage SYSTEM 'http://cobertura.sourceforge.net/xml/coverage-04.dtd'>",
    ),
    # An empty internal subset: legal, and declares nothing.
    ("coverage", "<!DOCTYPE coverage []>"),
]


@pytest.mark.parametrize(("root", "doctype"), _DOCTYPES)
def test_an_external_doctype_still_parses(root: str, doctype: str) -> None:
    """A doctype that declares no entity is not an attack and must be allowed."""
    doc = (
        f"<?xml version='1.0'?>{doctype}<{root} line-rate='0.5'><child n='1'>text</child></{root}>"
    )
    parsed = fromstring(doc)
    assert parsed.tag == root
    assert parsed.get("line-rate") == "0.5"
    assert parsed.findtext("child") == "text"
    # And it agrees with the reference implementation, doctype and all.
    assert parsed.get("line-rate") == ET.fromstring(doc).get("line-rate")  # noqa: S314


# ─── the real call site ──────────────────────────────────────────────────


def test_coverage_delta_rejects_a_bomb(tmp_path: Path) -> None:
    """A hostile coverage.xml is a parse failure, not an OOM."""
    target = tmp_path / "coverage.xml"
    target.write_text(_bomb(_COVERAGE_BODY))
    with pytest.raises(CoverageParseError):
        parse_coverage_xml(target)


def test_coverage_delta_still_parses_a_real_report(tmp_path: Path) -> None:
    target = tmp_path / "coverage.xml"
    target.write_text(
        "<coverage line-rate='0.5'><packages><package name='p'><classes>"
        "<class filename='a.py'><lines>"
        "<line number='1' hits='1'/><line number='2' hits='0'/>"
        "</lines></class></classes></package></packages></coverage>"
    )
    snapshot = parse_coverage_xml(target)
    assert snapshot.line_rate == 0.5
    assert snapshot.covered_lines["a.py"] == frozenset({1})
