"""PFactory planning pipeline.

The ``plan`` package is PFactory's planning-and-governance core: it ingests a
project plan, enriches it with live organizational context, decomposes it,
runs review gates, and emits governed GitHub epics + child issues for AIFactory
to execute. Stages live in sub-packages (``ingest``, ``enrich``, ``detect``,
``decompose``, ``synthesize``, ``review``, ``emit``).
"""
