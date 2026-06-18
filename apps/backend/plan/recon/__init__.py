"""Repository reconnaissance (RFC-0010).

PFactory reads the target repository **statically** at planning time — a
read-only checkout plus manifest / AST / IaC parsing — so the plan it emits is
grounded in the code that actually exists, not guessed from the spec text.

The bright line (RFC-0010 §2): reconnaissance **never executes** target-repo
code (no build/install/``terraform init``, no git hooks, no submodule fetch).
The only place untrusted code runs is TFactory's hardened sandbox.

This package is introduced in phases:

* Phase 1 (this commit): the :class:`~plan.recon.models.RepoMap` data contract.
* Phase 2: ``clone`` (safe checkout) + ``reconnoiter`` (RepoMap builder) +
  ``iac_probe`` (static IaC inventory).
* Phase 3+: ``change_mode`` / ``language_reconcile`` / ``delta``.
"""

from plan.recon.models import RepoMap
from plan.recon.reconnoiter import build_repo_map, reconnoiter

__all__ = ["RepoMap", "build_repo_map", "reconnoiter"]
