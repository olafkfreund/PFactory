---
status: draft
issue: 801
---

# Intent: the language signal table resolves by list order, so shared and English-word tokens win wrongly

## Problem

`detect_spec_language_signal` (`apps/backend/plan/recon/language_reconcile.py:91`)
returns on the **first** entry of `_LANGUAGE_SIGNALS` whose token appears in the
spec text. `gradle` is one of java's tokens, java sits at index 5, kotlin at
index 9 — so any Kotlin spec that mentions Gradle resolves to java, every time.
Gradle is Kotlin's standard build tool, so that is most of them.

`reconcile_language` then reports a conflict, and the `language-reconciled`
readiness check fails on it.

## Measured, end to end

Driving `reconcile_language` directly (the decision function the gate reads):

    CONFLICT   Kotlin Android app, Gradle build, kotlin repo
               spec='java' signal='gradle' repo='kotlin'
    CONFLICT   "Users can go to the next screen and confirm.", python repo
               spec='go' signal='go' repo='python'
    CONFLICT   "The system must give a swift response under load.", python repo
               spec='swift' signal='swift' repo='python'
    ok         control: Java Spring Boot + Maven, java repo

**The reported Gradle case is the mild one.** `go` and `swift` are ordinary
English, and `go` sits at index 1, so it beats nearly every other signal: *any*
brief containing the word "go" in a non-Go repo produces a language conflict.
"Approvals go through a review queue" is enough. This is the same class as #397,
where a bare `rust` matched inside "untrusted" — word boundaries fixed matching
*inside* words but not tokens that are whole words with an everyday meaning.

So there are two distinct defects behind one symptom:

1. **Shared tokens.** `gradle` legitimately belongs to java, kotlin, scala and
   groovy. Order alone decides, and the comment above the table asserts the
   opposite ("boundaries also make the ordering non-load-bearing") — true for
   substring collisions, false here.
2. **English-word tokens.** `go` and `swift` (and arguably `ts`, `js`, `rs`, `uv`)
   fire on prose that says nothing about a language. No ordering fixes this.

## Correction to the issue

#801 says "the check is `hard=False` and waivable, so it degrades a plan rather
than stopping it". Measured: `checks.py:164` sets **`hard=True`, waivable=True** on
the failing branch. So a false detection is a hard gate failure that must be
waived, not a soft degradation — and the author is told their Kotlin spec is a
Java spec while they do it. That makes this more severe than filed, not less.

## Desired outcome

A spec that says "Kotlin" and "Gradle" resolves to kotlin. A brief whose only
"signal" is the English word "go" or "swift" resolves to no language at all,
rather than to a conflict against the repo. Genuine signals keep working: a Java +
Maven spec is still java, and #397's "untrusted" must still not read as rust.

## Affected

- `apps/backend/plan/recon/language_reconcile.py` — the table and/or the
  resolution order in `detect_spec_language_signal`.
- `plan/review/readiness/checks.py` — no change expected; it consumes the decision.
- Existing tests over this module, including whatever pins #397's behaviour.
- The fleet now routes Kotlin (gradle) and Java (maven) to separate test lanes on
  exactly this value, so a wrong answer sends work to the wrong lane.

## Constraints

- **#585's contract holds:** when a spec asks for Y and the repo is X and it is not
  a migration, the spec must win or the run HALTs — never silently resolve to the
  repo's language. Any tie-break that consults the repo language must not become a
  back door that quietly resolves conflicts.
- **#397 must not regress:** the fix cannot reintroduce matching inside words.
- Resolving *less* is acceptable where the evidence is genuinely absent
  (`None` means "unstated", which the reconciler already handles); resolving
  *wrongly* is not.

## Open questions

1. **Which design?** #801 offers three (drop `gradle` from java; give it to both and
   tie-break; score all signals and take the strongest). None addresses the
   `go`/`swift` class. My recommendation is a two-part fix:
   - split the table into **language names** and **tool/ecosystem tokens**, and
     consult names before tools, so "Kotlin … Gradle" resolves on the name;
   - require the English-word tokens (`go`, `swift`, and the two-letter `ts`,
     `js`, `rs`, `uv`) to be **corroborated** — either an unambiguous form
     (`golang`, `go.mod`, `goroutine`, `swiftui`) or a second token for the same
     language — so bare prose stops resolving.
   That fixes both defects and removes the ordering dependence the file's comment
   already believes is gone.
2. **Tie-break on the repo language, or refuse to guess?** If a spec says only
   "Gradle" and nothing else, java/kotlin/scala are equally plausible. Preferring
   the repo's language is convenient but edges toward the #585 back door; returning
   `None` (unstated) is the conservative reading. I lean to `None` — it is honest
   and the reconciler already has a path for it.
3. **Do `scala` and `groovy` need adding?** They are absent from the table, so a
   Gradle-built Scala spec currently reads as java. Out of scope for #801 unless
   you want it, but worth naming rather than leaving as a silent gap.
