# /pfactory-add-test

Run the **pfactory-add-test** skill to add ONE generated test to the current
project without firing the full PFactory pipeline. The skill collects the
target symbol (`<path>::<symbol>`), the acceptance criterion it covers,
detects the language from the file extension, picks the right framework
(pytest for Python, Jest or Playwright for TypeScript), builds a minimal
v0.2 `Subtask`, drops it into a scratch spec dir, and runs
`run_gen_functional` against it. Output is a single test file on disk —
no Executor, no Evaluator, no Triager.

Requires `.pfactory.yml` at the repo root (run `/pfactory-init` first if
absent).

See `.claude/skills/pfactory-add-test/SKILL.md` for the full procedure.
