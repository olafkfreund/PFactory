#!/usr/bin/env bash
#
# verify-fork.sh — sanity check after hard-forking AIFactory into PFactory.
#
# Asserts:
#   1. We are running from inside the PFactory repo root.
#   2. Obsolete modules (per the design plan's "Files to delete from the fork" list)
#      are actually gone.
#   3. Renamed paths exist under their new names (pfactory_server.py, test_plan/).
#   4. Key Python modules import cleanly (no broken imports left over from
#      deletions/renames).
#   5. No stray `aifactory` / `AIFactory` references survive outside the
#      documented allowlist of intentional cross-references.
#
# Exit codes:
#   0   all checks pass
#   1+  number of failed checks
#
# Usage:
#   ./scripts/verify-fork.sh
#   ./scripts/verify-fork.sh --no-import   # skip the Python import check
#                                          # (useful before deps are installed)
#
set -uo pipefail

# ---------- config ----------
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Modules to import (skip with --no-import). Adjust as the rewrite progresses.
PY_MODULES=(
    "apps.backend.mcp_server.pfactory_server"
    "apps.backend.test_plan.models"
    "apps.backend.context.project_analyzer"
)

# Files / dirs that MUST NOT exist after the fork (from design plan).
OBSOLETE_PATHS=(
    "apps/backend/agents/coder.py"
    "apps/backend/qa"
    "apps/backend/spec_runner.py"
    "apps/backend/spec_agents"
    "apps/backend/prompts/coder.md"
    "apps/backend/prompts/qa_reviewer.md"
    "apps/backend/prompts/qa_fixer.md"
    "apps/backend/prompts/complexity_assessor.md"
    "apps/backend/prompts/followup_planner.md"
    ".claude/skills/aifactory-spec"
    ".claude/skills/handover"
    "apps/backend/mcp_server/aifactory_server.py"
    "apps/backend/implementation_plan"
)

# Files / dirs that MUST exist after the fork.
REQUIRED_PATHS=(
    "apps/backend/mcp_server/pfactory_server.py"
    "apps/backend/test_plan"
    ".claude/skills/handover-to-pfactory/SKILL.md"
    ".mcp.json"
    "README.md"
    "LICENSE"
    "docs/_config.yml"
    "docs/index.md"
    ".agent-os/specs/2026-05-28-pfactory-mvp-walking-skeleton/spec.md"
    # Task 2 (#18): .pfactory.yml schema + parser
    "apps/backend/pfactory_yml/__init__.py"
    "apps/backend/pfactory_yml/schema.py"
    "apps/backend/pfactory_yml/parser.py"
    "apps/backend/pfactory_yml/secrets.py"
    "apps/backend/pfactory_yml/exceptions.py"
    ".pfactory.yml.example"
    # Task 3 (#19): tests-catalog schema + IO + lookup + migration
    "apps/backend/tests_catalog/__init__.py"
    "apps/backend/tests_catalog/schema.py"
    "apps/backend/tests_catalog/io.py"
    "apps/backend/tests_catalog/lookup.py"
    "apps/backend/tests_catalog/migration.py"
    "docs/tests-catalog.md"
    # Task 12 (#28): per-framework template starter set + engine
    "apps/backend/templates_pkg/__init__.py"
    "apps/backend/templates_pkg/engine.py"
    # Task 14 (#30): portal endpoints (framework registry, templates, skills, catalog)
    "apps/web-server/server/routes/pfactory_frameworks.py"
    "apps/web-server/server/routes/pfactory_templates.py"
    "apps/web-server/server/routes/pfactory_skills.py"
)

