"""Compliance lens: surface regulatory obligations a plan's data classes imply.

Deterministic heuristics, like every other lens — no model call. The lens scans
the normalized plan text for *data-class signals* (personal profiles, location,
matching/recommendation, user-to-user contact, store distribution) and raises a
:class:`~plan.review.models.Finding` for each obligation the plan does not
address: lawful basis, location consent and minimisation, profiling
transparency, trust and safety, age assurance, retention/erasure, in-app
account deletion, and — blocking — a plan that processes personal data but
names no target jurisdiction, so applicable law cannot be determined.

When the plan carries a project constitution (RFC-0015,
``plan.constitution_md``), its ENFORCEABLE clauses are classified to these
obligation topics and checked too: a customer whose policy demands age
assurance and whose plan is silent gets a blocking finding citing their own
clause, not only the generic regulation. Clauses that classify to no plan-time
topic are surfaced in an info finding rather than silently dropped. This is
the only plan-time execution of the constitution: the default pipeline is
deterministic (no LLM prompt injection runs) and the downstream
standards_conformance gate proves linter-class tooling ran, not that an
obligation was considered.

Every finding that asks for a change carries at least one
:class:`~plan.review.models.Citation` with a real, resolvable ``uri`` —
honouring the house rule stated on ``Citation``: PFactory helps, never
overrides; say WHY and point at a source the engineer can read. (A
constitution citation's ``uri`` is the repo path ``.factory/constitution.md``;
such findings always also carry a regulation citation with an https URI.)

IMPORTANT — this lens is a *descriptive obligations signpost*, not legal
advice. The article/guideline references are navigational labels indicating
where an obligation may arise; they are explicitly not a determination that any
law applies or that the plan does or does not comply. Determinations of actual
legal obligations require qualified counsel. Every finding set the lens emits
carries :data:`DISCLAIMER`. (This mirrors the disclaimer stance of
``plan/emit/audit_pack.py``.)
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, NamedTuple

from plan.review.lenses.base import register_lens
from plan.review.models import Citation, Finding, LensScore

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan

DISCLAIMER = (
    "This finding surfaces a potential regulatory obligation and cites sources "
    "the engineer can read. It is a descriptive signpost, not legal advice, and "
    "not a determination that any law applies or is violated; consult qualified "
    "counsel for actual obligations."
)

# ── data-class signal patterns ─────────────────────────────────────────────

_PERSONAL_RE = re.compile(
    r"(?i)\b(personal\s+(?:data|information)|profiles?|accounts?|photos?|selfies?|"
    r"avatars?|email\s+address|date\s+of\s+birth|phone\s+number|user\s+data|"
    r"sign[\s-]?up|registration)\b"
)
_LOCATION_RE = re.compile(
    r"(?i)\b(locations?|geolocation|geo\w*|gps|nearby|around\s+you|proximity|"
    r"latitude|longitude)\b"
)
_PROFILING_RE = re.compile(
    r"(?i)\b(match(?:ing|es|ed)?|recommend\w*|suggest(?:ed|ions?)?\s+"
    r"(?:people|friends|users)|personali[sz]\w+|ranking\s+algorithm)\b"
)
_CONTACT_RE = re.compile(
    r"(?i)\b(chat|direct\s+messages?|dms?|messag(?:es?|ing)|user[\s-]to[\s-]user|"
    r"connect\s+with|friend\s+requests?|contact\s+(?:other\s+)?users)\b"
)
_STORE_RE = re.compile(
    r"(?i)\b(app\s+store|play\s+store|google\s+play|testflight|ios\s+app|"
    r"android\s+app|mobile\s+app|app\s+review)\b"
)

# ── "the plan already addressed it" patterns (suppress the finding) ────────

_LAWFUL_BASIS_OK_RE = re.compile(
    r"(?i)\b(lawful\s+basis|legal\s+basis|purpose\s+limitation|legitimate\s+interest)\b"
)
_LOCATION_OK_RE = re.compile(
    r"(?i)\bconsent\b.*\b(coarse|precision|minimi[sz]\w+)\b"
    r"|\b(coarse|precision|minimi[sz]\w+)\b.*\bconsent\b",
    re.DOTALL,
)
_PROFILING_OK_RE = re.compile(
    r"(?i)\b(profiling|automated\s+(?:decision|processing)|art(?:icle)?\.?\s*22)\b"
)
_SAFETY_OK_RE = re.compile(
    r"(?i)\b(block(?:ing)?\s+(?:and\s+report|users?)|report(?:ing)?\s+"
    r"(?:abuse|users?|content)|moderat\w+|notice[\s-]and[\s-]action)\b"
)
# NOTE: '16+' ends in a non-word char, so it must not sit before a closing \b.
_AGE_OK_RE = re.compile(
    r"(?i)\b(?:age\s+(?:gate|assurance|verification|check)\b|minimum\s+age\b|"
    r"under[\s-]?1[38]\b|1[368]\s*\+|coppa\b|age[\s-]appropriate\b|"
    r"parental\s+consent\b)"
)
_RETENTION_OK_RE = re.compile(
    r"(?i)\b(retention|erasure|right\s+to\s+be\s+forgotten|storage\s+limitation|"
    r"delet(?:e|ion|ing)\b.{0,40}\b(?:data|account|profile)s?|data\s+lifecycle)\b"
)
_ACCOUNT_DELETION_OK_RE = re.compile(
    r"(?i)\b(account\s+deletion|delete\s+(?:their|my|the|an?)\s+account|"
    r"in[\s-]app\s+deletion)\b"
)

# ── jurisdiction detection ─────────────────────────────────────────────────

# A "## Jurisdictions" (or "Target markets") section heading in the raw spec.
_JURISDICTION_SECTION_RE = re.compile(r"(?im)^#{1,6}\s*(?:jurisdictions?|target\s+markets?)\b")
# Market names, case-insensitive where unambiguous...
_MARKET_NAME_RE = re.compile(
    r"(?i)\b(european\s+union|united\s+kingdom|great\s+britain|united\s+states|"
    r"california|germany|france|spain|italy|netherlands|norway|switzerland|"
    r"canada|australia|japan|brazil|india)\b"
)
# ...and uppercase-only acronyms so prose "us"/"eu" cannot false-positive.
# 'U.S.' ends in a non-word char, so it must not sit before a closing \b.
_MARKET_ACRONYM_RE = re.compile(r"\b(?:EU|EEA|UK|USA)\b|\bU\.S\.")

# ── citations (real, resolvable URIs) ──────────────────────────────────────


def _cite(why: str, uri: str, title: str, source: str) -> Citation:
    return Citation(why=why, uri=uri, title=title, source=source)


_GDPR = "regulation:gdpr"
_CITE_GDPR_5 = _cite(
    "Personal data must be processed for specified, explicit purposes.",
    "https://gdpr-info.eu/art-5-gdpr/",
    "GDPR Art. 5 - Principles relating to processing of personal data",
    _GDPR,
)
_CITE_GDPR_6 = _cite(
    "Processing personal data requires a stated lawful basis.",
    "https://gdpr-info.eu/art-6-gdpr/",
    "GDPR Art. 6 - Lawfulness of processing",
    _GDPR,
)
_CITE_GDPR_8 = _cite(
    "A child's consent is only valid above a member-state age threshold.",
    "https://gdpr-info.eu/art-8-gdpr/",
    "GDPR Art. 8 - Conditions applicable to child's consent",
    _GDPR,
)
_CITE_GDPR_13 = _cite(
    "Users must be told about automated decision-making, including profiling.",
    "https://gdpr-info.eu/art-13-gdpr/",
    "GDPR Art. 13(2)(f) - Information to be provided",
    _GDPR,
)
_CITE_GDPR_17 = _cite(
    "Users have a right to erasure of their personal data.",
    "https://gdpr-info.eu/art-17-gdpr/",
    "GDPR Art. 17 - Right to erasure",
    _GDPR,
)
_CITE_GDPR_22 = _cite(
    "Solely automated decisions with significant effects are restricted.",
    "https://gdpr-info.eu/art-22-gdpr/",
    "GDPR Art. 22 - Automated individual decision-making, including profiling",
    _GDPR,
)
_CITE_APPLE_511 = _cite(
    "Apple review requires purpose strings, consent, and data-collection limits.",
    "https://developer.apple.com/app-store/review/guidelines/#5.1.1",
    "Apple App Review Guideline 5.1.1 - Data Collection and Storage",
    "store-policy:apple",
)
_CITE_PLAY_LOCATION = _cite(
    "Google Play restricts location access, especially in the background.",
    "https://support.google.com/googleplay/android-developer/answer/9799150",
    "Google Play policy - Location permissions",
    "store-policy:google-play",
)
_CITE_PLAY_DELETION = _cite(
    "Google Play requires apps with accounts to offer account and data deletion.",
    "https://support.google.com/googleplay/android-developer/answer/13327111",
    "Google Play policy - App account deletion",
    "store-policy:google-play",
)
_CITE_DSA_16 = _cite(
    "Hosting services must provide notice-and-action mechanisms for illegal content.",
    "https://eur-lex.europa.eu/eli/reg/2022/2065/oj",
    "EU Digital Services Act Art. 16 - Notice and action mechanisms",
    "regulation:eu-dsa",
)
_CITE_DSA_20 = _cite(
    "Online platforms must operate an internal complaint-handling system.",
    "https://eur-lex.europa.eu/eli/reg/2022/2065/oj",
    "EU Digital Services Act Art. 20 - Internal complaint-handling system",
    "regulation:eu-dsa",
)
_CITE_COPPA = _cite(
    "US services directed at (or knowingly collecting from) under-13s need parental consent.",
    "https://www.ftc.gov/legal-library/browse/rules/childrens-online-privacy-protection-rule-coppa",
    "COPPA - Children's Online Privacy Protection Rule (16 CFR Part 312)",
    "regulation:us-coppa",
)
_CITE_UK_AADC = _cite(
    "UK services likely to be accessed by children must meet the Children's Code.",
    "https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/childrens-information/childrens-code-guidance-and-resources/",
    "UK ICO Age Appropriate Design Code (Children's Code)",
    "regulator:uk-ico",
)

# ── the signal → finding table ─────────────────────────────────────────────


class _Rule(NamedTuple):
    """One data-class heuristic: signal present + obligation unaddressed."""

    key: str
    signal: re.Pattern[str] | None  # None: fires on personal data alone
    addressed: re.Pattern[str]
    title: str
    detail: str
    severity: str
    citations: list[Citation]


_RULES: tuple[_Rule, ...] = (
    _Rule(
        key="personal-profile",
        signal=_PERSONAL_RE,
        addressed=_LAWFUL_BASIS_OK_RE,
        title="Lawful basis and purpose limitation not stated",
        detail=(
            "The plan handles personal data (profiles, accounts, or photos) but "
            "states no lawful basis or purpose limitation for that processing. "
            "State the basis (e.g. consent, contract, legitimate interest) and "
            "the specific purposes the data is collected for."
        ),
        severity="medium",
        citations=[_CITE_GDPR_5, _CITE_GDPR_6],
    ),
    _Rule(
        key="location",
        signal=_LOCATION_RE,
        addressed=_LOCATION_OK_RE,
        title="Location data handling not specified",
        detail=(
            "The plan uses location ('nearby'/geo signals) but does not state "
            "explicit consent, precision minimisation (coarse by default), or "
            "background-use disclosure. Location is treated as sensitive by "
            "regulators and both app stores."
        ),
        severity="medium",
        citations=[_CITE_GDPR_6, _CITE_APPLE_511, _CITE_PLAY_LOCATION],
    ),
    _Rule(
        key="profiling",
        signal=_PROFILING_RE,
        addressed=_PROFILING_OK_RE,
        title="Automated matching without profiling transparency",
        detail=(
            "The plan matches or recommends people/content but says nothing "
            "about automated-processing and profiling transparency. State what "
            "the algorithm uses, how users are informed, and whether a solely "
            "automated decision has significant effects."
        ),
        severity="medium",
        citations=[_CITE_GDPR_13, _CITE_GDPR_22],
    ),
    _Rule(
        key="user-contact",
        signal=_CONTACT_RE,
        addressed=_SAFETY_OK_RE,
        title="User-to-user contact without trust and safety controls",
        detail=(
            "The plan lets users contact each other but states no blocking, "
            "reporting, moderation, or notice-and-action mechanism. Add these "
            "as explicit acceptance criteria."
        ),
        severity="medium",
        citations=[_CITE_DSA_16, _CITE_DSA_20],
    ),
    _Rule(
        key="retention",
        signal=None,  # applies whenever personal data is processed
        addressed=_RETENTION_OK_RE,
        title="No retention or deletion policy stated",
        detail=(
            "The plan processes personal data but states no retention period, "
            "deletion path, or erasure handling. State how long each data class "
            "is kept and how a user's erasure request is honoured."
        ),
        severity="medium",
        citations=[_CITE_GDPR_5, _CITE_GDPR_17],
    ),
    _Rule(
        key="store-account-deletion",
        signal=_STORE_RE,
        addressed=_ACCOUNT_DELETION_OK_RE,
        title="Store distribution without in-app account deletion",
        detail=(
            "The plan implies app-store distribution but states no in-app "
            "account deletion. Both Apple and Google require apps that support "
            "account creation to also offer account deletion."
        ),
        severity="medium",
        citations=[_CITE_APPLE_511, _CITE_PLAY_DELETION],
    ),
)

_PENALTY = {"info": 0.0, "low": 0.05, "medium": 0.15, "high": 0.3, "critical": 0.6}


# ── shared helpers (also consumed by readiness + emit) ─────────────────────


def scan_text(plan: NormalizedPlan) -> str:
    """The plan text the compliance heuristics scan (title, prose, criteria)."""
    parts = [plan.title, plan.description, plan.raw_text or ""]
    parts.extend(c.text for c in plan.criteria)
    return "\n".join(p for p in parts if p)


def detect_data_classes(text: str) -> list[str]:
    """Data-class signals present in ``text``, as stable keys."""
    classes: list[str] = []
    for key, pattern in (
        ("personal-profile", _PERSONAL_RE),
        ("location", _LOCATION_RE),
        ("profiling", _PROFILING_RE),
        ("user-contact", _CONTACT_RE),
        ("store-distribution", _STORE_RE),
    ):
        if pattern.search(text):
            classes.append(key)
    return classes


def processes_personal_data(plan: NormalizedPlan) -> bool:
    """True when the plan text shows any personal-data signal.

    Location, matching, and user-to-user contact all imply personal data even
    when the word "profile"/"account" never appears.
    """
    text = scan_text(plan)
    return any(p.search(text) for p in (_PERSONAL_RE, _LOCATION_RE, _PROFILING_RE, _CONTACT_RE))


def declared_jurisdictions(plan: NormalizedPlan) -> list[str]:
    """Target markets the plan names (section heading or market names)."""
    text = scan_text(plan)
    found: list[str] = []
    if _JURISDICTION_SECTION_RE.search(text):
        found.append("jurisdictions-section")
    found.extend(m.group(0) for m in _MARKET_NAME_RE.finditer(text))
    found.extend(m.group(0) for m in _MARKET_ACRONYM_RE.finditer(text))
    # De-duplicate case-insensitively, preserving first-seen order.
    seen: set[str] = set()
    unique: list[str] = []
    for name in found:
        lowered = name.lower()
        if lowered not in seen:
            seen.add(lowered)
            unique.append(name)
    return unique


# ── constitution grounding (RFC-0015) ──────────────────────────────────────
#
# When the plan carries a project constitution (captured during recon as
# plan.constitution_md), its ENFORCEABLE clauses are read and checked against
# the plan. A customer whose policy says "P3 (enforceable): age assurance
# required" and whose plan is silent gets a BLOCKING finding that cites their
# own P3, not only a generic COPPA reference. This matters because at plan
# time nothing else executes the constitution: the default pipeline is fully
# deterministic (decompose_method: heuristic — the LLM prompt injection point
# never runs), and the downstream standards_conformance gate proves
# linter-class tooling ran, not that a retention period was considered.
#
# Each enforceable clause is classified to one of the lens's obligation topics
# by keywords in the clause text; first match wins, most specific first. A
# clause that classifies to no topic is surfaced in an info finding rather
# than silently dropped — a clause reaching no check must never look enforced.

_CLAUSE_TOPICS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("jurisdiction", re.compile(r"(?i)\b(jurisdictions?|markets?)\b")),
    ("age", re.compile(r"(?i)\b(age|minors?|under[\s-]?18|child(?:ren)?)\b")),
    ("location", re.compile(r"(?i)\b(location|precision|geofenc\w+)\b")),
    (
        "user-contact",
        re.compile(
            r"(?i)\b(block(?:ing)?|report(?:ing)?|moderat\w+|response\s+path|"
            r"person[\s-]to[\s-]person)\b"
        ),
    ),
    (
        "store-account-deletion",
        re.compile(r"(?i)\baccounts?\b.*\bdelet\w+|\bdelet\w+\b.*\baccounts?\b", re.DOTALL),
    ),
    ("retention", re.compile(r"(?i)\b(retention|retain\w*|how\s+long|kept|storage\s+period)\b")),
    ("profiling", re.compile(r"(?i)\b(profil\w+|automated\s+decision|recommend\w+|matching)\b")),
    ("personal-profile", re.compile(r"(?i)\b(lawful\s+basis|purpose\s+limitation)\b")),
)

_CLAUSE_EXCERPT_LEN = 200


def enforceable_clauses(plan: NormalizedPlan) -> list[dict[str, str]]:
    """The constitution's enforceable principles, or [] when none. Never raises."""
    text = getattr(plan, "constitution_md", None)
    if not text:
        return []
    try:
        # Lazy: keep the emit stage out of the review import graph (the same
        # seam plan.review.gates uses for the readiness constitution block).
        from plan.emit.constitution import parse_constitution  # noqa: PLC0415

        return [
            {"id": str(p["id"]), "text": str(p["text"])}
            for p in parse_constitution(text)
            if p.get("enforceable")
        ]
    except Exception:  # noqa: BLE001 — constitution grounding is best-effort, never breaks review
        return []


