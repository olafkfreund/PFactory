"""Ingest stage — turn an uploaded plan into normalized acceptance criteria.

Text formats (markdown / Gherkin / EARS) are handled by the existing
``spec_sources`` module. This package adds binary document loaders (PDF, DOCX)
that flatten an uploaded file to text and reuse those same parsers.
"""

from plan.ingest.document_loaders import (
    DocumentLoadError,
    extract_docx_text,
    extract_pdf_text,
    extract_text,
    ingest_document,
    load_document_text,
)

__all__ = [
    "DocumentLoadError",
    "extract_docx_text",
    "extract_pdf_text",
    "extract_text",
    "ingest_document",
    "load_document_text",
]
