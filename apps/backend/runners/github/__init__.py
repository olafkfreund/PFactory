"""GitHub provider layer.

PFactory keeps only the multi-provider abstraction the web-server uses to
read GitHub/GitLab/Azure DevOps PRs and issues:

    from runners.github.providers.factory import get_provider

The inherited AIFactory PR-review / issue-triage / auto-fix automation that
used to live here (orchestrator, models, bot_detection, batch_*, …) was
removed in #43 — it was dead weight with no live consumer. ``providers/``
depends only on ``gh_client`` + ``rate_limiter``, which remain.

Imports are NOT eager here on purpose: importing ``runners.github`` must not
drag in heavy submodules. Consumers import what they need directly.
"""
