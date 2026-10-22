---
status: approved
issue: 827
intent: intent/2026-09-29-827-weak-signal-leaks.md
---

# Spec: gate the weak language tokens on evidence, not adjacency

## Design

One rule replaces `_CTX_BEFORE`/`_CTX_AFTER`/`_GAP`. A weak token `T` — one that is
both a language name and an ordinary English word — resolves only under **A**, **B**
or **C**:

- **A — a strong prefix.** `written in`, `rewritten in`, `rewrite in`, `ported to`,
  `migrated to`, `migrates to`, `implemented in`. These carry evidence by themselves,
  at any casing: "Ported to TS" resolves.
- **B — a bare prefix (`in`, `using`, `with`) *and* `T` capitalised in the original
  text.** Case raises confidence rather than gating the tier, which is the review's
  caveat on #827: "write it in Go" resolves, "responded in swift succession" does not.
- **C — `T` followed by a language noun, with `T` itself proper-noun-shaped.** Any
  intervening qualifier must carry an uppercase letter or a digit ("Swift **SPM**
  library", "Go **HTTP** service", "Go **1.22** module"), and for the English-word
  tokens `T` must be Capitalised **and not ALL-CAPS** — so "A **Swift** SPM library"
  resolves while "A **SWIFT** MT103 service" (the banking network) and "a swift KYC
  api" do not.

Nouns: `service · module · package · binary · app · application · code · codebase ·
version · program · library · sdk · backend · api · microservice · project`.

Two structural changes follow from it:

- **The text is no longer lowercased before matching.** Rules B and C read original
  casing; every pattern that does not care is compiled `re.I`. Tier-1 names never
  require case.
- **The English-word tool tokens move into the weak tier**: `cargo` → rust,
  `flask`/`django` → python, `maven` → java. One rule then covers them, which is what
  the issue's cause 3 asks for. `_TOOL_SIGNALS` keeps only the unambiguous ones
  (`tokio`, `actix`, `pytest`, `fastapi`, `jetpack compose`, `cmake`).

The derived `_LANGUAGE_SIGNALS` union is unaffected in **content** — those tokens move
between tiers, not out of the union — so `migration_classifier`'s `_CANON` still maps
each to the same language. Per-language token *order* shifts, which matters only to
`setdefault` on a duplicate, and there are none. Verified as part of the plan, not
assumed: #801's deviation 7 is exactly this mistake made once already.

## Measured

Four sets, all run against the candidate:

| Set | Result |
| --- | --- |
| The 49 rows shipped by #801 | **49/49** — nothing regresses |
| The 9 leaks #827 reports | **9/9 now resolve to `None`** |
| 7 new positives/negatives (Django, Flask, Cargo, Maven, "Go to settings") | **7/7** |
| 13 adversarial cases I wrote to break my own design | **12/13** |

The one failure is recorded below rather than smoothed over.

## Residual, known and not fixed here

`"In Swift succession the jobs retried."` still resolves to `swift` via rule B: a
capitalised English word directly after a bare prefix. I tried to close it by requiring
the token to be followed by end-of-clause or a function word, and it costs a genuine
positive — `"Using Go conventions for naming."` stops resolving. On a `hard=True` gate a
false negative is the worse error (failing to catch a real mismatch is what the gate is
for), so the false positive stays.

It is **not** given a test row: a test asserting the wrong answer enshrines it and
reads as coverage. It is documented here, in the plan, and on #827.

Three more from my attack set that the tightening *did* close, listed because they are
realistic for this product's domain (payments briefs): `"SWIFT payment api …"`,
`"A SWIFT MT103 service."`, `"A swift KYC api for onboarding."`.

## Alternatives rejected

- **Extend the `_GAP` denylist.** What #822 does now. A denylist cannot enumerate
  English; `and`/`live`/`reliable` were three of an open set.
- **Require case for the whole weak tier.** The review's warning, confirmed by
  measurement: `uv`, `rs` and `js` appear lowercase in real text, so a blanket case
  requirement trades a false-positive class for a false-negative one on a hard gate.
- **Drop `ts`/`js`/`rs`/`uv` from the weak tier** (the intent's first recommendation).
  Measurement made it unnecessary: rules A–C already reject the noise those tokens
  produce (`:ts` in SQL, `a.rs` in paths, `Node.js`, `uv pip install`) while keeping
  "Ported to TS". Dropping them would also have failed a row #801 shipped.
- **Give tier 3 its own context rule.** Duplicates rules A–C for four tokens; moving
  them into the weak tier reuses one rule.
- **POS-tag the prose.** The only thing that cleanly separates "in Swift succession"
  from "in Go with a store", and far beyond this issue's weight.

## Risks

- **`a go service` in lowercase no longer resolves.** Rule C requires the token
  proper-noun-shaped for English words. A sloppy all-lowercase brief that names Go
  only that way now grounds on the repo language instead. Accepted deliberately: on
  this gate, not resolving is safe (it produces no conflict) while resolving wrongly
  HALTs a valid plan.
- Matching on original casing means every pattern that should ignore case needs `re.I`
  explicitly. A missed flag is a silent false negative, so the table covers each tier's
  tokens in both casings.
- `flask`/`cargo`/`django`/`maven` moving tiers changes `_LANGUAGE_SIGNALS`' per-language
  token order. Content is identical; the plan verifies `_CANON` rather than assuming.

## Verification

- **Before:** the nine cases resolve to a language, five with `conflict=True`. Measured;
  re-run as the before half.
- **After:** all nine resolve to `None`, no conflict.
- **Regression:** all 49 rows from #801 pass unchanged.
- **New table rows:** the 9 leaks, the 7 new cases, and the 12 attack cases that pass —
  28 additions, each commented with the rule (A/B/C) that decides it.
- **Negative controls, one per rule:** (a) allow a bare prefix without the case check →
  the `in swift succession` / `in go-to-market` rows fail; (b) drop the uppercase-or-digit
  requirement from the qualifier → the `swift and reliable api` / `go live with backend`
  rows fail; (c) drop the not-ALL-CAPS requirement → the SWIFT rows fail; (d) leave the
  English-word tokens in `_TOOL_SIGNALS` → the `flask`/`cargo`/`django` rows fail. Each
  must fail only its own rows.
- **`_CANON` equivalence:** assert the derived union still maps every token to the same
  language as before the move.
- Full backend suite via the pre-commit hook.
