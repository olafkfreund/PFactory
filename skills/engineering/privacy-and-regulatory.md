# privacy-and-regulatory

> Source: https://github.com/olafkfreund/PFactory | v0.1.0 | License: MIT | Tags: privacy, gdpr, compliance, data-protection, mobile, retention, age-assurance

---

# Privacy and Regulatory Implementation

Use this skill when implementing a feature that touches personal data: profiles, accounts, photos, location, matching or recommendation, user-to-user contact, or anything distributed through the App Store or Play Store. Triggers on retention, deletion, erasure, age assurance, minors, consent, location permissions, profiling transparency, blocking and reporting, account deletion, and any task whose contract carries a `compliance` block. This is the build-side companion to PFactory's compliance review lens: the lens raises the obligation at plan time, this skill describes what a correct implementation looks like.

---

This skill surfaces obligations and cites sources. It is not legal advice and
makes no compliance determination; qualified counsel assesses actual
obligations. What it does do is stop the common failure mode: an obligation
arrives on the contract, and the build satisfies it in name only.

## Where obligations come from

A signed Task Contract may carry a `compliance` block: `obligations[]`, each
with `severity`, `blocking`, and `citations` (a source with a resolvable URI —
for a customer with a `.factory/constitution.md`, the first citation is their
own clause). Treat every `blocking: true` obligation as an acceptance
criterion: it needs an implementation AND evidence a verifier can check, not a
sentence in the README. The block's `jurisdictions` list names the target
markets; obligations differ by market, so never assume one market's rule
covers another.

## Reading a brief for data classes

Five signals, each implying obligations whether or not the brief mentions them:

| Signal in the brief | Data class | Obligations it implies |
|---|---|---|
| profile, account, photo, sign-up | personal data | lawful basis, retention, erasure, age assurance |
| location, "nearby", geo | sensitive location | consent, precision minimisation, background disclosure |
| matching, recommendation | profiling | transparency, automated-decision limits |
| chat, DM, comments | user-to-user contact | blocking, reporting, moderation |
| App Store / Play Store | store distribution | in-app account deletion, store review policies |

## What a correct implementation looks like, per obligation

### Retention needs a period and an enforcer

A retention policy is a stated period per data class PLUS something that
enforces it: a TTL index, a scheduled purge job, a partition drop — code that
runs, not a paragraph. "Indefinitely" is an acceptable answer if it is written
down and justified; an unstated period is a defect. Erasure (the user's right,
GDPR Art. 17, https://gdpr-info.eu/art-17-gdpr/) must cascade: the profile
row, the photos in object storage, the search index entries, the analytics
copies, the backups' expiry story. Test it by creating a user, exercising
every feature, deleting, and proving nothing identifying remains queryable.

### Age assurance is not a birth-date field

A date-of-birth input that trusts its own answer verifies nothing. Age
assurance means a gate proportionate to risk (self-declaration with friction
at minimum; verification where the risk demands it) AND a stated difference
in behaviour for minors: private-by-default profiles, no discoverability to
adults, tighter data minimisation, no profiling for targeting. Sources: COPPA
(https://www.ftc.gov/legal-library/browse/rules/childrens-online-privacy-protection-rule-coppa)
for under-13s in the US; the UK Age Appropriate Design Code
(https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/childrens-information/childrens-code-guidance-and-resources/)
for services likely accessed by under-18s; GDPR Art. 8
(https://gdpr-info.eu/art-8-gdpr/) for consent age thresholds in the EU.

### In-app deletion is a path, not a support address

Both stores require it where accounts can be created: Apple App Review
Guideline 5.1.1(v)
(https://developer.apple.com/app-store/review/guidelines/#5.1.1) and Google
Play's account deletion policy
(https://support.google.com/googleplay/android-developer/answer/13327111).
Correct: a flow inside the app that the user completes alone, ending in the
same cascade as an erasure request. Incorrect: a mailto link, a web form the
app never links to, or deletion that only deactivates. Build it in the same
phase as account creation — retrofitting deletion against a schema that never
planned for it is where cascades get missed.

### Location minimisation is a precision choice, not a consent checkbox

Request the coarsest precision the feature works with (city-level for
"people in your area"; precise only if the product genuinely ranks by
metres), request it in context when the feature is first used, and default
to foreground-only. Background collection needs a written reason in the plan
and a disclosure the user actually saw. Sources: GDPR Art. 6
(https://gdpr-info.eu/art-6-gdpr/), Apple Guideline 5.1.1, Google Play
location permissions policy
(https://support.google.com/googleplay/android-developer/answer/9799150).
Store review rejects background-location requests without visible
justification; do not ship one hoping otherwise.

### Profiling transparency: say what the matcher uses

If matching or recommendation is automated, tell users what signals feed it
(GDPR Art. 13(2)(f), https://gdpr-info.eu/art-13-gdpr/) in the privacy
notice and ideally at the surface itself. If a solely automated decision has
significant effects on a person, Art. 22
(https://gdpr-info.eu/art-22-gdpr/) applies: provide human review or ensure
a human is meaningfully in the loop. A "why am I seeing this" affordance is
the implementation shape that satisfies both.

### Trust and safety ships with the contact surface

Any user-to-user surface needs, in the same phase: block (immediate, mutual
invisibility), report (categorised, queued to a review path with a stated
response time), and — for EU markets — notice-and-action for illegal content
(DSA Art. 16) and an internal complaint-handling path (DSA Art. 20), both at
https://eur-lex.europa.eu/eli/reg/2022/2065/oj. A chat feature without
blocking is an incomplete feature, not a v1.

### Lawful basis: record it per data class

For each data class, record which basis applies (consent, contract,
legitimate interest — GDPR Art. 6) and the specific purpose (Art. 5,
https://gdpr-info.eu/art-5-gdpr/). The implementation consequence is purpose
limitation: data collected for matching does not silently feed advertising.
Where consent is the basis, it must be withdrawable as easily as it was given.

## Evidence, or it did not happen

For each obligation, leave something a verifier can run: the purge job's
test, the erasure cascade test, the minor-account behavioural test, a
screenshot path for the in-context permission prompt. An obligation
satisfied without evidence reads downstream as unsatisfied — the same rule
the rest of the factory applies to builds and tests.
