"""Auth coverage accepts every auth word form, in both lenses (#670).

The security and red-team lenses share ``AUTH_RE``. A networked plan that
states auth as "authenticated"/"unauthenticated" must not be told it has no
auth criteria; a plan whose only ``auth…`` words are "author"/"authority"
must still be told it has none (false-cover guard).
"""

from __future__ import annotations

import pytest

from plan.decompose.models import EpicPlan
from plan.models import Criterion, NormalizedPlan
from plan.review import extension_registry
from plan.review.lenses.red_team import RedTeamLens
from plan.review.lenses.security import AUTH_RE, SecurityLens

AUTH_WORDS = [
    "auth", "authn", "authz", "authentication", "authenticated", "authenticating",
    "authorization", "authorisation", "authorized", "authorised",
    "unauthenticated", "unauthorized",
]  # fmt: skip
NOT_AUTH_WORDS = ["author", "authored", "authority", "authoritative", "authorship"]

# (lens, title of its "no auth" finding)
LENSES = [
    (SecurityLens, "No authentication/authorization criteria"),
    (RedTeamLens, "Unstated security / access scope"),
]


@pytest.fixture(autouse=True)
def _enable_red_team(monkeypatch):
    monkeypatch.setenv("PFACTORY_RED_TEAM_REVIEW", "1")
    extension_registry.reset_cache()
    yield
    extension_registry.reset_cache()


def _auth_finding(lens_cls, title: str, criterion: str) -> bool:
    plan = NormalizedPlan(
        plan_id="001-x",
        title="Build a service",
        description="",
        source_format="markdown",
        target_kind="software",
        criteria=[Criterion(id="AC#1", text=criterion)],
    )
    score = lens_cls().evaluate(plan, EpicPlan(plan_id="001-x", epic_title="x", children=[]))
    return any(f.title == title for f in score.findings)


@pytest.mark.parametrize("word", AUTH_WORDS)
def test_auth_re_matches_every_auth_form(word):
    assert AUTH_RE.search(f"the {word} flow")


@pytest.mark.parametrize("word", NOT_AUTH_WORDS)
def test_auth_re_ignores_author_words(word):
    assert not AUTH_RE.search(f"the {word} of the doc")


@pytest.mark.parametrize(("lens_cls", "title"), LENSES)
@pytest.mark.parametrize(
    "criterion",
    [
        # The exact AC from the issue (session 010-myfriends).
        "The backend exposes the discovery, request, and messaging operations over an "
        "authenticated API, and rejects any unauthenticated call.",
        "Every API request is authorised against the caller's role.",
        "The server rejects unauthorized requests with 401.",
    ],
)
def test_participle_phrasing_counts_as_auth(lens_cls, title, criterion):
    assert not _auth_finding(lens_cls, title, criterion)


@pytest.mark.parametrize(("lens_cls", "title"), LENSES)
def test_author_words_do_not_count_as_auth(lens_cls, title):
    criterion = "The API server is authored by the platform team, the authority on the schema."
    assert _auth_finding(lens_cls, title, criterion)
