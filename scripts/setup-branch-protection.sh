#!/usr/bin/env bash
# =============================================================================
# Apply branch protection rules to PFactory's main and dev branches.
# Idempotent — safe to re-run.
#
# Requires the GitHub CLI authenticated as a repo admin:
#   gh auth status
#
# Usage:
#   bash scripts/setup-branch-protection.sh                # uses olafkfreund/PFactory
#   REPO=other/repo bash scripts/setup-branch-protection.sh
# =============================================================================
set -euo pipefail

REPO="${REPO:-olafkfreund/PFactory}"

if ! command -v gh >/dev/null 2>&1; then
  echo "error: gh CLI is required (https://cli.github.com/)" >&2
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "error: gh CLI is not authenticated. Run 'gh auth login' first." >&2
  exit 1
fi

# Check job names from .github/workflows/ci.yml
REQUIRED_CHECKS_JSON='[
  {"context":"backend (ruff + pytest)"},
  {"context":"frontend (typecheck)"}
]'

apply_protection() {
  local branch="$1"
  local enforce_admins="$2"   # true|false
  local required_reviews="$3" # integer

  echo "==> Protecting ${REPO}@${branch} (admins=${enforce_admins}, reviews=${required_reviews})"

  # Build the JSON payload using printf for proper quoting.
  local payload
  payload=$(cat <<JSON
{
  "required_status_checks": {
    "strict": true,
    "checks": ${REQUIRED_CHECKS_JSON}
  },
  "enforce_admins": ${enforce_admins},
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "required_approving_review_count": ${required_reviews}
  },
  "required_conversation_resolution": true,
  "required_linear_history": false,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "restrictions": null
}
JSON
)

  echo "${payload}" | gh api \
    --method PUT \
    -H "Accept: application/vnd.github+json" \
    "repos/${REPO}/branches/${branch}/protection" \
    --input - >/dev/null
}

# main: 1 review required (CI checks, CODEOWNERS, conversation resolution).
# Admin enforcement is OFF so the maintainer can self-merge solo PRs — a
# solo OSS maintainer otherwise has no one to approve their own PR.
# Outside contributors still need a review because the PR-review rule itself
# stays enabled.
apply_protection "main" "false" "1"

# dev: same checks, admins can bypass for fast hotfix integration
apply_protection "dev"  "false" "1"

echo
echo "Done. Verify with:"
echo "  gh api repos/${REPO}/branches/main/protection | jq '.required_pull_request_reviews,.required_status_checks'"
echo "  gh api repos/${REPO}/branches/dev/protection  | jq '.required_pull_request_reviews,.required_status_checks'"
