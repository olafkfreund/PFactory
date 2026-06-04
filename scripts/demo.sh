#!/usr/bin/env bash
#
# PFactory end-to-end demo.
#
# What it shows:
#   1. Seed olafkfreund/pfactory-demo with 3 GitHub issues (resets to known state)
#   2. Register the repo with your local PFactory portal
#   3. Import the issues → portal backlog tasks
#   4. Show the portal picked them up
#   5. Prompt you to drive Claude Code from another terminal
#   6. Kick off one autonomous build via the portal API
#
# Prereqs (script will fail fast if missing):
#   - gh CLI authenticated
#   - jq, curl
#   - Portal running at http://localhost:3114 (or pass --portal=URL)
#   - ~/.pfactory/.token exists (created by the portal at first boot)
#
# Flags:
#   --yolo         run uninterrupted (no Enter prompts between steps)
#   --no-reset     skip step 1 (don't touch the GitHub repo)
#   --portal=URL   override the portal base URL (default http://localhost:3114)
#   --help         print this help and exit
#
# Exit codes:
#   0 — demo completed end-to-end
#   1 — prereq missing (with a tactical fix message)
#   2 — portal returned an error mid-flow (with the curl command to repro)
#

set -euo pipefail

# ---------- defaults + flag parsing ----------

DEMO_REPO="olafkfreund/pfactory-demo"
DEMO_REPO_LOCAL="/tmp/pfactory-demo"
PORTAL="http://localhost:3114"
YOLO=0
NO_RESET=0

print_help() {
  sed -n '2,30p' "$0" | sed 's/^# *//'
}

for arg in "$@"; do
  case "$arg" in
    --yolo) YOLO=1 ;;
    --no-reset) NO_RESET=1 ;;
    --portal=*) PORTAL="${arg#--portal=}" ;;
    --help|-h) print_help; exit 0 ;;
    *) echo "Unknown flag: $arg" >&2; print_help; exit 1 ;;
  esac
done

# ---------- output helpers ----------

C_RESET='\033[0m'
C_BOLD='\033[1m'
C_GREEN='\033[32m'
C_YELLOW='\033[33m'
C_RED='\033[31m'
C_CYAN='\033[36m'

step() {
  echo
  echo -e "${C_BOLD}${C_CYAN}=== $1 ===${C_RESET}"
}
ok() { echo -e "  ${C_GREEN}✓${C_RESET} $1"; }
warn() { echo -e "  ${C_YELLOW}⚠${C_RESET} $1"; }
die() { echo -e "  ${C_RED}✗${C_RESET} $1" >&2; exit "${2:-1}"; }

prompt_enter() {
  [ "$YOLO" = "1" ] && return
  echo -en "${C_BOLD}Press Enter to continue${C_RESET} (or Ctrl-C to abort)... "
  read -r _
}

# ---------- prereq checks ----------

step "Step 0 — Checking prerequisites"

command -v gh >/dev/null || die "gh CLI not found. Install: https://cli.github.com/" 1
command -v jq >/dev/null || die "jq not found. Install: brew install jq / apt-get install jq" 1
command -v curl >/dev/null || die "curl not found." 1

gh auth status >/dev/null 2>&1 || die "gh CLI not authenticated. Run: gh auth login" 1
ok "gh authenticated as $(gh api user --jq .login)"

curl -fsS "${PORTAL}/api/health" >/dev/null 2>&1 \
  || die "Portal not reachable at ${PORTAL}. Start it: cd apps/web-server && python -m server.main" 1
ok "Portal reachable at ${PORTAL}"

TOKEN_FILE="${HOME}/.pfactory/.token"
[ -f "$TOKEN_FILE" ] || die "Token not found at ${TOKEN_FILE}. Start the portal at least once to create it." 1
TOKEN=$(cat "$TOKEN_FILE")
ok "Token loaded from ${TOKEN_FILE}"

AUTH_HEADER="Authorization: Bearer ${TOKEN}"
CONTENT_HEADER="Content-Type: application/json"

# ---------- step 1: seed the demo repo ----------

if [ "$NO_RESET" = "1" ]; then
  step "Step 1 — Skipping repo reset (--no-reset)"
