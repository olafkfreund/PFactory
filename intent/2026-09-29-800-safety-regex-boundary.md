---
status: draft
issue: 800
---

# Intent: four compliance-gate escapes fail on inflected wording and block valid plans

## Problem

`_SAFETY_OK_RE` (`apps/backend/plan/review/lenses/compliance.py:95`) is the
"this brief already covers trust and safety" escape for the P5.1 finding
*"User-to-user contact without trust and safety controls"*. That finding is
`blocking=True` under an enforceable constitution, so it refuses the plan at
`approve` and at live `emit`.

The regex does not match the ordinary phrasing. Measured, on the shipped
pattern:

    False  'Blocking and reporting are not enough'
    True   'blocking and report'
    False  'blocking and reporting'
    True   'a person can report abuse'
    True   'moderation queue'

So a brief that says "blocking and reporting" is told to add, as explicit
acceptance criteria, the thing it just described. In the `pfactory-friends-demo`
run this was only cleared by rewording the brief to say "moderation" — the plan
did not change, the wording did.

## The issue's diagnosis is right about the line, wrong about the branch

#800 says the fix is that `report(?:ing)?` "needs the same treatment the other
branches have". Measured: that branch is already fine —
`report(?:ing)?\s+(?:abuse|users?|content)` matches "reporting abuse",
"reporting users" and "reporting content".

The break is in the **first** alternative,
`block(?:ing)?\s+(?:and\s+report|users?)`, which ends in the literal `report`.
The group's closing `\b` then demands a non-word character straight after it, and
"reporting" continues with `ing` — so the branch that carefully handles the
`-ing` form of *block* cannot handle it for *report*. Fixing the second
alternative as the issue suggests would change nothing.

## Measured: this is a defect class, not one branch

I probed every `_*_OK_RE` in the file with the bare and inflected forms of each
branch's final stem. **Four of the seven are broken the same way**, each one the
escape for a finding that blocks:

| Regex | Phrase that should pass but does not |
| --- | --- |
| `_SAFETY_OK_RE` | `blocking and reporting`, `blocking and reports` |
| `_LAWFUL_BASIS_OK_RE` | `legitimate interests` |
| `_PROFILING_OK_RE` | `automated decisions` |
| `_AGE_OK_RE` | `age gates` |

`_LOCATION_OK_RE`, `_RETENTION_OK_RE` and `_ACCOUNT_DELETION_OK_RE` are clean,
and every negative probe still fails to match, so the gates are not simply loose.

`legitimate interests` is the striking one: that is the GDPR term of art, and it
is almost always written in the plural. A brief that names its lawful basis
correctly is told it has not stated one.

## Desired outcome

A brief that describes its safety controls, lawful basis, profiling transparency
or age assurance in the way people actually write those things does not trip a
hard gate. The gates keep firing for briefs that genuinely say nothing — these
must not become patterns that match everything.

## Affected

- `apps/backend/plan/review/lenses/compliance.py` — one token in each of four
  regexes (lines 84, 92, 95, 100).
- `tests/test_compliance_lens.py` — asserts the findings *fire*
  (`test_social_spec_raises_every_expected_finding`) but nothing pins any
  escape's branches, which is why all four survived.

## Constraints

- This is the **fourth** boundary defect in this file: the `#397` note above
  `_LANGUAGE_SIGNALS` records the same `\b` trap for "untrusted", and the comment
  above `_AGE_OK_RE` records it for `16+` — a comment on the very regex that is
  still broken for `age gates`. A note warning about the trap has not stopped the
  trap, so the per-branch test matters more here than the token fix does.
- Widening an escape weakens a hard gate. Every negative probe currently fails to
  match and must still fail afterwards, so the verification needs negatives per
  regex, not only positives.

## Open questions

1. **`report(?:ing)?` or `report\w*` in the first alternative?** I lean to
   `report\w*`: it also covers "reports"/"reported", and it matches how
   `moderat\w+` is already written two lines down, so the file stays internally
   consistent. `(?:ing)?` is the more conservative change.
2. **Scope: #800's branch alone, or all four?** Asked and now answered by the
   audit above — three more are broken, one of them (`legitimate interests`)
   arguably worse than the reported one. My recommendation is to fix all four
   here: they are the same one-token defect in one file, the test that pins them
   is the same test, and splitting it would leave known false blocks in place
   for no gain. Say if you would rather I fix only #800's and file the rest.
3. **Is a per-branch test table the right shape?** I would add one
   parametrised test over (regex, phrase, expected) covering every branch of all
   seven escapes in bare and inflected form, plus a negative per regex. That is
   what turns a recurring trap into a caught one, and it is the fourth time this
   file has hit it.
