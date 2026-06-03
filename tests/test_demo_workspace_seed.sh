#!/usr/bin/env bash
#
# test_demo_workspace_seed.sh — verify scripts/seed-aifactory-workspace.sh
# behaves as the demo glue phase expects:
#
#   1. Writes the spec.md + implementation_plan.json into the layout
#      `<root>/workspaces/pfactory-demo/specs/001-greeting-generator/`
#   2. spec.md contains the canonical ACs (AC#1, AC#5) and vocabulary
#   3. implementation_plan.json is valid JSON with exactly 5 phases
#   4. Re-running the script produces NO diff (idempotent)
#
# Runs against an isolated temp dir (PFACTORY_AIFACTORY_ROOT override) —
# never touches the user's real ~/.aifactory.
#
# Usage:
#   ./tests/test_demo_workspace_seed.sh
#
# Exit codes:
#   0 — all assertions passed
#   1 — at least one assertion failed
#
set -euo pipefail

# ─── Locate script under test ──────────────────────────────────────────

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${THIS_DIR}/.." && pwd)"
SEED_SCRIPT="${REPO_ROOT}/scripts/seed-aifactory-workspace.sh"

if [[ ! -x "$SEED_SCRIPT" ]]; then
    echo "FAIL: $SEED_SCRIPT is not executable or missing" >&2
    exit 1
fi

# ─── Counters ──────────────────────────────────────────────────────────

PASS=0
FAIL=0

pass() {
    PASS=$((PASS + 1))
    printf '  [pass] %s\n' "$1"
}

fail() {
    FAIL=$((FAIL + 1))
    printf '  [FAIL] %s\n' "$1" >&2
}

assert_file() {
    if [[ -f "$2" ]]; then pass "$1 ($2)"; else fail "$1: not a file: $2"; fi
}
assert_dir() {
    if [[ -d "$2" ]]; then pass "$1 ($2)"; else fail "$1: not a directory: $2"; fi
}
assert_contains(){
    if grep -q -- "$2" "$3"; then
        pass "$1 — found '$2' in $(basename "$3")"
    else
        fail "$1 — '$2' missing from $3"
    fi
}

# ─── Set up isolated workspace root ────────────────────────────────────

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

echo "Using temp workspace root: $TMP_ROOT"

WORKSPACE_DIR="${TMP_ROOT}/workspaces/pfactory-demo"
SPEC_DIR="${WORKSPACE_DIR}/specs/001-greeting-generator"
SPEC_FILE="${SPEC_DIR}/spec.md"
PLAN_FILE="${SPEC_DIR}/implementation_plan.json"

# ─── Test 1: first invocation produces the expected layout ─────────────

echo
echo "Test 1: first invocation writes the expected layout"
PFACTORY_AIFACTORY_ROOT="$TMP_ROOT" "$SEED_SCRIPT" >/dev/null

assert_dir  "project_id dir exists" "$WORKSPACE_DIR"
assert_dir  "spec dir exists"       "$SPEC_DIR"
assert_file "spec.md exists"        "$SPEC_FILE"
assert_file "implementation_plan.json exists" "$PLAN_FILE"

# ─── Test 2: spec.md content sanity ────────────────────────────────────

echo
echo "Test 2: spec.md contains canonical AC markers + vocabulary"
assert_contains "header" "# Greeting Generator" "$SPEC_FILE"
assert_contains "AC#1 marker" "AC#1" "$SPEC_FILE"
assert_contains "AC#5 marker" "AC#5" "$SPEC_FILE"
assert_contains "greeting vocab — hello"     "hello"     "$SPEC_FILE"
assert_contains "greeting vocab — welcome"   "welcome"   "$SPEC_FILE"
assert_contains "snarky vocab — obviously"   "obviously" "$SPEC_FILE"
assert_contains "snarky vocab — whatever"    "whatever"  "$SPEC_FILE"
assert_contains "Clear button mention"       "Clear"     "$SPEC_FILE"

# ─── Test 3: implementation_plan.json is well-formed ───────────────────

echo
echo "Test 3: implementation_plan.json is valid JSON with 5 phases"
if command -v python3 >/dev/null 2>&1; then
    if python3 - <<PY 2>/dev/null
import json, sys
with open("$PLAN_FILE") as fh:
    plan = json.load(fh)
assert plan.get("version") == 1, "version != 1"
phases = plan.get("phases")
assert isinstance(phases, list), "phases not a list"
assert len(phases) == 5, f"expected 5 phases, got {len(phases)}"
ids = [p.get("id") for p in phases]
assert ids == ["AC#1", "AC#2", "AC#3", "AC#4", "AC#5"], f"unexpected ids: {ids}"
for p in phases:
    assert p.get("status") == "complete", f"phase {p.get('id')} not complete"
sys.exit(0)
PY
    then
        pass "implementation_plan.json: version=1, 5 phases AC#1..AC#5, all complete"
    else
        fail "implementation_plan.json failed structured JSON validation"
    fi
else
    fail "python3 unavailable — cannot validate JSON structure"
fi

# ─── Test 4: idempotence (second run = no diff) ────────────────────────

echo
echo "Test 4: second invocation is idempotent (no file diff)"
SPEC_SHA_BEFORE="$(sha256sum "$SPEC_FILE" | awk '{print $1}')"
PLAN_SHA_BEFORE="$(sha256sum "$PLAN_FILE" | awk '{print $1}')"

PFACTORY_AIFACTORY_ROOT="$TMP_ROOT" "$SEED_SCRIPT" >/dev/null

SPEC_SHA_AFTER="$(sha256sum "$SPEC_FILE" | awk '{print $1}')"
PLAN_SHA_AFTER="$(sha256sum "$PLAN_FILE" | awk '{print $1}')"

if [[ "$SPEC_SHA_BEFORE" == "$SPEC_SHA_AFTER" ]]; then
    pass "spec.md unchanged after re-run"
else
    fail "spec.md content drifted on re-run (before=$SPEC_SHA_BEFORE after=$SPEC_SHA_AFTER)"
fi

if [[ "$PLAN_SHA_BEFORE" == "$PLAN_SHA_AFTER" ]]; then
    pass "implementation_plan.json unchanged after re-run"
else
    fail "implementation_plan.json content drifted on re-run (before=$PLAN_SHA_BEFORE after=$PLAN_SHA_AFTER)"
fi

# ─── Report ────────────────────────────────────────────────────────────

echo
echo "───────────────────────────────────────────"
printf 'Passed: %d   Failed: %d\n' "$PASS" "$FAIL"
echo "───────────────────────────────────────────"

if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
exit 0
