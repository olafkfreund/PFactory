#!/usr/bin/env python3
"""Keep PFactory authenticated from the live Claude *subscription* login.

Reads the OAuth access token your Claude Code keeps refreshed in
``~/.claude/.credentials.json`` (``claudeAiOauth.accessToken``) and upserts it
into PFactory's "Personal" Claude profile via the web-server API. Run it on a
timer (see scripts/sync-claude-auth-loop.sh) so PFactory stays authenticated as
long as you use Claude Code — no API key, no setup-token, no browser.

    python3 scripts/sync-claude-auth.py            # one sync
    PFACTORY_API=http://127.0.0.1:3198 python3 ...  # override target

Never prints the token.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = os.environ.get("PFACTORY_API", "http://127.0.0.1:3198")
CREDS = Path.home() / ".claude" / ".credentials.json"


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.load(r)


def main() -> int:
    if not CREDS.exists():
        print(f"no Claude credentials at {CREDS}; is Claude Code logged in?", file=sys.stderr)
        return 1
    token = json.loads(CREDS.read_text()).get("claudeAiOauth", {}).get("accessToken", "")
    if not token:
        print("no claudeAiOauth.accessToken found", file=sys.stderr)
        return 1

    # Find an existing "Personal" profile id so we update in place.
    pid = None
    try:
        data = _get(f"{API}/api/settings/claude-profiles")
        profiles = data.get("profiles") or data.get("data", {}).get("profiles") or []
        pid = next((p.get("id") for p in profiles if p.get("name") == "Personal"), None)
    except Exception:
        pass

    payload = {"name": "Personal", "oauthToken": token, "isDefault": True}
    if pid:
        payload["id"] = pid
    req = urllib.request.Request(
        f"{API}/api/settings/claude-profiles",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except urllib.error.HTTPError as e:
        print(f"sync failed: HTTP {e.code}: {e.read().decode()[:160]}", file=sys.stderr)
        return 1

    status = _get(f"{API}/api/settings/auth-status")
    print(f"synced Claude subscription token -> hasToken={status.get('hasToken')} "
          f"source={status.get('source')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
