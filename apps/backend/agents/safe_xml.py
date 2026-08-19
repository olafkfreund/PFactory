"""XML parsing hardened against entity-expansion denial of service.

`xml.etree.ElementTree` is documented by CPython as vulnerable to
entity-expansion attacks ("billion laughs", quadratic blowup): a few hundred
bytes of nested entity declarations expand to gigabytes during parsing and
exhaust the process. The reports this fleet parses -- here, the Cobertura `coverage.xml` -- are
emitted by the system
under test, which is LLM-generated or user-supplied code running inside the
verification sandbox. That input is not trusted.

The remedy is to reject entity DECLARATIONS outright. Every expansion attack
needs one, and none of the report formats above legitimately declares an
entity, so rejecting them costs nothing and closes the class. This is done at
the expat layer, where the handlers actually live: `ElementTree.XMLParser` on
CPython 3.9+ is the C accelerator and exposes no way to reach them, so the
parser is driven directly and fed into a stock `ElementTree.TreeBuilder`.

Namespace handling matches `ElementTree`'s own: expat is created with `}` as
the namespace separator and `{` is prepended, yielding the familiar
`{uri}local` tags.

OWASP: A05:2021 Security Misconfiguration (XML entity expansion).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from xml.parsers import expat

__all__ = ["EntityDeclarationError", "fromstring", "parse"]


class EntityDeclarationError(ValueError):
    """Raised when a document declares an XML entity.

    Callers that already handle `ElementTree.ParseError` for malformed input
    should treat this the same way: the document is rejected, not parsed.
    """


def _new_parser(builder: ET.TreeBuilder) -> expat.XMLParserType:
    parser = expat.ParserCreate(namespace_separator="}")

    def _reject(*_args: object) -> bool:
        raise EntityDeclarationError(
            "XML entity declarations are not allowed (entity-expansion DoS)"
        )

    # Every expansion attack routes through one of these three declarations.
    parser.EntityDeclHandler = _reject
    parser.UnparsedEntityDeclHandler = _reject
    parser.ExternalEntityRefHandler = _reject

    def _start(tag: str, attrs: dict[str, str]) -> None:
        builder.start(_qualify(tag), {_qualify(k): v for k, v in attrs.items()})

    parser.StartElementHandler = _start
    parser.EndElementHandler = lambda tag: builder.end(_qualify(tag))
    parser.CharacterDataHandler = builder.data
    return parser


def _qualify(name: str) -> str:
    # expat emits "uri}local" for namespaced names; ElementTree spells the same
    # thing "{uri}local". Unqualified names contain no separator and pass through.
    return f"{{{name}" if "}" in name else name


def _parse_bytes(raw: bytes) -> ET.Element:
    builder = ET.TreeBuilder()
    parser = _new_parser(builder)
    try:
        parser.Parse(raw, True)
    except expat.ExpatError as exc:
        raise ET.ParseError(str(exc)) from exc
    return builder.close()


def fromstring(text: str | bytes) -> ET.Element:
    """Parse a document from a string, rejecting entity declarations."""
    return _parse_bytes(text.encode() if isinstance(text, str) else text)


def parse(source: str | Path) -> ET.ElementTree[ET.Element]:
    """Parse a document from a path, rejecting entity declarations.

    The return type is spelled with its element parameter so that `.getroot()`
    is an `Element` and not `Element | None`: `ElementTree.__init__` accepts
    `None`, and an unparameterised return would push that `None` onto every
    caller as a union they would each have to narrow.
    """
    return ET.ElementTree(_parse_bytes(Path(source).read_bytes()))
