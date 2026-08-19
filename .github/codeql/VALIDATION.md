# Validating the barrier packs

`custom-queries/PathInjectionSanitized.ql` re-emits `py/path-injection`,
`CommandInjectionSanitized.ql` re-emits `py/command-line-injection`, and
`{Full,Partial}SsrfSanitized.ql` re-emit `py/full-ssrf` and `py/partial-ssrf`,
each with this repo's barriers registered; `codeql-config.yml` excludes the four
stock rules in their favour.

**Never change a barrier list without re-measuring.** A pack that is not
measured fails silently in both directions: too narrow and it suppresses nothing
while looking installed, too broad and it hides real findings. The alert count
moves either way, so the count alone cannot tell you which happened.

This file follows AIFactory's `.github/codeql/VALIDATION.md`, which has the
fuller method write-up. What is recorded here is this repo's measurements and
one trap AIFactory's copy does not yet cover.

## Measure with the bundle production runs, not a pinned favourite

**This is the first step, not a detail.** Check what PFactory's own code
scanning uses:

```bash
gh api repos/olafkfreund/PFactory/code-scanning/analyses?per_page=1 \
  --jq '.[0].tool.version'      # 2.26.3 as of 2026-08-15
```

Then take that whole bundle:

```bash
curl -fsSL -o /tmp/cqb.tar.gz \
  https://github.com/github/codeql-action/releases/download/codeql-bundle-v2.26.3/codeql-bundle-linux64.tar.gz
mkdir -p /tmp/cqbundle && tar -xzf /tmp/cqb.tar.gz -C /tmp/cqbundle
/tmp/cqbundle/codeql/codeql version --format=terse
```

**Do not assemble a pack set by hand.** Mixing versions across pack families is
not a configuration that ships, and it produces numbers that look like findings.
Measured on the same tree and the same query:

| packs | stock | fork |
|---|---|---|
| `python-queries` 1.8.4 + `python-all` 7.1.2 (a real 2.25.6 bundle) | 32 | 30 |
| `python-queries` 1.8.4 + `python-all` 7.2.2 (**not a bundle**) | 32 | 2 |
| CodeQL 2.26.3 bundle whole (`python-all` 7.2.3) | 2 | 0 |

The middle row reads unmistakably as "the fork went blind against an unchanged
stock rule". It is an artefact of a pairing that no release ships. Compare whole
bundles.

## Measure

```bash
export CQ=/tmp/cqbundle/codeql/codeql
$CQ database create /tmp/cqdb --language=python --source-root=. --overwrite

$CQ database analyze /tmp/cqdb --rerun --format=csv --output=/tmp/stock.csv \
  "codeql/python-queries:Security/CWE-078/CommandInjection.ql"

(cd .github/codeql/custom-queries && \
 $CQ database analyze /tmp/cqdb --rerun --format=csv --output=/tmp/fork.csv \
   ./CommandInjectionSanitized.ql)
```

Compare **distinct sources**, not flows -- one unguarded source fans out to many
sinks:

```bash
for f in stock fork; do
  grep -oE 'relative:///[^]|]*' /tmp/$f.csv | sed 's/""$//' | sort -u > /tmp/src_$f.txt
done
echo "cleared: $(comm -23 /tmp/src_stock.txt /tmp/src_fork.txt | wc -l)"
echo "NEW:     $(comm -13 /tmp/src_stock.txt /tmp/src_fork.txt | wc -l)"
```

The hub's `scripts/check_codeql_fork_validation.py` does this comparison, and
`codeql-fork-validation.yml` runs it daily against this repo.

## Baseline recorded 2026-08-15 (Factory#778)

PFactory `dev`, CodeQL **2.26.3**, `python-all` 7.2.3, database over the whole
repo, distinct sources:

| rule | stock | fork | cleared | NEW |
|---|---|---|---|---|
| `py/path-injection` | 76 | 6 | 70 | 0 |
| `py/command-line-injection` | 2 | 0 | 2 | 0 |
| `py/full-ssrf` | 1 | 0 | 1 | 0 |
| `py/partial-ssrf` | 10 | 0 | 10 | 0 |

