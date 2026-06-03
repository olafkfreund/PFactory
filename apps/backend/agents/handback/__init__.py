"""PFactory → AIFactory correction hand-back (epic #182).

When PFactory's pipeline finds problems in a feature it tested, this package
packages a correction request and (in later phases) hands it back to AIFactory's
QA Fixer. Built in phases:

  - P2 (#184): ``request`` + ``render`` — pure-compute builder/renderer.
  - P4 (#185): ``send`` — dry-run-first, opt-in sender.
  - P6 (#187): ``loop`` — bounded poll → re-test closed loop.
"""

from __future__ import annotations

from .render import render_fix_request_md
from .request import CorrectionRequest, Failure, build_correction_request

__all__ = [
    "Failure",
    "CorrectionRequest",
    "build_correction_request",
    "render_fix_request_md",
]