# Allowlist of paths that MAY legitimately mention `aifactory` / `AIFactory`.
# These are the documented intentional cross-references — files that DESCRIBE
# the handover-from-AIFactory relationship (skills, MCP tool descriptions,
# specs, the Pages site, quarantined inherited tests). Adding to this list
# must be justified in a commit message.
ALLOWLIST_GLOBS=(
    "docs/"
    ".agent-os/"
    "README.md"
    "scripts/verify-fork.sh"
    "scripts/e2e-smoke.sh"
    ".env.example"
    "CLAUDE.md"
    "CHANGELOG.md"
    ".claude/skills/handover-to-pfactory/"
    "companion-skills/"
    "apps/backend/agents/tools_pkg/tools/task_control.py"
    "apps/backend/workspaces/"
    # PFactory Planner files (Task 5, #6). Prompts + helpers + tests
    # legitimately describe the AIFactory→PFactory cross-reference
    # (planner reads ~/.aifactory/.../specs/.../ snapshot via Task 3).
    "apps/backend/agents/planner.py"
    "apps/backend/agents/gen_functional.py"
    "apps/backend/agents/evaluator.py"
    "apps/backend/agents/triager.py"
    "apps/backend/tools/git_writer.py"
    "apps/backend/tools/pr_comment.py"
    "apps/backend/prompts/"
    "apps/backend/prompts_pkg/"
    "tests/test_mcp_task_control.py"
    "tests/test_pfactory_mcp_tools.py"
    "tests/test_snapshotter.py"
    "tests/test_planner_prompts.py"
    "tests/test_planner.py"
    "tests/test_planner_integration.py"
    "tests/test_gen_functional.py"
    "tests/test_gen_functional_prompts.py"
    "tests/test_gen_functional_integration.py"
    "tests/test_evaluator.py"
    "tests/test_evaluator_prompts.py"
    "tests/test_evaluator_integration.py"
    "tests/test_triager.py"
    "tests/test_triager_integration.py"
    "tests/test_e2e_smoke_script.py"
    "apps/frontend-web/src/lib/pfactory-api.ts"
    "apps/frontend-web/src/components/pfactory/"
    "tests/fixtures/planner_smoke/"
    "guides/"
    ".git/"
    # .husky/pre-commit comments mention AIFactory test files for context
    ".husky/"
    # framework_registry docs reference the AIFactory spec handover relationship
    "docs/framework-registry.md"
    # Task 2 (#18): .pfactory.yml schema + parser. These files describe the
    # config file that AIFactory projects carry; references to "AIFactory repo"
    # are intentional documentation of the cross-project relationship.
    "apps/backend/pfactory_yml/"
    ".pfactory.yml.example"
    ".pre-commit-config.yaml"
    # Task 3 (#19): tests-catalog schema + IO + lookup + migration.
    # The catalog lives in the AIFactory repo; module docstrings and the
    # developer guide legitimately refer to "AIFactory repo" when describing
    # where the catalog is checked in.
    "apps/backend/tests_catalog/"
    "tests/test_tests_catalog.py"
    "docs/tests-catalog.md"
    # Task 12 (#28): per-framework template engine and 15 starter templates.
    "apps/backend/templates_pkg/"
    "frameworks/pytest/templates/"
    "frameworks/jest/templates/"
    "frameworks/playwright/templates/"
    "tests/test_templates.py"
    # Task 13 (#29): Claude Code skill bundles + slash commands.
    # The skill bodies describe scaffolding PFactory inside an AIFactory repo
    # and handing finished AIFactory features off to the PFactory pipeline.
    # Skill prose legitimately references the AIFactory cross-project
    # relationship — same allowlist pattern as the handover-to-pfactory skill.
    ".claude/skills/pfactory-init/"
    ".claude/skills/pfactory-add-test/"
    ".claude/skills/pfactory-from-template/"
    ".claude/commands/"
    "tests/test_skills.py"
    # Task 14 (#30): portal endpoints for framework registry, templates, skills,
    # catalog.  Route module docstrings reference the AIFactory→PFactory
    # cross-project relationship (framework registry serves data about the
    # AIFactory project's test stack; catalog is snapshotted from the AIFactory
    # repo at handover time).
    "apps/web-server/server/routes/pfactory_frameworks.py"
    "apps/web-server/server/routes/pfactory_templates.py"
    "apps/web-server/server/routes/pfactory_skills.py"
    # pfactory_tasks.py extended in Task 14 with /catalog endpoint whose docstring
    # legitimately references the AIFactory snapshotter (the source of the snapshot).
    "apps/web-server/server/routes/pfactory_tasks.py"
    "tests/test_pfactory_routes_frameworks.py"
    "tests/test_pfactory_routes_templates.py"
    "tests/test_pfactory_routes_skills.py"
)

