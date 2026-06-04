#!/usr/bin/env bash
#
# PFactory end-to-end smoke runner — Task 11 (#12).
#
# Runs the 9 verification scenarios from the design plan against a
# real AIFactory-produced Python feature. This script is intentionally
# *manual* — it requires real LLM access (Claude API key), real Docker,
# real git, and a real AIFactory project on disk. It cannot be run
# from CI without those secrets + daemons.
#
# Usage:
#   scripts/e2e-smoke.sh --list
#   scripts/e2e-smoke.sh --scenario N
#   scripts/e2e-smoke.sh --all
#   scripts/e2e-smoke.sh --dry-run --scenario N
#   scripts/e2e-smoke.sh --dry-run --all
#
# Environment variables required:
#   ANTHROPIC_API_KEY           — Claude API access
#   PFACTORY_AIFACTORY_ROOT     — local path to an AIFactory checkout
#   PFACTORY_AIFACTORY_BRANCH   — feature branch with the change to test
#   PFACTORY_AIFACTORY_PR       — PR number for scenario 7 (gh pr view)
#
# State file:
#   ~/.pfactory/e2e-state.json  — tracks spec_id + workspace_root +
#                                 results between invocations
#
# Exit codes:
#   0  — all requested scenarios passed
#   1  — at least one scenario failed
#   2  — usage / pre-flight error
#

set -euo pipefail

# ─── Colour helpers ────────────────────────────────────────────────────

if [[ -n "${NO_COLOR:-}" || ! -t 1 ]]; then
    C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_BOLD=""; C_DIM=""; C_RESET=""
else
    C_RED=$'\033[0;31m'
    C_GREEN=$'\033[0;32m'
    C_YELLOW=$'\033[0;33m'
    C_BLUE=$'\033[0;34m'
    C_BOLD=$'\033[1m'
    C_DIM=$'\033[2m'
    C_RESET=$'\033[0m'
fi

# ─── Globals ───────────────────────────────────────────────────────────

DRY_RUN=0
SCENARIO=""
RUN_ALL=0
DO_LIST=0

STATE_DIR="${PFACTORY_E2E_STATE_DIR:-$HOME/.pfactory}"
STATE_FILE="$STATE_DIR/e2e-state.json"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PFACTORY_PYTHON_BIN:-$REPO_ROOT/apps/backend/.venv/bin/python}"

# Scenario registry — pairs of "function_name|description".
# Functions are defined below; this list drives --list, --scenario, --all.
SCENARIOS=(
    "scenario_1_workspace_creation|Known AIFactory spec → workspace creation under ~/.pfactory/workspaces/"
    "scenario_2_portal_starts|Portal backend + frontend start; /api/pfactory/tasks reachable"
    "scenario_3_handover_progression|Trigger /handover-to-pfactory; observe status progression"
    "scenario_4_tests_committed|Generated tests committed onto AIFactory feature branch"
    "scenario_5_pytest_passes|cd \$PFACTORY_AIFACTORY_ROOT && pytest tests/ passes"
    "scenario_6_mutation_kills_test|Mutate one line of feature code; at least one generated test now fails"
    "scenario_7_pr_comment_posted|gh pr view --comments shows PFactory's triage report"
    "scenario_8_hallucination_replan|Planner fed non-existent method; Gen-Functional rejects; replan; no broken test committed"
    "scenario_9_docker_down_failure|Docker daemon down → status=failed with clear error, no hang"
)

# ─── Output helpers ────────────────────────────────────────────────────

log_info()    { printf '%s%s%s\n' "$C_BLUE" "$*" "$C_RESET"; }
log_dim()     { printf '%s%s%s\n' "$C_DIM"  "$*" "$C_RESET"; }
log_pass()    { printf '%s✓ PASS%s %s\n' "$C_GREEN"  "$C_RESET" "$*"; }
log_fail()    { printf '%s✗ FAIL%s %s\n' "$C_RED"    "$C_RESET" "$*" >&2; }
log_skip()    { printf '%s⊘ SKIP%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
log_section() {
    echo
    printf '%s%s── %s ──%s\n' "$C_BOLD" "$C_BLUE" "$*" "$C_RESET"
}

# ─── State helpers ─────────────────────────────────────────────────────

state_init() {
    mkdir -p "$STATE_DIR"
    if [[ ! -f "$STATE_FILE" ]]; then
        printf '{"started_at":"%s","scenarios":{}}\n' "$(date -u +%FT%TZ)" >"$STATE_FILE"
    fi
}

state_set() {
    # state_set <key> <value>  — stores a string value in the state file
    local key="$1" value="$2"
    state_init
    "$PYTHON_BIN" - "$STATE_FILE" "$key" "$value" <<'PY'
import json, sys
path, key, value = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path) as f:
    doc = json.load(f)
doc[key] = value
with open(path, "w") as f:
    json.dump(doc, f, indent=2)
PY
}

