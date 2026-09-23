---
status: draft
issue: 749
intent: intent/2026-09-23-749-transport-marker-boundaries.md
---

# Spec: A vanished digest can be skipped as "registry unavailable"

## Design

`tests/docker/helpers.py`:

1. **Split the markers by kind.** The phrase markers (`i/o timeout`,
   `dial tcp`, `connection refused`, `too many requests`, …) stay plain
   substrings: none of them can occur inside a hex digest, since every one
   contains a space, a slash or a non-hex letter. Only the four HTTP status
   codes are digit-only and can collide, so they move to one compiled pattern:

       _TRANSPORT_CODE_RE = re.compile(r"\b(?:429|502|503|504)\b")

2. `_is_transport_error` returns True when any phrase marker is present **or**
   `_TRANSPORT_CODE_RE` searches the stderr. Everything else is unchanged:
   same two exception types, same retry-once-with-backoff, same skip wording.

Why the boundary works: inside `sha256:503edd78…` the neighbours of `503` are
hex characters, which are word characters, so `\b` does not hold. In
`status: 503 Service Unavailable` or `toomanyrequests: 429` the neighbours are
a space or a colon, so it does.

Verified before writing this (both offline, against the merged helper):

| stderr | today | with the fix |
| --- | --- | --- |
| `…@sha256:503edd78…: not found` | transport → **skip** | refusal → **fail** |
| `…@sha256:c8fedd78…: not found` | refusal → fail | refusal → fail |
| `unexpected status from HEAD request: 503 Service Unavailable` | transport → skip | transport → skip |
| `toomanyrequests: 429` | transport → skip | transport → skip |
| `dial tcp 1.2.3.4:443: i/o timeout` | transport → skip | transport → skip |

3. `tests/docker/test_p0_multi_arch.py`: the comment naming
   `ManifestInspectFailed` says `ManifestInspectError`, the real class.

## Alternatives rejected

- **Strip the ref from stderr before matching** (`stderr.replace(ref, "")`):
  also works, but it is a second mechanism doing the same job as the boundary
  and it hides part of the registry's own message from the classifier. One
  mechanism, tested, is enough.
- **Drop the numeric markers entirely**: a registry that answers `503` with no
  other wording would then fail the gate on an outage — the flake #744 fixed.
- **Require an `http`/`status` prefix before the code**: depends on wording
  that varies by registry and client version; the boundary does not.
- **Parse the exit status / use an API client instead of `imagetools`**: far
  beyond a test-helper fix.

## Risks

- A registry whose outage message embeds a code with no boundary (e.g.
  `HTTP503`) would now fail instead of skip. That is the safe direction (a red
  run a re-run clears), and no observed message has that shape.

## Verification

- New parametrised refusal case in `tests/test_registry_inspect_helper.py`
  carrying a marker-bearing digest — asserts `ManifestInspectError`, exactly
  one call (no retry), no sleep.
- Existing transport cases (including `503 Service Unavailable` and
  `toomanyrequests: 429`) stay green, proving real codes still skip.
- Negative control: revert `_TRANSPORT_CODE_RE` to the bare substrings → the
  new refusal case fails (it skips instead).
- `pytest tests/test_registry_inspect_helper.py tests/docker -m "not docker"`
  green; full suite via the pre-commit hook.
