---
status: draft
issue: 662
spec: spec/2026-09-18-662-triager-committed-count.md
---

# Plan: Triager reports tests as committed when the git write failed

Approved decisions (from the spec; port of TFactory#1261):

- `TriageReport.delivery_error: str | None = None`; `accepted_count =
  len(committed)`; `committed_count = 0 if delivery_error else len(committed)`.
- `build_report(..., delivery_error=None)`; JSON summary gains
  `accepted_count`, top level gains `delivery_error`; markdown gains
  `| Accepted | N |` above `| Committed (accept) | N |`, and on delivery error
  the "Delivery FAILED — nothing was committed" blockquote plus the section
  title "Accepted but NOT committed (delivery failed)".
- `triager._delivery_error(summary)`: error string (≤300 chars, default
  `"git write failed"`) only for a dict with `ok is False`; else `None`.
- Git write moves before report build/render; PR comment stays after.
- `status.json`: `accepted_count` + `committed_count` (0 on delivery error);
  empty path writes `accepted_count=0`. `status` / `final_status` unchanged.
- Keys kept, none renamed. Golden fixture gains the Accepted row.

## Steps

1. `apps/backend/agents/triage_report.py`: field, `accepted_count` property,
   `committed_count` change, `build_report` parameter, JSON + markdown changes.
   → verify by step 4 report tests.
2. `tests/fixtures/triage_report/expected.md`: add `| Accepted | 1 |` above the
   Committed row. → verify golden test green.
3. `apps/backend/agents/triager.py`:
   a. add `_delivery_error` next to the other module helpers;
   b. move the step-5 git-write block above step 4, renumber comments
      (4 = commit, 5 = build + render), compute
      `delivery_error = _delivery_error(git_result_summary)` and pass it to
      `build_report`;
   c. step 7: `accepted_count = len(committed)`,
      `committed_count = 0 if delivery_error else accepted_count`, write both;
   d. empty-candidates path: add `accepted_count=0`.
   → verify by step 5 tests.
4. `tests/test_triage_report.py`: delivery-error report → counts, JSON keys,
   markdown lines/headings, no `## Committed`; no-error report unchanged apart
   from the Accepted row.
5. `tests/test_triager.py`:
   - failed write: seed as the existing tests do (source.json has a branch),
     set `PFACTORY_TRIAGER_GIT_WRITE=1`, monkeypatch
     `tools.git_writer.write_tests_to_branch` to return
     `GitWriteResult(ok=False, dry_run=False, error="checkout 'x' failed: already used by worktree", ...)`;
     assert `status.json` `committed_count == 0`, `accepted_count > 0`,
     `status == "triaged"`, `git_writer.ok is False`, and the report shows the
     delivery failure;
   - default dry run: `committed_count == accepted_count > 0`;
   - `_delivery_error` cases: dry-run summary, skipped summary, non-dict,
     failed with and without an error string.
6. Negative control (not committed): `_delivery_error` returns `None` → the
   failed-write test fails; restore.

## Tests

    apps/backend/.venv/bin/pytest tests/test_triager.py tests/test_triager_integration.py \
      tests/test_triager_completion_webhook.py tests/test_triage_report.py tests/test_flaky_history.py \
      tests/test_handback_send.py tests/test_pfactory_routes_tasks.py tests/test_git_writer.py -q

Expected: all pass. Full backend suite runs in the pre-commit hook.

## Rollback

Revert the commit. `status.json` files written by the new code carry an extra
`accepted_count` key the old code ignores; no cleanup needed.
