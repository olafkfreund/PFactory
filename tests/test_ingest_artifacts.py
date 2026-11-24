"""Approved Factory artifact triples map to executable, reviewable plans."""

from pathlib import Path

import pytest

from plan.ingest.artifacts import ArtifactIngestError, ingest_artifacts

FIXTURES = Path(__file__).parent / "fixtures" / "factory_artifacts"


@pytest.mark.parametrize("issue", ["3865", "3858"])
def test_real_triples(issue: str) -> None:
    artifacts = {
        stage: (FIXTURES / issue / f"{stage}.md").read_text()
        for stage in ("intent", "spec", "plan")
    }
    plan, epic = ingest_artifacts(artifacts)
    assert ("factory` CLI" if issue == "3865" else "project breadcrumb") in plan.title
    assert plan.content_trust == "untrusted_user_content"
    assert plan.source_format == "factory-artifacts"
    assert plan.hash_matches()
    assert epic.children
    assert epic.children[-1].acceptance_criteria == [c.text for c in plan.criteria]
    assert "## Rollback" in epic.children[-1].body
    assert all(
        child.depends_on == ([epic.children[i - 1].key] if i else [])
        for i, child in enumerate(epic.children)
    )


def test_frontmatter_and_nested_criteria() -> None:
    plan, epic = ingest_artifacts(
        {
            "intent": (
                "---\nstatus: approved\n---\n# Intent: Example\n"
                "## Problem\nBroken\n## Proposed outcome\nWorks"
            ),
            "spec": "## Verification\n- Parent\n  - nested detail\n- Other\nUnrelated prose.",
            "plan": (
                "## Steps\n1. Add tests\n   Continue\n2. Set up CI\n"
                "## Tests\nRun them\n## Rollback\nRevert"
            ),
        }
    )
    assert plan.title == "Example"
    assert [c.text for c in plan.criteria] == ["Parent nested detail", "Other"]
    assert [c.kind for c in epic.children] == ["testing", "cicd"]
    assert "Continue" in epic.children[0].body
    assert "Run them" in epic.children[-1].body


def test_missing_steps_rejected() -> None:
    with pytest.raises(ArtifactIngestError, match="numbered steps"):
        ingest_artifacts(
            {
                "intent": "# Intent: Example",
                "spec": "## Verification\n- Works",
                "plan": "## Steps\nNone",
            }
        )


def test_steps_keep_preamble_wrapping_and_code_headings() -> None:
    _, epic = ingest_artifacts({
        "intent": "# Intent: Example",
        "spec": "## Verification\n- Works",
        "plan": (
            "## Steps\nShared context before the list.\n"
            "1. **Build** parser\nUnindented continuation\n"
            "```md\n## Fake heading\n```\n2. Test parser\n"
            "## Tests\nRun suite"
        ),
    })
    assert "Shared context before the list." in epic.epic_body
    assert epic.children[0].title == "Build parser"
    assert "Unindented continuation" in epic.children[0].body
    assert "## Fake heading" in epic.children[0].body
    assert len(epic.children) == 2
