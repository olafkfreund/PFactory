#!/usr/bin/env bash
#
# seed-scenario.sh — make any /demo scenario runnable.
#
# For the named scenario it:
#   1. seeds the simulated AIFactory spec workspace
#      (~/.aifactory/workspaces/<project>/specs/<spec>/{spec.md,
#       implementation_plan.json})
#   2. materialises the SUT into a real local git repo with a base→branch diff
#      so the handover has a finished feature to test, plus .pfactory.yml and an
#      empty .pfactory/tests-catalog.json
#   3. prints the handover parameters (project_id, spec_id, repo_path, branch,
#      base_ref, expect_failure) as KEY=value lines the orchestrator can read
#
# Scenarios live under tests/fixtures/demo-scenarios/<id>/ with a meta.env, a
# spec-content/ (spec.md + implementation_plan.json) and, for local SUTs, a
# sut/ tree. greeting-generator delegates to seed-aifactory-workspace.sh (its
# SUT is the external olafkfreund/pfactory-demo repo); failure-flow is a lens
# over a base scenario.
#
# Usage:
#   scripts/demo/seed-scenario.sh <scenario> [--base <scenario>]
#   scripts/demo/seed-scenario.sh --list
#
# Env vars:
#   PFACTORY_AIFACTORY_ROOT   AIFactory workspace root (default ~/.aifactory)
#   PFACTORY_DEMO_SUT_ROOT    where local SUTs are materialised
#                             (default ~/.pfactory/demo-suts)
#
# Exit codes:  0 ok · 1 seeding/verification failed · 2 usage error
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCEN_ROOT="${REPO_ROOT}/tests/fixtures/demo-scenarios"
AIFACTORY_ROOT="${PFACTORY_AIFACTORY_ROOT:-${HOME}/.aifactory}"
SUT_ROOT="${PFACTORY_DEMO_SUT_ROOT:-${HOME}/.pfactory/demo-suts}"

if [[ -n "${NO_COLOR:-}" || ! -t 1 ]]; then
  C_G=""; C_Y=""; C_B=""; C_BOLD=""; C_R=""
else
  C_G=$'\033[0;32m'; C_Y=$'\033[0;33m'; C_B=$'\033[0;34m'; C_BOLD=$'\033[1m'; C_R=$'\033[0m'
fi
info() { printf '%s[seed]%s %s\n' "$C_B" "$C_R" "$*" >&2; }
ok()   { printf '%s[ok]%s   %s\n' "$C_G" "$C_R" "$*" >&2; }
die()  { printf '%s[err]%s  %s\n' "$C_Y" "$C_R" "$*" >&2; exit 1; }