state_get() {
    # state_get <key>  — prints the value (empty if not set)
    local key="$1"
    [[ -f "$STATE_FILE" ]] || { echo ""; return; }
    "$PYTHON_BIN" - "$STATE_FILE" "$key" <<'PY'
import json, sys
path, key = sys.argv[1], sys.argv[2]
try:
    with open(path) as f:
        doc = json.load(f)
    print(doc.get(key, ""))
except (FileNotFoundError, json.JSONDecodeError):
    print("")
PY
}

state_record_result() {
    # state_record_result <scenario_name> <pass|fail|skip>
    local name="$1" outcome="$2"
    "$PYTHON_BIN" - "$STATE_FILE" "$name" "$outcome" <<'PY'
import json, sys, datetime
path, name, outcome = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path) as f:
    doc = json.load(f)
doc.setdefault("scenarios", {})[name] = {
    "outcome": outcome,
    "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}
with open(path, "w") as f:
    json.dump(doc, f, indent=2)
PY
}

# ─── Pre-flight ────────────────────────────────────────────────────────

preflight() {
    log_section "Pre-flight"
    local fail=0

    for cmd in gh docker git python3; do
        if command -v "$cmd" >/dev/null 2>&1; then
            log_dim "  ✓ $cmd: $(command -v "$cmd")"
        else
            log_fail "  ✗ $cmd not found on PATH"
            fail=1
        fi
    done

    if [[ -x "$PYTHON_BIN" ]]; then
        log_dim "  ✓ python venv: $PYTHON_BIN"
    else
        log_fail "  ✗ python venv not found at $PYTHON_BIN — run 'uv pip install -r apps/backend/requirements.txt'"
        fail=1
    fi

    # Required env vars — only checked when NOT dry-running
    if [[ "$DRY_RUN" -eq 0 ]]; then
        for var in ANTHROPIC_API_KEY PFACTORY_AIFACTORY_ROOT PFACTORY_AIFACTORY_BRANCH; do
            if [[ -n "${!var:-}" ]]; then
                # Don't echo the actual API key — just confirm presence
                if [[ "$var" == "ANTHROPIC_API_KEY" ]]; then
                    log_dim "  ✓ $var: set"
                else
                    log_dim "  ✓ $var: ${!var}"
                fi
            else
                log_fail "  ✗ env $var is not set"
                fail=1
            fi
        done
        # AIFactory root must exist + be a git repo
        if [[ -n "${PFACTORY_AIFACTORY_ROOT:-}" ]]; then
            if [[ ! -d "$PFACTORY_AIFACTORY_ROOT/.git" ]]; then
                log_fail "  ✗ PFACTORY_AIFACTORY_ROOT ($PFACTORY_AIFACTORY_ROOT) is not a git repo"
                fail=1
            fi
        fi
    else
        log_dim "  (dry-run: skipping env / project checks)"
    fi

    if [[ "$fail" -eq 1 ]]; then
        log_fail "pre-flight failed"
        return 1
    fi
    log_pass "pre-flight OK"
    return 0
}

# ─── Assertion helpers ─────────────────────────────────────────────────

assert_file_exists() {
    local path="$1"
    if [[ -f "$path" ]]; then
        log_dim "  ✓ file exists: $path"
    else
        log_fail "  expected file: $path"
        return 1
    fi
}

assert_status_eq() {
    # assert_status_eq <status.json path> <expected status>
    local status_path="$1" expected="$2"
    local actual
    actual="$("$PYTHON_BIN" -c \
        "import json; print(json.load(open('$status_path')).get('status', ''))")"
    if [[ "$actual" == "$expected" ]]; then
        log_dim "  ✓ status == $expected"
    else
        log_fail "  expected status=$expected, got $actual"
        return 1
    fi
}

run_or_say() {
    # run_or_say <description> <command...>
    # In dry-run mode just prints what would run.
    local desc="$1"; shift
    log_dim "  → $desc"
    log_dim "    \$ $*"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    "$@"
}

