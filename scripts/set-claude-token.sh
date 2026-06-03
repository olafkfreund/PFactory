#!/usr/bin/env bash
# Wire a long-lived Claude *subscription* token (from `claude setup-token`) into
# PFactory's "Personal" profile — securely. The token is read at a prompt (not
# echoed), passed to the API via env (never on a command line or in chat), and
# the response is printed with the token redacted.
#
#   bash scripts/set-claude-token.sh            # prompts for the sess-… token
#
# Targets the local full web-server (default :3198; override with PFACTORY_API).
set -euo pipefail

API="${PFACTORY_API:-http://127.0.0.1:3198}"

read -rsp 'Paste your sess-… token (from `claude setup-token`): ' PFTOKEN
echo
if [ -z "${PFTOKEN}" ]; then echo "no token entered; aborting." >&2; exit 1; fi

PFTOKEN="$PFTOKEN" PFAPI="$API" python3 - <<'PY'
import json, os, urllib.request, urllib.error

api = os.environ["PFAPI"]
tok = os.environ["PFTOKEN"].strip()
if not (tok.startswith("sess-") or tok.startswith("sk-ant-")):
    raise SystemExit("token must start with 'sess-' (setup-token) or 'sk-ant-'")

# Find an existing "Personal" profile id so we UPDATE rather than duplicate.
pid = None
try:
    with urllib.request.urlopen(f"{api}/api/settings/claude-profiles", timeout=10) as r:
        data = json.load(r)
    for p in (data.get("profiles") or data.get("data", {}).get("profiles") or []):
        if p.get("name") == "Personal":
            pid = p.get("id")
            break
except Exception:
    pass

payload = {"name": "Personal", "oauthToken": tok, "isDefault": True}
if pid:
    payload["id"] = pid

req = urllib.request.Request(
    f"{api}/api/settings/claude-profiles",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        out = json.load(r)
except urllib.error.HTTPError as e:
    raise SystemExit(f"HTTP {e.code}: {e.read().decode()[:200]}")

# redact the token before printing
d = out.get("data", out)
if isinstance(d, dict) and "oauthToken" in d:
    d["oauthToken"] = "sess-<redacted>"
print("saved profile:", json.dumps(out)[:200])

# Confirm PFactory now reports the token configured.
with urllib.request.urlopen(f"{api}/api/settings/auth-status", timeout=10) as r:
    a = json.load(r)
print("auth-status -> hasToken:", a.get("hasToken"), "| source:", a.get("source"))
PY
echo "Done. Click 'Refresh' in the portal's Claude Code popover."
