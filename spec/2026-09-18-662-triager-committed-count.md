---
status: approved
issue: 662
intent: intent/2026-09-18-662-triager-committed-count.md
---

# Spec: Triager reports tests as committed when the git write failed

Port of TFactory#1261, keeping its names and wording.

## Design

### `apps/backend/agents/triage_report.py`

- `TriageReport` gains `delivery_error: str | None = None` — the git write's
  error when accepted tests were **not** delivered; `None` when the write
  succeeded, was a declared dry run, or had nothing to write.
- New property `accepted_count` = `len(self.committed)` (what triage accepted).
- `committed_count` becomes `0 if self.delivery_error else len(self.committed)`
  (what landed).
- `build_report(..., delivery_error: str | None = None)` passes it through.
- `render_json`: `summary` gains `accepted_count`; top level gains
  `delivery_error`. `committed_count` stays.
- `render_markdown`:
  - summary table gains a row `| Accepted | N |` directly above
    `| Committed (accept) | N |` (TFactory's placement);
  - when `delivery_error` is set, a blockquote right after the header:
    `> **Delivery FAILED — nothing was committed.** N test(s) were accepted and
    none reached the branch: \`<error>\``;
  - the "Committed" section is titled
    "Accepted but NOT committed (delivery failed)" in that case, same body.
- `tests/fixtures/triage_report/expected.md` updated for the new row (the only
  change on the success path).

### `apps/backend/agents/triager.py`

- New `_delivery_error(git_writer: object) -> str | None` (TFactory's function,
  verbatim semantics): returns the error (truncated to 300 chars, default
  `"git write failed"`) only when the summary is a dict with `ok is False`;
  otherwise `None`. A dry run records `ok: true, dry_run: true`, and a skipped
  write has no `ok` key, so neither counts as a failure.
- **Reorder**: step 5 (git write) moves before step 4 (build + render the
  report), so the report is written knowing the result. The PR-comment step
  (6) stays after the report, since it posts the report.
- `build_report(..., delivery_error=_delivery_error(git_result_summary))`.
- Step 7 writes `accepted_count=len(committed)` and
  `committed_count=0 if delivery_error else len(committed)`. The empty path
  (`triager_no_candidates`) also writes `accepted_count=0`.
- `final_status` logic unchanged: it keys on what triage decided
  (`committed or flagged`), which is still true after a failed push.

### Decided (intent Q1)

Terminal `status` is not changed on a delivery failure. The signal is
`committed_count: 0` + `accepted_count > 0` + `git_writer.ok: false` in
`status.json`, and the report's heading. No outcome field is added — PFactory's
fork has no outcome layer, and adding one is out of scope.

## Alternatives rejected

- **Rename `committed_count` to `accepted_count`**: every existing reader of
  `committed_count` would silently change meaning. Two keys, no rename.
- **Delete the fork**: live (`evaluator.py:455` schedules it).
- **`triager_failed` on delivery failure**: rejected in the intent — the
  handback trigger and `/pfactory-watch` would read a failed push as a triage
  crash.
- **Keep render-before-write and patch the files afterwards**: two writes of the
  same report, and a window where the wrong one is on disk.

## Risks

- Reordering moves the git write earlier. It still happens after dedup/rank
  (it needs `committed`/`flagged`) and before the catalog/harvest steps, which
  do not depend on its order. The report no longer exists on disk while the git
  write runs; nothing reads it in between.
- The golden fixture changes by one table row; anyone diffing report output
  across versions sees an added "Accepted" line.

## Verification

- `test_triage_report.py`: `delivery_error` set → `committed_count == 0`,
  `accepted_count == N`, JSON carries both + `delivery_error`, markdown has the
  "Delivery FAILED" line and the "Accepted but NOT committed" heading and no
  `## Committed` heading. Unset → today's output plus the Accepted row (golden
  fixture).
- `test_triager.py`: with `source.json` branch set and
  `tools.git_writer.write_tests_to_branch` monkeypatched to a failed result
  (the triager imports it inside `run_triager`, `triager.py:529`)
  (`ok=False`, error text): `status.json` has `committed_count: 0`,
  `accepted_count > 0`, `status: triaged`, and `triage_report.md` shows the
  delivery failure. Default dry run: `committed_count == accepted_count`.
- `_delivery_error` unit cases: dry-run summary, skipped summary, non-dict,
  failed summary.
- Negative control: make `_delivery_error` return `None` → the failed-write
  triager test goes red.
- Existing suites green: `pytest tests/test_triager*.py tests/test_triage_report.py
  tests/test_flaky_history.py tests/test_handback_send.py
  tests/test_pfactory_routes_tasks.py -q`, full suite via the hook.