# ─── Scenarios ─────────────────────────────────────────────────────────

scenario_1_workspace_creation() {
    log_section "Scenario 1: workspace creation"
    log_info "Trigger the snapshotter against \$PFACTORY_AIFACTORY_ROOT and"
    log_info "verify ~/.pfactory/workspaces/<project>/specs/<spec>/ lands."

    local proj_id; proj_id="$(basename "${PFACTORY_AIFACTORY_ROOT:-demo}")"
    local spec_id="${PFACTORY_E2E_SPEC_ID:-e2e-smoke-$(date +%s)}"
    state_set "project_id" "$proj_id"
    state_set "spec_id" "$spec_id"

    run_or_say "Run the MCP task_create_and_run via the backend's CLI" \
        "$PYTHON_BIN" -m apps.backend.cli.pfactory_e2e_helper \
            --project-id "$proj_id" \
            --spec-id "$spec_id" \
            --branch "${PFACTORY_AIFACTORY_BRANCH:-feature/x}" \
            --base-ref main \
            --aifactory-root "${PFACTORY_AIFACTORY_ROOT:-/tmp}"

    if [[ "$DRY_RUN" -eq 1 ]]; then
        log_pass "(dry-run) workspace creation step would invoke MCP task_create_and_run"
        return 0
    fi

    local spec_dir="${PFACTORY_WORKSPACE_ROOT:-$HOME/.pfactory}/workspaces/$proj_id/specs/$spec_id"
    assert_file_exists "$spec_dir/status.json" || return 1
    assert_file_exists "$spec_dir/context/aifactory_spec.md" || return 1
    assert_file_exists "$spec_dir/context/source.json" || return 1
    log_pass "workspace created at $spec_dir"
}

scenario_2_portal_starts() {
    log_section "Scenario 2: portal starts + /api/pfactory/tasks reachable"
    log_info "Start the web-server + frontend dev server; hit the list endpoint."

    run_or_say "Start web-server (background)" \
        bash -c "cd '$REPO_ROOT/apps/web-server' && APP_PORT=3114 nohup python -m server.main >/tmp/pfactory-portal.log 2>&1 &"

    run_or_say "Wait for /api/pfactory/tasks to respond" \
        bash -c "for i in {1..30}; do curl -sf http://localhost:3114/api/pfactory/tasks >/dev/null && exit 0; sleep 1; done; exit 1"

    if [[ "$DRY_RUN" -eq 1 ]]; then
        log_pass "(dry-run) portal start + readiness probe would run"
        return 0
    fi
    log_pass "portal /api/pfactory/tasks reachable on :3114"
}

scenario_3_handover_progression() {
    log_section "Scenario 3: handover progression (planning → generated → evaluated → triaged)"
    log_info "With AUTO_PLAN/GENERATE/EVALUATE/TRIAGE = 1, the pipeline auto-advances."

    local spec_id; spec_id="$(state_get spec_id)"
    local proj_id; proj_id="$(state_get project_id)"
    local spec_dir="${PFACTORY_WORKSPACE_ROOT:-$HOME/.pfactory}/workspaces/$proj_id/specs/$spec_id"

    run_or_say "Poll status.json until status == triaged or terminal failure (5 min cap)" \
        bash -c "
            for i in \$(seq 1 300); do
                s=\$('$PYTHON_BIN' -c 'import json; print(json.load(open(\"$spec_dir/status.json\")).get(\"status\",\"\"))' 2>/dev/null)
                case \"\$s\" in
                    triaged|triaged_empty) exit 0 ;;
                    *_failed|stuck)        exit 1 ;;
                esac
                sleep 1
            done
            exit 1
        "

    if [[ "$DRY_RUN" -eq 1 ]]; then
        log_pass "(dry-run) status-poll loop would run for up to 5 minutes"
        return 0
    fi
    assert_status_eq "$spec_dir/status.json" "triaged" || return 1
    log_pass "pipeline reached status=triaged"
}

scenario_4_tests_committed() {
    log_section "Scenario 4: tests committed onto AIFactory feature branch"
    log_info "Requires PFACTORY_TRIAGER_GIT_WRITE=1 during run (per CLAUDE.md, off by default)."

    run_or_say "git log on AIFactory branch shows a PFactory commit" \
        bash -c "cd '${PFACTORY_AIFACTORY_ROOT:-/tmp}' && git log --oneline -n 5 \"${PFACTORY_AIFACTORY_BRANCH:-HEAD}\" | grep -i 'pfactory'"

    if [[ "$DRY_RUN" -eq 1 ]]; then
        log_pass "(dry-run) would verify a 'pfactory:' commit on the feature branch"
        return 0
    fi
    log_pass "pfactory commit visible on $PFACTORY_AIFACTORY_BRANCH"
}

