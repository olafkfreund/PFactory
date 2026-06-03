#!/usr/bin/env bash
#
# seed-aifactory-workspace.sh — populate ~/.aifactory/workspaces/pfactory-demo/
# with a simulated AIFactory workspace for the PFactory v0.2.0 showcase demo.
#
# Reads the canonical content from tests/fixtures/pfactory-demo/spec-content/
# (the source of truth, version-controlled). Idempotent: safe to re-run; it
# overwrites the spec files on every invocation so the canonical fixture
# always wins.
#
# The resulting layout matches the snapshotter's contract (see
# apps/backend/workspaces/snapshotter.py — `snapshot_aifactory_spec`):
#
#   <root>/workspaces/pfactory-demo/specs/001-greeting-generator/
#       spec.md
#       implementation_plan.json
#
# Usage:
#   ./scripts/seed-aifactory-workspace.sh
#
# Env vars:
#   PFACTORY_AIFACTORY_ROOT — override workspace root (default ~/.aifactory)
#
# Exit codes:
#   0 — success (files written + verified)
#   1 — fixture missing or verification failed
#   2 — usage error
#
set -euo pipefail

# ─── Constants ─────────────────────────────────────────────────────────

readonly PROJECT_ID="pfactory-demo"
readonly SPEC_ID="001-greeting-generator"

# ─── Colour helpers ────────────────────────────────────────────────────

if [[ -n "${NO_COLOR:-}" || ! -t 1 ]]; then
    C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_BOLD=""; C_DIM=""; C_RESET=""
else
    C_GREEN=$'\033[0;32m'
    C_YELLOW=$'\033[0;33m'
    C_BLUE=$'\033[0;34m'
    C_BOLD=$'\033[1m'
    C_DIM=$'\033[2m'
    C_RESET=$'\033[0m'
fi

log_info()  { printf '%s[seed]%s %s\n' "$C_BLUE" "$C_RESET" "$*"; }
log_ok()    { printf '%s[ok]%s   %s\n' "$C_GREEN" "$C_RESET" "$*"; }
log_warn()  { printf '%s[warn]%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
log_err()   { printf '%s[err]%s  %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }

# ─── Argument handling ─────────────────────────────────────────────────

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
fi

if [[ $# -gt 0 ]]; then
    log_err "unexpected argument: $1 (use --help)"
    exit 2
fi

# ─── Locate repo root + canonical fixture ──────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly REPO_ROOT

readonly FIXTURE_DIR="${REPO_ROOT}/tests/fixtures/pfactory-demo/spec-content"
readonly FIXTURE_SPEC="${FIXTURE_DIR}/spec.md"
readonly FIXTURE_PLAN="${FIXTURE_DIR}/implementation_plan.json"

if [[ ! -f "$FIXTURE_SPEC" ]]; then
    log_err "canonical spec.md missing: $FIXTURE_SPEC"
    exit 1
fi
if [[ ! -f "$FIXTURE_PLAN" ]]; then
    log_err "canonical implementation_plan.json missing: $FIXTURE_PLAN"
    exit 1
fi

# ─── Resolve workspace root ────────────────────────────────────────────

AIFACTORY_ROOT="${PFACTORY_AIFACTORY_ROOT:-${HOME}/.aifactory}"
readonly AIFACTORY_ROOT

readonly PROJECT_DIR="${AIFACTORY_ROOT}/workspaces/${PROJECT_ID}"
readonly SPEC_DIR="${PROJECT_DIR}/specs/${SPEC_ID}"
readonly DEST_SPEC="${SPEC_DIR}/spec.md"
readonly DEST_PLAN="${SPEC_DIR}/implementation_plan.json"

log_info "Seeding simulated AIFactory workspace"
log_info "  root:     ${AIFACTORY_ROOT}"
log_info "  project:  ${PROJECT_ID}"
log_info "  spec:     ${SPEC_ID}"
log_info "  fixture:  ${FIXTURE_DIR#"$REPO_ROOT"/}"

# ─── Create directory structure ────────────────────────────────────────

mkdir -p "$SPEC_DIR"

# ─── Copy canonical fixtures (overwrite-on-rerun for idempotence) ──────

cp -f "$FIXTURE_SPEC" "$DEST_SPEC"
cp -f "$FIXTURE_PLAN" "$DEST_PLAN"

# Match the snapshotter's read-only contract loosely: workspace files
# stay writable so re-runs of this script work. The snapshotter is the
# one that pins 0o444 on copy. Leaving these at default umask perms.

# ─── Verify ────────────────────────────────────────────────────────────

if [[ ! -s "$DEST_SPEC" ]]; then
    log_err "verification failed: $DEST_SPEC is empty or missing"
    exit 1
fi
if [[ ! -s "$DEST_PLAN" ]]; then
    log_err "verification failed: $DEST_PLAN is empty or missing"
    exit 1
fi

# First-bytes match: both files start with predictable content. If a
# previous failed run left a partial file, this catches it.
spec_head="$(head -c 80 "$DEST_SPEC")"
if [[ "$spec_head" != "# Greeting Generator"* ]]; then
    log_err "verification failed: $DEST_SPEC does not start with '# Greeting Generator'"
    exit 1
fi

plan_head="$(head -c 20 "$DEST_PLAN")"
# Strip whitespace for the leading-brace check.
plan_head_trimmed="${plan_head#"${plan_head%%[![:space:]]*}"}"
if [[ "${plan_head_trimmed:0:1}" != "{" ]]; then
    log_err "verification failed: $DEST_PLAN does not start with '{'"
    exit 1
fi

# JSON validity check (best-effort; skip silently if python3 unavailable).
if command -v python3 >/dev/null 2>&1; then
    if ! python3 -c "import json,sys; json.load(open('$DEST_PLAN'))" 2>/dev/null; then
        log_err "verification failed: $DEST_PLAN is not valid JSON"
        exit 1
    fi
fi

# ─── Summary ───────────────────────────────────────────────────────────

log_ok "wrote ${DEST_SPEC}"
log_ok "wrote ${DEST_PLAN}"

printf '\n%sSeeded layout:%s\n' "$C_BOLD" "$C_RESET"
if command -v tree >/dev/null 2>&1; then
    tree -a "$AIFACTORY_ROOT" 2>/dev/null || true
else
    # Portable fallback: find-based tree view (depth-limited).
    printf '%s%s%s\n' "$C_DIM" "$AIFACTORY_ROOT" "$C_RESET"
    find "$AIFACTORY_ROOT" -mindepth 1 -maxdepth 5 -print 2>/dev/null \
        | sed -e "s|^${AIFACTORY_ROOT}/||" -e 's|[^/]*/|  |g' -e 's|^|  |'
fi

printf '\n%sDone.%s Snapshotter should now find the spec at:\n' \
    "$C_GREEN" "$C_RESET"
printf '  %s\n' "$SPEC_DIR"