# ─── args ──────────────────────────────────────────────────────────────
SCENARIO=""; BASE_OVERRIDE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --list)
      for d in "$SCEN_ROOT"/*/; do printf '  %s\n' "$(basename "$d")"; done; exit 0 ;;
    --base) BASE_OVERRIDE="$2"; shift 2 ;;
    -h|--help) sed -n '2,33p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "seed-scenario.sh: unknown flag '$1'" >&2; exit 2 ;;
    *) SCENARIO="$1"; shift ;;
  esac
done
[[ -n "$SCENARIO" ]] || { echo "usage: seed-scenario.sh <scenario> [--base <scenario>]" >&2; exit 2; }

SCEN_DIR="${SCEN_ROOT}/${SCENARIO}"
[[ -d "$SCEN_DIR" ]] || die "unknown scenario '$SCENARIO' (try --list)"
[[ -f "$SCEN_DIR/meta.env" ]] || die "scenario '$SCENARIO' has no meta.env"

# ─── load meta (sourced; values may be quoted) ─────────────────────────
# shellcheck disable=SC1090
source "$SCEN_DIR/meta.env"

# failure-flow / lens scenarios delegate to a base scenario.
BASE_SCENARIO="${BASE_OVERRIDE:-${BASE_SCENARIO:-}}"
if [[ -n "$BASE_SCENARIO" ]]; then
  info "scenario '$SCENARIO' is a lens over base '$BASE_SCENARIO'"
  # Re-run the seed for the base, then re-emit with the lens overrides applied.
  base_out="$("$0" "$BASE_SCENARIO")"
  printf '%s\n' "$base_out"
  printf 'SCENARIO_ID=%s\n' "$SCENARIO"
  printf 'EXPECT_FAILURE=%s\n' "${EXPECT_FAILURE:-1}"
  [[ -n "${PFACTORY_TRIAGER_GIT_WRITE:-}" ]] && printf 'PFACTORY_TRIAGER_GIT_WRITE=%s\n' "$PFACTORY_TRIAGER_GIT_WRITE"
  ok "lens '$SCENARIO' seeded over '$BASE_SCENARIO'"
  exit 0
fi

# ─── 1. seed the AIFactory spec workspace ──────────────────────────────
SPEC_WS="${AIFACTORY_ROOT}/workspaces/${PROJECT_ID}/specs/${SPEC_ID}"

if [[ -n "${DELEGATE_SEED:-}" ]]; then
  info "delegating spec seed to ${DELEGATE_SEED}"
  ( cd "$REPO_ROOT" && bash "$DELEGATE_SEED" ) >&2 || die "delegate seed failed"
else
  [[ -f "$SCEN_DIR/spec-content/spec.md" ]] || die "missing spec-content/spec.md"
  [[ -f "$SCEN_DIR/spec-content/implementation_plan.json" ]] || die "missing implementation_plan.json"
  mkdir -p "$SPEC_WS"
  cp -f "$SCEN_DIR/spec-content/spec.md" "$SPEC_WS/spec.md"
  cp -f "$SCEN_DIR/spec-content/implementation_plan.json" "$SPEC_WS/implementation_plan.json"
  if command -v python3 >/dev/null 2>&1; then
    python3 -c "import json,sys; json.load(open('$SPEC_WS/implementation_plan.json'))" \
      || die "implementation_plan.json is not valid JSON"
  fi
  ok "seeded AIFactory spec → $SPEC_WS"
fi

# ─── 2. materialise the local SUT into a git repo (if any) ─────────────
REPO_PATH=""
if [[ -d "$SCEN_DIR/sut" ]]; then
  REPO_PATH="${SUT_ROOT}/${SCENARIO}"
  info "materialising SUT → $REPO_PATH"
  rm -rf "$REPO_PATH"
  mkdir -p "$REPO_PATH"
  ( cd "$REPO_PATH"
    git init -q
    git checkout -q -b "${BASE_REF:-main}"
    git config user.email "demo@pfactory.local"
    git config user.name  "PFactory Demo"

    # Base commit: project skeleton WITHOUT the feature, so base_ref..branch is
    # a clean diff containing exactly the finished feature.
    case "${PROJECT_KIND:-}" in
      python)
        cat > pyproject.toml <<PYEOF
[project]
name = "${PROJECT_ID}"
version = "0.0.0"
requires-python = ">=3.12"

[tool.pytest.ini_options]
# Make the SUT modules at the repo root importable from tests/ in the
# sandbox (otherwise \`from pricing import ...\` is ModuleNotFoundError).
pythonpath = ["."]
PYEOF
        ;;
      polyglot)
        cat > pyproject.toml <<PYEOF
[project]
name = "${PROJECT_ID}"
version = "0.0.0"
requires-python = ">=3.12"
dependencies = ["fastapi", "uvicorn"]

[tool.pytest.ini_options]
pythonpath = ["."]
PYEOF
        cat > package.json <<PKGEOF
{ "name": "${PROJECT_ID}", "version": "0.0.0", "private": true,
  "dependencies": { "react": "^19.0.0" } }
PKGEOF
        ;;
    esac
    printf '# %s\n\nPFactory demo SUT (%s).\n' "$PROJECT_NAME" "$SCENARIO" > README.md
    git add -A && git commit -q -m "chore: scaffold ${SCENARIO} demo project"

    # Feature branch: drop in the finished feature + .pfactory.yml + catalog.
    git checkout -q -b "${BRANCH}"
    cp -a "$SCEN_DIR/sut/." .
    mkdir -p .pfactory
    printf '{"version": 1, "updated_at": "1970-01-01T00:00:00Z", "tests": []}\n' \
      > .pfactory/tests-catalog.json
    git add -A && git commit -q -m "feat: ${SCENARIO} finished feature for PFactory handover"
  )
  ok "SUT repo ready: ${BRANCH} off ${BASE_REF:-main}"
  # sanity: the diff must be non-empty
  ndiff="$(cd "$REPO_PATH" && git diff --name-only "${BASE_REF:-main}".."${BRANCH}" | wc -l)"
  [[ "$ndiff" -ge 1 ]] || die "base→branch diff is empty (snapshotter would see nothing)"
  info "diff: ${ndiff} changed file(s)"
elif [[ -n "${EXTERNAL_REPO:-}" ]]; then
  REPO_PATH="external:${EXTERNAL_REPO}"
  info "SUT is external repo ${EXTERNAL_REPO} (clone/deploy handled by /demo)"
fi

# ─── 3. emit handover parameters ───────────────────────────────────────
echo
printf '%s── handover parameters ──%s\n' "$C_BOLD" "$C_R" >&2
cat <<OUT
SCENARIO_ID=${SCENARIO}
PROJECT_ID=${PROJECT_ID}
PROJECT_NAME=${PROJECT_NAME}
SPEC_ID=${SPEC_ID}
SPEC_WS=${SPEC_WS}
REPO_PATH=${REPO_PATH}
BRANCH=${BRANCH:-}
BASE_REF=${BASE_REF:-main}
LANES=${LANES:-}
EXPECT_FAILURE=${EXPECT_FAILURE:-0}
OUT
ok "scenario '${SCENARIO}' seeded"