def _classify_clause(text: str) -> str | None:
    for topic, pattern in _CLAUSE_TOPICS:
        if pattern.search(text):
            return topic
    return None


def _clause_citation(clause: dict[str, str]) -> Citation:
    return Citation(
        why=clause["text"][:_CLAUSE_EXCERPT_LEN],
        uri=".factory/constitution.md",
        title=f"Project constitution {clause['id']} (enforceable)",
        source="constitution:.factory/constitution.md",
    )


def _clause_prefix(clause: dict[str, str] | None) -> str:
    if clause is None:
        return ""
    return (
        f"The project constitution ({clause['id']}, enforceable) makes this "
        f"mandatory: {clause['text'][:_CLAUSE_EXCERPT_LEN]!r}. "
    )


# ── the lens ───────────────────────────────────────────────────────────────


class ComplianceLens:
    """Deterministic obligation-surfacing heuristics over the plan text."""

    name = "compliance"

    def evaluate(self, plan: NormalizedPlan, epic: EpicPlan) -> LensScore:  # noqa: ARG002 - Lens protocol signature
        text = scan_text(plan)
        personal = processes_personal_data(plan)
        is_social = bool(_CONTACT_RE.search(text))

        # The customer's own enforceable policy, classified to this lens's
        # obligation topics. A clause on a topic upgrades the topic's finding
        # to high + blocking and cites the clause itself.
        clause_for_topic: dict[str, dict[str, str]] = {}
        unmapped_clauses: list[dict[str, str]] = []
        for clause in enforceable_clauses(plan):
            topic = _classify_clause(clause["text"])
            if topic is None:
                unmapped_clauses.append(clause)
            elif topic not in clause_for_topic:
                clause_for_topic[topic] = clause

        findings: list[Finding] = []
        for rule in _RULES:
            if rule.signal is None:
                if not personal:
                    continue
            elif not rule.signal.search(text):
                continue
            if rule.addressed.search(text):
                continue
            rule_clause = clause_for_topic.get(rule.key)
            findings.append(
                Finding(
                    title=rule.title,
                    detail=f"{_clause_prefix(rule_clause)}{rule.detail} {DISCLAIMER}",
                    severity="high" if rule_clause else rule.severity,
                    source=self.name,
                    blocking=rule_clause is not None,
                    citations=([_clause_citation(rule_clause)] if rule_clause else [])
                    + list(rule.citations),
                )
            )

        # Age assurance: personal data + no age gate stated. On a social app
        # (user-to-user contact), or under an enforceable constitution clause,
        # this is high + blocking; otherwise medium.
        if personal and not _AGE_OK_RE.search(text):
            age_clause = clause_for_topic.get("age")
            age_blocking = is_social or age_clause is not None
            findings.append(
                Finding(
                    title="No age assurance stated",
                    detail=(
                        _clause_prefix(age_clause)
                        + "The plan processes personal data but states no age "
                        "gate, age assurance, or children's-data handling. "
                        "State the minimum age, how it is checked, and how "
                        "under-age users are handled. "
                        + (
                            "For a social app that connects users this is a blocking gap. "
                            if is_social
                            else ""
                        )
                        + DISCLAIMER
                    ),
                    severity="high" if age_blocking else "medium",
                    source=self.name,
                    blocking=age_blocking,
                    citations=([_clause_citation(age_clause)] if age_clause else [])
                    + [_CITE_COPPA, _CITE_UK_AADC, _CITE_GDPR_8],
                )
            )

        # Jurisdiction: without target markets, applicable law cannot be
        # determined at all — so every other obligation is unresolvable. Blocking.
        if personal and not declared_jurisdictions(plan):
            juris_clause = clause_for_topic.get("jurisdiction")
            findings.append(
                Finding(
                    title="No target jurisdiction stated - applicable law cannot be determined",
                    detail=(
                        _clause_prefix(juris_clause)
                        + "The plan processes personal data but names no target "
                        "market, so which regulations apply cannot be "
                        "determined. Add a '## Jurisdictions' section naming "
                        "the target markets (e.g. UK, EU, US-California). " + DISCLAIMER
                    ),
                    severity="high",
                    source=self.name,
                    blocking=True,
                    citations=([_clause_citation(juris_clause)] if juris_clause else [])
                    + [_CITE_GDPR_6],
                )
            )

        # Enforceable clauses this lens cannot check at plan time are surfaced,
        # never silently dropped: a clause reaching no check must not look
        # enforced. Info only — it reports, it does not request a change.
        if unmapped_clauses:
            ids = ", ".join(c["id"] for c in unmapped_clauses)
            findings.append(
                Finding(
                    title="Enforceable constitution clauses not machine-checked at plan time",
                    detail=(
                        f"Clause(s) {ids} are marked enforceable but classify to "
                        "no plan-time obligation this lens checks (e.g. a "
                        "verification-stage rule). They are surfaced here so the "
                        "approver knows they rely on downstream gates or human "
                        "review, not on this lens. " + DISCLAIMER
                    ),
                    severity="info",
                    source=self.name,
                    citations=[_clause_citation(c) for c in unmapped_clauses],
                )
            )

        score = 1.0
        blocking = False
        for f in findings:
            score -= _PENALTY.get(f.severity, 0.0)
            blocking = blocking or f.blocking

        return LensScore(
            lens=self.name,
            score=round(max(0.0, min(1.0, score)), 4),
            findings=findings,
            blocking=blocking,
        )


register_lens(ComplianceLens())