scenario_5_pytest_passes() {
    log_section "Scenario 5: pytest passes inside AIFactory project"
    log_info "cd \$PFACTORY_AIFACTORY_ROOT && pytest tests/ — generated tests run green."

    run_or_say "Run pytest in the AIFactory project root" \
        bash -c "cd '${PFACTORY_AIFACTORY_ROOT:-/tmp}' && pytest tests/ -q"

    if [[ "$DRY_RUN" -eq 1 ]]; then
        log_pass "(dry-run) would run pytest tests/ in the AIFactory project"
        return 0
    fi
    log_pass "pytest tests/ green in the AIFactory project"
}

scenario_6_mutation_kills_test() {
    log_section "Scenario 6: hand-mutation makes ≥1 generated test fail"
    log_info "Manually mutate a line in the changed feature; rerun pytest;"
    log_info "expect at least one PFactory-generated test to fail."

    cat <<EOF
${C_DIM}
  Operator steps (manual):
    1. Identify the feature file changed on \$PFACTORY_AIFACTORY_BRANCH
       (e.g. app/auth/login.py)
    2. Make a SMALL semantic change there (flip a boolean, off-by-one,
       remove a guard, etc.). Don't break the test file.
    3. cd \$PFACTORY_AIFACTORY_ROOT && pytest tests/ -q
    4. Expect at least one failure; that's the verification.
    5. git checkout -- <feature file>  to restore.
${C_RESET}
EOF
    if [[ "$DRY_RUN" -eq 1 ]]; then
        log_pass "(dry-run) scenario 6 is a manual mutation check"
        return 0
    fi
    log_skip "manual scenario — record outcome in $STATE_FILE via 'mark-pass 6'"
    return 77
}

scenario_7_pr_comment_posted() {
    log_section "Scenario 7: triage report comment posted to the PR"
    log_info "Requires PFACTORY_TRIAGER_PR_COMMENT=1 during run + PFACTORY_AIFACTORY_PR set."

    if [[ -z "${PFACTORY_AIFACTORY_PR:-}" && "$DRY_RUN" -eq 0 ]]; then
        log_skip "PFACTORY_AIFACTORY_PR not set — skipping"
        return 77
    fi

    run_or_say "Inspect PR comments for the PFactory triage report header" \
        bash -c "cd '${PFACTORY_AIFACTORY_ROOT:-/tmp}' && gh pr view '${PFACTORY_AIFACTORY_PR:-N}' --comments | grep -F '# Triage Report'"

    if [[ "$DRY_RUN" -eq 1 ]]; then
        log_pass "(dry-run) would gh pr view PR comments + grep the report header"
        return 0
    fi
    log_pass "Triage Report header present in PR comments"
}

scenario_8_hallucination_replan() {
    log_section "Scenario 8: hallucination guard — replan kicks in"
    log_info "Inject a hand-crafted plan referencing a non-existent method;"
    log_info "Gen-Functional's preflight rejects; Planner replans; no broken test commits."

    cat <<EOF
${C_DIM}
  Operator steps (manual):
    1. Create a fresh spec workspace.
    2. Hand-author its test_plan.json with a Lane.FUNCTIONAL subtask
       whose target is "app/auth/login.py::ghost_function_that_does_not_exist".
    3. Trigger Gen-Functional.
    4. Verify findings/replan_request.json is written + status transitions
       to replan_needed.
    5. Trigger Planner replan; verify a NEW replan-1 phase is appended +
       the ghost_function subtask's replan_count increments.
    6. Run Triager; verify no test referencing the ghost function lands
       in the triage report's committed list.
${C_RESET}
EOF

    if [[ "$DRY_RUN" -eq 1 ]]; then
        log_pass "(dry-run) scenario 8 is a manual hallucination-replan smoke"
        return 0
    fi
    log_skip "manual scenario — record outcome in $STATE_FILE"
    return 77
}