else
  step "Step 1 — Reset ${DEMO_REPO} to known state"

  # Confirm the repo exists; if not, tell the user how to create it
  if ! gh repo view "$DEMO_REPO" >/dev/null 2>&1; then
    warn "${DEMO_REPO} doesn't exist."
    echo "  Create it first with:"
    echo "    gh repo create ${DEMO_REPO} --public \\"
    echo "      --description 'PFactory demo project'"
    die "Demo repo missing" 1
  fi

  # Close any open issues from a previous run
  CLOSE_COUNT=0
  while read -r num; do
    [ -z "$num" ] && continue
    gh issue close "$num" --repo "$DEMO_REPO" >/dev/null 2>&1 || true
    CLOSE_COUNT=$((CLOSE_COUNT + 1))
  done < <(gh issue list --repo "$DEMO_REPO" --state open --json number --jq '.[].number')
  [ "$CLOSE_COUNT" -gt 0 ] && ok "Closed $CLOSE_COUNT leftover open issue(s)" || ok "No leftover issues to close"

  # Seed three fresh issues
  ISSUE_1=$(gh issue create --repo "$DEMO_REPO" \
    --title "Add /healthz endpoint" \
    --body "Add a HTTP GET /healthz endpoint that returns {\"status\":\"ok\"} with HTTP 200. Should not require auth (load balancer probes will hit it)." \
    --label enhancement | grep -oE '[0-9]+$')
  ISSUE_2=$(gh issue create --repo "$DEMO_REPO" \
    --title "Document the Quick Start in README" \
    --body "Add a Quick Start section to README.md showing the three commands a new user runs to get the demo app running locally." \
    --label documentation | grep -oE '[0-9]+$')
  ISSUE_3=$(gh issue create --repo "$DEMO_REPO" \
    --title "Add /version endpoint with tests" \
    --body "Add /version that returns the build version + tests covering it. Read version from src/app/__init__.py __version__." \
    --label tests | grep -oE '[0-9]+$')

  ok "Created issues #${ISSUE_1}, #${ISSUE_2}, #${ISSUE_3}"
  ISSUE_NUMBERS="[${ISSUE_1},${ISSUE_2},${ISSUE_3}]"
fi

prompt_enter

# ---------- step 2: register the project ----------

step "Step 2 — Register ${DEMO_REPO} with the portal"

if [ ! -d "$DEMO_REPO_LOCAL" ]; then
  ok "Cloning ${DEMO_REPO} to ${DEMO_REPO_LOCAL}"
  gh repo clone "$DEMO_REPO" "$DEMO_REPO_LOCAL" >/dev/null 2>&1
else
  ok "Pulling latest in ${DEMO_REPO_LOCAL}"
  git -C "$DEMO_REPO_LOCAL" pull --quiet 2>&1 | grep -v "Already up to date" || true
fi

REGISTER_RESPONSE=$(curl -sS -w '\n%{http_code}' -X POST "${PORTAL}/api/projects" \
  -H "$AUTH_HEADER" -H "$CONTENT_HEADER" \
  -d "{\"path\":\"${DEMO_REPO_LOCAL}\",\"name\":\"pfactory-demo\"}")
REGISTER_BODY=$(echo "$REGISTER_RESPONSE" | head -n -1)
REGISTER_STATUS=$(echo "$REGISTER_RESPONSE" | tail -n 1)

if [ "$REGISTER_STATUS" = "201" ]; then
  PROJECT_ID=$(echo "$REGISTER_BODY" | jq -r '.id')
  ok "Registered project: $PROJECT_ID"
elif [ "$REGISTER_STATUS" = "409" ]; then
  # Already registered — find it in the list
  PROJECT_ID=$(curl -sS -H "$AUTH_HEADER" "${PORTAL}/api/projects" \
    | jq -r --arg p "$DEMO_REPO_LOCAL" '.[] | select(.path == $p) | .id')
  [ -n "$PROJECT_ID" ] && ok "Project already registered: $PROJECT_ID" \
    || die "409'd on register but project not in list" 2
else
  die "Register failed (HTTP ${REGISTER_STATUS}): $REGISTER_BODY" 2
fi

prompt_enter

# ---------- step 3: import GitHub issues → tasks ----------

