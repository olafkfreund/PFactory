"""deploy.yml must not ignore what .dockerignore deliberately keeps (Factory#552).

THE DEFECT THIS CLOSES (Factory#454). `deploy.yml` carried
``paths-ignore: ['docs/**', '**/*.md']``. The agent prompts live in
``apps/backend/prompts/*.md``, are read at runtime, and match ``**/*.md`` — so a
prompt-only merge to `main` triggered no deploy at all. Every status stayed green
while the behaviour on `main` was not the behaviour in the cluster. It happened
twice, and one of the two commits was the fix for the wrong-language gap, which
shipped nowhere at merge time.

THE INVARIANT IS NOT "anything baked into the image must trigger a deploy". The
Dockerfile copies the whole repo, so `docs/` is in the image too, and skipping a
deploy for a docs change is CORRECT — nothing reads it at runtime. The question
is which baked files change behaviour, and `.dockerignore` already answers it:

    *.md
    !apps/**/*.md

Everything markdown is stripped from the build context EXCEPT ``apps/**/*.md``.
That negation exists for exactly one reason: those files are read at runtime. So
the rule is the narrower one:

    paths-ignore must not ignore anything .dockerignore deliberately un-ignores.

Which is precisely what ``**/*.md`` did — it swallowed the one directory
`.dockerignore` went out of its way to keep.

WHY THIS IS A TEST AND NOT A HUB WATCHDOG. It reads two files in this repo and
needs nothing else, and a per-repo test fails in the PR that breaks it. A hub
check would find it the next day, after the merge — and Factory#525 settled that
post-merge is a strictly worse place to find this class of thing.

The two files are edited by different people, for different reasons, months
apart, and neither mentions the other. Reverting `'*.md'` to `'**/*.md'` reads
as a tidy-up in a diff.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[1]


def _github_glob_matches(path: str, pattern: str) -> bool:
    """Does *path* match *pattern* under GITHUB's path-filter rules?

    Deliberately not ``fnmatch``. In a GitHub path filter ``*`` does NOT cross
    ``/`` and ``**`` does; ``fnmatch``'s ``*`` crosses ``/``, so it reports
    ``apps/backend/prompts/coder.md`` as matching ``*.md``. That is a wrong
    answer in the dangerous direction — it calls the CORRECT configuration
    broken, and would wave the broken one through if the patterns were inverted.
    Measured while verifying Factory#454; it caught me first.
    """
    rx = re.escape(pattern)
    rx = rx.replace(r"\*\*/", "(?:.*/)?").replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
    rx = rx.replace(r"\?", "[^/]")
    return re.fullmatch(rx, path) is not None


def _dockerignore_negations() -> list[str]:
    """Patterns `.dockerignore` re-includes after excluding them (``!`` lines).

    A negation is a deliberate act: something matched a broad exclude and
    somebody decided it had to be in the image anyway.
    """
    text = (_REPO / ".dockerignore").read_text(encoding="utf-8")
    return [
        line.strip()[1:]
        for line in text.splitlines()
        if line.strip().startswith("!") and not line.strip().startswith("!#")
    ]


def _deploy_paths_ignore() -> list[str]:
    doc = yaml.safe_load((_REPO / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8"))
    # PyYAML parses the bare key `on:` as the boolean True.
    triggers = doc[True] if True in doc else doc["on"]
    return list((triggers.get("push") or {}).get("paths-ignore") or [])


def _sample_path_for(pattern: str) -> str:
    """A concrete path that the negation pattern would re-include.

    Patterns are matched against paths, so the comparison needs a path. The
    substitution is deliberately literal — `**` becomes one directory level and
    `*` becomes a name — because a representative example is enough to decide
    whether a `paths-ignore` entry swallows the whole class.
    """
    return pattern.replace("**/", "apps/backend/prompts/").replace("**", "x").replace("*", "sample")


def test_paths_ignore_does_not_swallow_a_dockerignore_negation() -> None:
    """THE ASSERTION WITH TEETH — the exact Factory#454 defect.

    Skipped-by-construction when there is nothing to keep: a repo with no
    negations has nothing this rule can violate, which is CFactory's case (it
    has no markdown under apps/ at all). Derived, not special-cased.
    """
    negations = _dockerignore_negations()
    ignores = _deploy_paths_ignore()
    for negation in negations:
        sample = _sample_path_for(negation)
        swallowed = [p for p in ignores if _github_glob_matches(sample, p)]
        assert not swallowed, (
            f".dockerignore keeps {negation!r} in the image because it is read at "
            f"runtime, but deploy.yml's paths-ignore {swallowed} matches {sample!r}. "
            "A change to those files would merge to main and deploy nothing, while "
            "every status stayed green (Factory#454)."
        )


def test_the_prompts_this_service_actually_ships_would_trigger_a_deploy() -> None:
    """The invariant restated against real files, not just patterns.

    A pattern-level check passes vacuously if the prompts move somewhere the
    negation no longer covers. This asserts the concrete paths.
    """
    # TRACKED files only. `Path.glob` walks the working tree and sweeps in
    # node_modules — 240 of CFactory's 250 apps/**/*.md are vendored frontend
    # docs that .dockerignore excludes from the image anyway. Asserting over
    # those is slow and, worse, measures something that does not ship.
    listed = subprocess.run(  # noqa: S603
        ["git", "ls-files", "apps/**/*.md"],  # noqa: S607
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    prompts = sorted(line for line in listed.stdout.splitlines() if line.strip())
    if not prompts:
        return  # nothing baked that is read at runtime; see the test above
    ignores = _deploy_paths_ignore()
    for prompt in prompts:
        swallowed = [p for p in ignores if _github_glob_matches(prompt, p)]
        assert not swallowed, f"{prompt} would not trigger a deploy (matched by {swallowed})"


def test_root_markdown_and_docs_are_still_ignored() -> None:
    """The other half: the rule must not degenerate into deploying on everything.

    Widening `paths-ignore` to nothing would pass every assertion above and
    rebuild the image for a README typo. The useful part of the rule has to
    survive the fix.
    """
    ignores = _deploy_paths_ignore()
    for undeployable in ("README.md", "docs/index.md"):
        assert any(_github_glob_matches(undeployable, p) for p in ignores), (
            f"{undeployable} should not trigger a deploy; paths-ignore is {ignores}"
        )


def test_the_matcher_uses_github_semantics_not_fnmatch() -> None:
    """Guard on the guard: this is the distinction the whole check rests on."""
    assert not _github_glob_matches("apps/backend/prompts/coder.md", "*.md")
    assert _github_glob_matches("README.md", "*.md")
    assert _github_glob_matches("apps/backend/prompts/coder.md", "**/*.md")
    assert _github_glob_matches("docs/a/b.md", "docs/**")
