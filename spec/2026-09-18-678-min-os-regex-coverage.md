---
status: approved
issue: 678
intent: intent/2026-09-18-678-min-os-regex-coverage.md
---

# Spec: OS-floor phrasing is not recognised as covering min-os-versions

## Design

`apps/backend/plan/decompose/implicit_requirements.py`:

1. **A keyword may be a compiled regex.** The requirement tuples keep their
   shape `(key, text, keywords)`; `keywords` becomes
   `tuple[str | re.Pattern[str], ...]`. `missing_requirements` treats a
   `re.Pattern` as covered when `pattern.search(hay)` matches, a `str` as today
   (`kw in hay`). Only this matcher reads keywords (checked: the completeness
   lens and the readiness check call `missing_requirements`; the lens reads
   only `key` from the tuples), so no other code changes. The type alias for
   the list is updated in the three signatures that spell it out.

2. **`min-os-versions` gains two patterns** (the haystack is already
   lower-cased):

       _OS  = r"(?:ios|ipados|android)"
       _VER = r"\d+(?:\.\d+)*"
       _AFTER = r"(?:\s*\+|\s+(?:and|or)\s+(?:above|up|later|newer|higher))"

       rf"\b{_OS}\s*{_VER}{_AFTER}"                                   # "iOS 16 and above", "Android 10+"
       rf"\b(?:at\s+least|minimum(?:\s+of)?|min\.?)\s+{_OS}\s*{_VER}\b" # "at least iOS 16"

   Both require a platform, a version number, and a floor word. The leading
   `\b` keeps "radios"/"audios" out (#397/#673).

Prototype run against the phrasings (all as expected):

| covers | phrase |
| --- | --- |
| yes | We support iOS 16 and above and prompt users on older versions to update |
| yes | Android 10 or newer · iOS 15+ · requires at least iOS 16 · iOS 16.4 or later · Android 8.0 and up · minimum of Android 9 · iPadOS 17 or higher |
| no | The app supports iOS and Android. · tested on iOS 17 · iOS 17 simulator screenshots · Android devices and tablets · radios 5 and up · audios 3+ |

## Alternatives rejected

- **4th tuple field for patterns**: breaks every `for key, text, keywords in …`
  unpacking for no gain.
- **Substring "support ios"**: false-covers "supports iOS and Android".
- **Bare `ios\s*\d+` (the issue's sketch)**: false-covers "tested on iOS 17" —
  a version, not a floor.
- **Regex for every requirement**: no other requirement has a reported gap.

## Risks

- A floor phrased another way ("iOS ≥ 16", "iOS sixteen onwards") still
  injects a duplicate AC — the recoverable direction, same as today.

## Verification

- `test_min_os_and_forced_upgrade_overlap_phrasing` flips to
  `"min-os-versions" not in missing` (forced-upgrade assertion unchanged).
- `test_supports_ios_and_android_does_not_cover_min_os` stays green unchanged.
- New parametrised tests over both columns of the table above against
  `missing_requirements(epic, MOBILE_IMPLICIT_REQUIREMENTS)`.
- Negative control: drop the two patterns → the "yes" cases fail; remove the
  leading `\b` → "radios 5 and up" / "audios 3+" fail.
- `pytest tests/test_implicit_requirements.py tests/test_completeness_lens.py`
  (if present) and the readiness-check tests green.