if [ "$NO_RESET" = "0" ]; then
  step "Step 3 — Import GitHub issues into the backlog"

  IMPORT_RESPONSE=$(curl -sS -w '\n%{http_code}' -X POST \
    "${PORTAL}/api/projects/${PROJECT_ID}/github/import" \
    -H "$AUTH_HEADER" -H "$CONTENT_HEADER" \
    -d "{\"issueNumbers\":${ISSUE_NUMBERS}}")
  IMPORT_BODY=$(echo "$IMPORT_RESPONSE" | head -n -1)
  IMPORT_STATUS=$(echo "$IMPORT_RESPONSE" | tail -n 1)

  [ "$IMPORT_STATUS" = "200" ] || die "Import failed (HTTP ${IMPORT_STATUS}): $IMPORT_BODY" 2

  IMPORTED=$(echo "$IMPORT_BODY" | jq -r '. | length // 3')
  ok "Imported ${IMPORTED} issues as backlog tasks"
else
  step "Step 3 — Skipping import (--no-reset)"
fi

prompt_enter

# ---------- step 4: verify portal picked them up ----------

step "Step 4 — Verify portal picked up the tasks"

for i in 1 2 3 4 5; do
  TASK_COUNT=$(curl -sS -H "$AUTH_HEADER" \
    "${PORTAL}/api/projects/${PROJECT_ID}/tasks" \
    | jq '. | length // 0')
  [ "$TASK_COUNT" -ge 3 ] && break
  warn "Only ${TASK_COUNT} tasks visible — polling (attempt ${i}/5)"
  sleep 2
done

[ "$TASK_COUNT" -ge 3 ] || die "Portal never showed 3 tasks (got ${TASK_COUNT})" 2
ok "Portal shows ${TASK_COUNT} tasks"

# Print the task list
echo
curl -sS -H "$AUTH_HEADER" "${PORTAL}/api/projects/${PROJECT_ID}/tasks" \
  | jq -r '.[] | "    \(.specId // .spec_id): \(.title // .name)"'

# Open the portal in a browser
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open http://localhost:3115/ >/dev/null 2>&1 || true
elif command -v open >/dev/null 2>&1; then
  open http://localhost:3115/ || true
fi
ok "Browser opened to http://localhost:3115/"

prompt_enter

# ---------- step 5: prompt for Claude Code interaction ----------

step "Step 5 — Drive Claude Code from another terminal"

cat <<'EOF'

  In a SEPARATE terminal:

    cd /tmp/pfactory-demo
    claude

  Then ask Claude:

    "Refine the spec for the /healthz task. Edit .pfactory/specs/001-*/spec.md
     to specify the exact JSON response shape and add a unit-test acceptance
     criterion. Also tighten test_plan.json so subtask 1.1 reads
     'add /healthz route to src/app/main.py' rather than the placeholder."

  Watch the portal's task-detail view update in real time as Claude edits
  the files — the web-server's worktree-sync ticks every 3 seconds.
EOF

prompt_enter

# ---------- step 6: kick off an autonomous build ----------

step "Step 6 — Kick off an autonomous build for the first task"

FIRST_TASK_ID=$(curl -sS -H "$AUTH_HEADER" \
  "${PORTAL}/api/projects/${PROJECT_ID}/tasks" \
  | jq -r '.[0].id')
[ -n "$FIRST_TASK_ID" ] && [ "$FIRST_TASK_ID" != "null" ] \
  || die "No tasks to start" 2

ok "Starting task ${FIRST_TASK_ID}"

START_RESPONSE=$(curl -sS -w '\n%{http_code}' -X POST \
  "${PORTAL}/api/tasks/${FIRST_TASK_ID}/recover" \
  -H "$AUTH_HEADER" -H "$CONTENT_HEADER" \
  -d '{"targetStatus":"backlog","autoRestart":true}')
START_STATUS=$(echo "$START_RESPONSE" | tail -n 1)

[ "$START_STATUS" = "200" ] || die "Start failed (HTTP ${START_STATUS})" 2
ok "Task started"

# ---------- done ----------

step "Done"

cat <<EOF

  The agent is running. Watch progress in the browser:

    • Kanban:           http://localhost:3115/
    • Task detail:      Click the task card, switch to the "Live Console" tab
    • Demo repo:        https://github.com/${DEMO_REPO}
    • Portal API:       ${PORTAL}/api/tasks/${FIRST_TASK_ID}

  Tail the agent logs in this terminal:

    curl -sN -H "Authorization: Bearer \$(cat ~/.pfactory/.token)" \\
      ${PORTAL}/api/tasks/${FIRST_TASK_ID}/logs

  When QA approves, click Merge in the task detail panel to land the
  agent's commits on your local branch.

EOF