# ---------- args ----------
DO_IMPORT_CHECK=1
for arg in "$@"; do
    case "$arg" in
        --no-import) DO_IMPORT_CHECK=0 ;;
        -h|--help)
            sed -n '2,/^set/p' "$0" | sed 's/^# //; s/^#//'
            exit 0
            ;;
        *) echo "verify-fork.sh: unknown arg: $arg" >&2; exit 2 ;;
    esac
done

# ---------- runner ----------
FAIL=0
pass() { printf "  \033[32m✓\033[0m %s\n" "$1"; }
fail() { printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL=$((FAIL+1)); }
section() { printf "\n\033[1m== %s ==\033[0m\n" "$1"; }

# ---------- 1. repo root ----------
section "repo root"
if [ -d "$REPO_ROOT/.git" ]; then
    pass "PFactory repo root: $REPO_ROOT"
else
    fail "not a git repo (no .git/ at $REPO_ROOT)"
fi
if [ "$(basename "$REPO_ROOT")" = "PFactory" ]; then
    pass "directory name is PFactory"
else
    fail "directory name is $(basename "$REPO_ROOT") (expected PFactory)"
fi

# ---------- 2. obsolete paths gone ----------
section "obsolete paths removed"
for p in "${OBSOLETE_PATHS[@]}"; do
    if [ -e "$p" ]; then
        fail "still exists: $p"
    else
        pass "gone: $p"
    fi
done

# ---------- 3. required paths exist ----------
section "required paths present"
for p in "${REQUIRED_PATHS[@]}"; do
    if [ -e "$p" ]; then
        pass "exists: $p"
    else
        fail "missing: $p"
    fi
done

# ---------- 4. Python import smoke ----------
if [ "$DO_IMPORT_CHECK" = "1" ]; then
    section "Python import smoke"
    if ! command -v python3 >/dev/null 2>&1; then
        fail "python3 not on PATH"
    else
        for mod in "${PY_MODULES[@]}"; do
            if python3 -c "import importlib; importlib.import_module('$mod')" 2>/dev/null; then
                pass "import $mod"
            else
                fail "import $mod"
            fi
        done
    fi
else
    section "Python import smoke (SKIPPED via --no-import)"
fi

# ---------- 5. stray aifactory references ----------
section "stray aifactory references outside allowlist"
if ! command -v rg >/dev/null 2>&1; then
    fail "ripgrep (rg) not on PATH — required for allowlist scan"
else
    IGNORE_ARGS=()
    for g in "${ALLOWLIST_GLOBS[@]}"; do
        IGNORE_ARGS+=("--glob" "!${g}**" "--glob" "!${g}")
    done
    # Ephemeral subagent worktrees live under .claude/worktrees/ and contain
    # complete PFactory checkouts (including legitimately-allowlisted files).
    # They're not part of the project tree we're verifying — skip them.
    IGNORE_ARGS+=("--glob" "!.claude/worktrees/**")
    # Search both lowercase and CamelCase. Source-only (rg respects .gitignore).
    HITS="$(rg --hidden --no-messages -l -e 'aifactory' -e 'AIFactory' "${IGNORE_ARGS[@]}" . 2>/dev/null || true)"
    if [ -z "$HITS" ]; then
        pass "no stray aifactory/AIFactory references"
    else
        fail "stray aifactory/AIFactory references in:"
        echo "$HITS" | sed 's/^/      /'
    fi
fi

# ---------- summary ----------
echo
if [ "$FAIL" -eq 0 ]; then
    printf "\033[32mverify-fork: PASS\033[0m\n"
    exit 0
else
    printf "\033[31mverify-fork: FAIL (%d check%s failed)\033[0m\n" "$FAIL" "$([ "$FAIL" -eq 1 ] || echo s)"
    exit "$FAIL"
fi
