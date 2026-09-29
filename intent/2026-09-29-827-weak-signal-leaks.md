---
status: draft
issue: 827
---

# Intent: language detection still HALTs valid plans on plain English

## Problem

#801 (PR #822) replaced first-match-wins with three tiers and fixed the reported
Kotlin+Gradle defect. It **narrowed** the false-detection class; it did not close it.
An independent review found three residual leaks, all verified by running them:

| Prose | Detected | `conflict=True`? |
| --- | --- | --- |
| "The team responded in swift succession." | `swift` | **yes** |
| "The enclosure is tested in uv light for 500 hours." | `python` | no |
| "Sales dropped in go-to-market velocity." | `go` | **yes** |
| "A swift and reliable api for partners." | `swift` | **yes** |
| "We go live with backend changes on Friday." | `go` | **yes** |
| "The handler must go and fetch application state." | `go` | no |
| "Sterilise the flask before each run." | `python` | no |
| "Track cargo across the fleet." | `rust` | **yes** |
| "Django Reinhardt playlist feature." | `python` | no |

`language-reconciled` is `hard=True, waivable=True`, so each `conflict=True` is a hard
gate failure someone must waive on a plan that never mentioned a language.

Three causes:

1. **`_CTX_BEFORE` has no trailing requirement.** The tier-2 prefix branch is
   `{_CTX_BEFORE}{token}\b` — nothing need follow. `_CTX_BEFORE` includes bare `in`,
   `using`, `with`, so any "in <weak word>" resolves. `written in` / `ported to` carry
   evidence; `in` alone does not.
2. **`_GAP` is a denylist.** It excludes ten function words and permits any two other
   `[\w.+#-]+` tokens before the context noun, so `and`, `live`, `reliable` pass —
   hence "a swift **and reliable** api".
3. **Tier 3 has no context requirement at all**, and holds ordinary English nouns
   (`flask`, `cargo`, `django`). #801's framing put the weak words in tier 2; they are
   not all there.

**Measured, and it bounds the urgency honestly:** old-vs-new on all nine — zero
regressions, every one leaked identically before #801. This is long-standing
behaviour, not a new break. It is also not evidence that the shapes are rare.

## Measured: is case a usable signal?

#827's filed direction was "use case from the original text", since language names are
proper nouns ("Swift", "Go") while the leaking words are lowercase English. The review
warned this trades a false-positive class for a **false-negative** one, because briefs
arrive lowercased. I probed every string fixture in `tests/` for the six weak tokens in
original casing:

    token   Cased   lower-only
    go         11           14
    swift      11            7
    ts          4           68
    js          0           13
    rs          0            3
    uv          0            3

The raw counts look bad for case — until you read what the lower-only hits are. They
are overwhelmingly **not** language claims: `ts` is SQL columns and timestamps
(`VALUES (:id, 'seed', 'test', :ts)`), `rs` is file paths (`rust/pay/src/a.rs`), `js`
is every occurrence inside `Node.js`, `uv` is tooling output (`uv pip install`). Those
are exactly what the weak tier should refuse. Where the token really does mean the
language, it is cased: "A native iOS and Android app (**Swift** / Kotlin)".

That suggests a simpler fix than a case rule over six tokens.

## Desired outcome

A brief that says nothing about a language resolves to `None` and does not trip the
gate — including the nine cases above. Genuine evidence still resolves: "written in
Go", "A Swift SPM library", "Kotlin … Gradle", and the existing suite's assertions.
No new false-negative class: a lowercased brief that genuinely names a language must
still resolve.

## Affected

- `apps/backend/plan/recon/language_reconcile.py` — `_CTX_BEFORE`, `_GAP`, and
  whichever tier the English-word tool tokens end up in.
- `tests/test_recon_change_mode.py` — the 49-row table needs the case classes it does
  not cover (see below).
- The derived `_LANGUAGE_SIGNALS` union feeds `plan/detect/migration_classifier.py`'s
  `_CANON`; moving a token between tiers changes that union, so its behaviour must be
  checked, not assumed. #801's deviation 7 records that I got this wrong once already.

## Constraints

- #585's contract: a real conflict must HALT, never resolve quietly to the repo's
  language. Reducing false positives must not become "resolve to the repo when unsure".
- #397 must not regress: no matching inside words.
- The gate is hard. **A false negative is the worse error here** — failing to catch a
  genuine language mismatch is what the gate exists for — so the fix must not buy
  quiet by refusing to resolve real evidence.
- #801's 49 existing rows must keep passing; they encode the behaviour just shipped.

## Open questions

1. **Drop `ts`, `js`, `rs`, `uv` from the weak tier entirely?** My recommendation.
   The probe says their bare forms are almost never language evidence in practice, and
   each is already covered by an unambiguous tier-1 name (`typescript`, `node.js`,
   `rust`, `python`). That leaves only `go` and `swift` in the weak tier — the two
   tokens that are genuinely both a language name and an English word — so whatever
   rule we choose has to work for two cases, not six.
2. **Then how to gate `go` / `swift`: an allowlist gap, or case as confidence?** With
   only two tokens, requiring a capitalised occurrence in the original text becomes
   attractive, and the review's shape applies — case raises confidence for the weak
   tier only, never for tier-1 names. But "go" starts sentences ("Go to settings"), so
   case alone is not sufficient; it likely needs to combine with the context phrase
   rather than replace it. I would measure both before choosing.
3. **Tier 3: give it a context requirement, or move its English words into tier 2?**
   `flask`, `cargo`, `django`, `maven` are the offenders. Moving them into the weak
   tier reuses one rule; giving tier 3 its own rule duplicates it. I lean to moving
   them, but that changes the `_LANGUAGE_SIGNALS` union and so `_CANON` — needs the
   check named under Affected.
4. **Is `hard=True` right for this gate at all?** Out of scope for this issue and not
   mine to decide, but worth asking once: the failure mode we keep fixing is a
   *blocking* verdict derived from prose heuristics. A non-blocking finding with the
   same evidence would make every leak a nuisance rather than a stoppage. Say if you
   want that explored separately.