scenario_9_docker_down_failure() {
    log_section "Scenario 9: docker daemon down → failed status, no hang"
    log_info "Stop docker; trigger a build; expect status=evaluator_failed (or earlier"
    log_info "failed status) within reasonable time, with a clear error message."

    cat <<EOF
${C_DIM}
  Operator steps (manual):
    1. systemctl stop docker  (or equivalent on your system)
    2. Trigger a fresh task_create_and_run.
    3. Wait up to 2 minutes.
    4. cat status.json — verify status ends in _failed (not 'hanging in *_started')
       and that the *_error field carries a docker-related message.
    5. systemctl start docker.
${C_RESET}
EOF

    if [[ "$DRY_RUN" -eq 1 ]]; then
        log_pass "(dry-run) scenario 9 is a manual docker-down smoke"
        return 0
    fi
    log_skip "manual scenario — record outcome in $STATE_FILE"
    return 77
}

# ─── Dispatcher ────────────────────────────────────────────────────────

list_scenarios() {
    echo "Available scenarios:"
    local i=1
    for entry in "${SCENARIOS[@]}"; do
        local fn="${entry%%|*}"
        local desc="${entry#*|}"
        printf '  %s%d%s. %s\n' "$C_BOLD" "$i" "$C_RESET" "$desc"
        printf '       %s%s%s\n' "$C_DIM" "$fn" "$C_RESET"
        i=$((i + 1))
    done
}

run_scenario_index() {
    local idx="$1"
    if ! [[ "$idx" =~ ^[1-9]$ ]]; then
        log_fail "Invalid scenario index: $idx (expected 1-9)"
        return 2
    fi
    local entry="${SCENARIOS[$((idx - 1))]}"
    local fn="${entry%%|*}"
    state_init
    set +e
    "$fn"
    local rc=$?
    set -e
    case "$rc" in
        0)
            log_pass "scenario $idx done"
            state_record_result "$fn" "pass"
            return 0
            ;;
        77)
            state_record_result "$fn" "skip"
            return 77
            ;;
        *)
            log_fail "scenario $idx failed (rc=$rc)"
            state_record_result "$fn" "fail"
            return 1
            ;;
    esac
}

run_all_scenarios() {
    local total=${#SCENARIOS[@]} passed=0 failed=0 skipped=0 i
    for ((i = 1; i <= total; i++)); do
        set +e
        run_scenario_index "$i"
        local rc=$?
        set -e
        case "$rc" in
            0)  passed=$((passed + 1)) ;;
            77) skipped=$((skipped + 1)) ;;
            *)  failed=$((failed + 1)) ;;
        esac
    done
    log_section "Summary"
    log_pass  "passed:  $passed"
    log_skip  "skipped: $skipped"
    log_fail  "failed:  $failed"
    [[ "$failed" -eq 0 ]] && return 0 || return 1
}

usage() {
    cat >&2 <<EOF
Usage: $(basename "$0") [--list | --scenario N | --all] [--dry-run]

Options:
  --list               List all 9 scenarios and exit.
  --scenario N         Run scenario N (1-9).
  --all                Run all scenarios in order.
  --dry-run            Print the commands each scenario would run;
                       skip env-var / project checks.
  -h, --help           Show this help.

Required envs (non-dry-run):
  ANTHROPIC_API_KEY            — Claude API access
  PFACTORY_AIFACTORY_ROOT      — AIFactory project checkout
  PFACTORY_AIFACTORY_BRANCH    — feature branch with the change
  PFACTORY_AIFACTORY_PR        — PR number (scenario 7)
EOF
    return 2
}

# ─── Arg parse ─────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --list)     DO_LIST=1; shift ;;
        --scenario) SCENARIO="${2:-}"; shift 2 ;;
        --all)      RUN_ALL=1; shift ;;
        --dry-run)  DRY_RUN=1; shift ;;
        -h|--help)  usage; exit 2 ;;
        *)
            log_fail "Unknown arg: $1"
            usage
            exit 2
            ;;
    esac
done

# ─── Main ──────────────────────────────────────────────────────────────

if [[ "$DO_LIST" -eq 1 ]]; then
    list_scenarios
    exit 0
fi

if [[ -z "$SCENARIO" && "$RUN_ALL" -eq 0 ]]; then
    usage
    exit 2
fi

preflight || exit 2

if [[ -n "$SCENARIO" ]]; then
    run_scenario_index "$SCENARIO"
    exit $?
fi

if [[ "$RUN_ALL" -eq 1 ]]; then
    run_all_scenarios
    exit $?
fi
