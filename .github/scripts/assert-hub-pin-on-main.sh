#!/usr/bin/env bash
# Assert a hub pin names a commit that is really on olafkfreund/Factory main.
#
# Every hub-pinned gate in this repo resolves its pin from the PULL REQUEST's
# own tree -- `standards/.hub-sha`, `apps/web-server/factory_common/.hub-sha`, or
# a `HUB_PIN_SHA:`/`HUB_SHA:` in the workflow file itself, which for a plain
# `pull_request` trigger is the PR's copy of the workflow -- and then hands that
# value to actions/checkout, codeload or raw.githubusercontent as a ref against
# olafkfreund/Factory. On a fork PR every one of those inputs is
# attacker-controlled:
#
#   ci.yml                          HUB_SHA                  -> raw.githubusercontent
#                                                               (via scripts/check_schema_drift.py --ref)
#   cq-ratchet.yml                  standards/.hub-sha       -> raw.githubusercontent
#   factory-common-drift.yml        factory_common/.hub-sha  -> actions/checkout
#   factory-github-drift.yml        HUB_SHA                  -> codeload tarball
#   factory-ui-drift.yml            HUB_PIN_SHA              -> actions/checkout
#   security-lint.yml               HUB_PIN_SHA              -> raw.githubusercontent
#   verification-core-drift.yml     HUB_PIN_SHA              -> actions/checkout
#
# Factory#711 already rejects anything that is not a bare 40-character commit
# id, which closes ref confusion and argument injection. It does not close the
# BYPASS, because GitHub keeps every fork of a repository in one shared object
# store: a commit that exists only in a fork of Factory is a perfectly valid
# 40-hex SHA and is still fetchable by SHA from Factory itself. So a fork PR
# could commit a hub tree that matches its own drifted vendored copy, point the
# pin at that fork commit, and the drift gate would diff two matching trees and
# report green -- defeating the only thing these gates exist to catch
# (Factory#806, the same defect as AIFactory#1281 / CFactory#373).
#
# This is a gate bypass, not code execution: the fetched hub tree is only ever
# diffed or read, the jobs run on ubuntu-latest under a plain `pull_request`
# trigger with read-only permissions, no secrets, and no cache or artifact that
# a privileged workflow later consumes.
#
# Mechanism: the compare API, not a local `git merge-base --is-ancestor`.
# merge-base can only answer for objects already in the local repo, and the
# gate's own checkout is exactly what an attacker controls -- proving the pin is
# an ancestor of a branch we fetched by the attacker's own instruction proves
# nothing. `repos/olafkfreund/Factory/compare/main...<sha>` is answered by the
# hub server about the hub's own main, so the PR cannot influence the answer.
# Reachability from main is the right bar (not "any branch"): the hub
# squash-merges, so main IS its reviewed history, and every re-vendor is taken
# from main.
#
# `identical` and `behind` mean the pin is on main (behind = an older main
# commit, which is the normal case for a pin). `ahead` and `diverged` mean it
# carries commits main does not have. A 404 means the compare has no common
# ancestor at all, or the SHA is not in the network.
#
# Measured 2026-08-18 against the live hub, both directions, before wiring:
#
#   565afeaf...  ci.yml HUB_SHA                        -> behind    exit 0
#   66cc93ac...  cq-ratchet standards/.hub-sha         -> behind    exit 0
#   9da6bff8...  factory-common-drift .hub-sha         -> behind    exit 0
#   4fcdd3d4...  factory-github-drift HUB_SHA          -> behind    exit 0
#   43cc78ab...  factory-ui-drift HUB_PIN_SHA          -> behind    exit 0
#   5a2dc479...  security-lint HUB_PIN_SHA             -> behind    exit 0
#   11834afc...  verification-core-drift HUB_PIN_SHA   -> behind    exit 0
#   a00a010a...  a real olafkfreund/Factory commit that is NOT on main.
#                actions/checkout resolves it fine today, so it is a strictly
#                harder negative than a fabricated SHA. -> diverged  exit 1
#   deadbeef...  fabricated 40-hex                      -> 404       exit 1
#
# What this does NOT close: a `pull_request` run executes the PR's own copy of
# the workflow, so a fork PR can also just delete this step. That is a separate,
# broader hole in the fork-PR gate model (a fork can neuter any gate defined in
# its own tree) and it is not fixable from inside a workflow file. The durable
# answer is the checker-from-hub-main pattern several of these gates already use,
# extended to the pin itself. Closing the pin bypass is still worth it: it is
# the hole a re-vendor PR from a trusted contributor could hit by accident, and
# the one the gate's own logic can see.
#
# Usage: assert-hub-pin-on-main.sh <40-hex-sha> [what-named-it]
set -euo pipefail

sha="${1:?usage: assert-hub-pin-on-main.sh <40-hex-sha> [source]}"
source_desc="${2:-hub pin}"
hub="olafkfreund/Factory"

if ! printf '%s' "${sha}" | grep -qE '^[0-9a-f]{40}$'; then
  echo "::error::${source_desc}: '${sha}' is not a 40-character commit SHA"
  exit 1
fi

# No `|| true`: an API error must fail the gate, not skip the check. Per
# standards rule 4.7 a gate that cannot run must fail rather than pass.
if ! status="$(gh api "repos/${hub}/compare/main...${sha}" --jq .status 2>&1)"; then
  echo "::error::${source_desc}: ${hub} cannot compare main...${sha} -- the pin is"
  echo "not reachable from hub main (a fork-network or garbage-collected commit"
  echo "gives exactly this). API said: ${status}"
  exit 1
fi

case "${status}" in
  identical | behind)
    echo "${source_desc}: ${sha} is on ${hub} main (${status})"
    ;;
  *)
    echo "::error::${source_desc}: ${sha} is NOT hub history -- compare main...${sha}"
    echo "reports '${status}', meaning the commit carries history main does not"
    echo "have. A pin must name a commit on ${hub} main; a commit that merely"
    echo "exists somewhere in the fork network does not count (Factory#806)."
    exit 1
    ;;
esac