## The two checks that are not optional

**Vacuous-barrier control: the fork must reproduce stock exactly.** Rewrite the
sanitizer class to match an impossible name and re-run. If the fork does not
then equal stock, the difference is not the barrier and no barrier reasoning
about it is valid. Run for `CommandInjectionSanitized.ql`:

| toolchain | stock | fork, sanitizer vacuous |
|---|---|---|
| 2.25.6 bundle | 32 | **32** |
| 2.26.3 bundle | 2 | **2** |

Equal in both, so the pack is faithful: neither under-matching nor over-broad.

**Spot-check what cleared.** A barrier matching a name by accident clears alerts
just as effectively as a correct one and looks identical in the numbers. The two
sources the command-injection pack clears, confirmed by reading the code:

- `routes/changelog.py:629` -- every ref reaching `git log` argv passes
  `assert_safe_git_ref` (aliased locally); attributed by ablation, removing that
  one barrier name restores this source.
- `routes/terminal.py:384` -- `safe_spec_component` on the worktree name;
  removing that name restores this source and only this one.

## Why `py/command-line-injection` reads 2 and not 32

Recorded because the low number invites the wrong conclusion. Under the 2.25.6
bundle the stock rule reports 32 distinct sources on this tree; under 2.26.3 it
reports 2. Upstream narrowed the rule's source model between those releases. The
30 that disappeared are FastAPI route parameters (`projectId`, `prNumber`,
`task_id`) reaching `gh`/`git` argv through `run_gh_command` / `run_git_command`.

Two things follow, and both matter:

- **The barrier is not responsible.** It clears the same two sources under both
  toolchains, and the vacuous-barrier control above shows the fork tracking
  stock exactly in each.
- **The 30 were never hidden.** Under 2.25.6 the fork reported all 30; they were
  visible alerts, not suppressed ones. Under 2.26.3 neither query models them as
  sources.

Whether those 30 flows deserve a source model is upstream's call, not something
a barrier pack should paper over in either direction. If a future bundle
re-widens the rule they will reappear, and the daily gate will show `cleared`
unchanged at 2 against a larger stock -- which is the correct reading.

## Registered barriers

Path injection: `safe_spec_component`, `split_task_id`, `get_next_spec_id`,
`_safe_launch_path`, `confine_to_workspace`, `os.path.basename`.

Command injection: `assert_safe_git_ref`, `ref`, `log_count`,
`safe_spec_component`.

Ablation shows only two of those four do any work, and it is worth knowing which,
because a reader should not assume every name in a barrier list is load-bearing:

| barrier list | sources left | clears |
|---|---|---|
| all four | 30 | `changelog.py:629`, `terminal.py:384` |
| `assert_safe_git_ref` only | 31 | `changelog.py:629` |
| minus `safe_spec_component` | 31 | `changelog.py:629` |
| minus `ref` | 30 | both -- no change |
| minus `log_count` | 30 | both -- no change |

(measured under the 2.25.6 bundle, where stock's 32 sources make the differences
visible; under 2.26.3 stock reports 2 and both are cleared either way)

`ref` is redundant because it is a thin wrapper that calls `assert_safe_git_ref`
and re-raises as a 400 -- a flow through `ref` passes through the inner call,
which is already a barrier. `log_count` is redundant for a different reason: it
returns `int(value)`, and the integer conversion breaks the taint on its own
without any barrier being registered.

Both are harmless and are left as documentation of intent. Neither should be
cited as evidence that a path is guarded.

SSRF: `assert_safe_probe_url` (full), `assert_safe_outbound_url` (partial), each
on the call and on the guard's own first parameter. Both registrations are BY
NAME, so PFactory#612 -- which moved `assert_safe_outbound_url` from the forked
`server/services/url_safety.py` to the vendored hub canonical
`factory_common/url_safety.py` and deleted the fork -- left them matching. No
barrier was added or removed there.

## If you add a barrier

Register it, re-measure, and run both checks above. A barrier that matches nodes
and clears no alerts is a claim in the query that no result depends on --
AIFactory's `VALIDATION.md` records one that matched 172 nodes and moved zero
alerts, and it was refused for that reason.
