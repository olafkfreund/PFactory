"""Compliance lens: surface regulatory obligations a plan's data classes imply.

Deterministic heuristics, like every other lens — no model call. The lens scans
the normalized plan text for *data-class signals* (personal profiles, location,
matching/recommendation, user-to-user contact, store distribution) and raises a
:class:`~plan.review.models.Finding` for each obligation the plan does not
address: lawful basis, location consent and minimisation, profiling
transparency, trust and safety, age assurance, retention/erasure, in-app
account deletion, and — blocking — a plan that processes personal data but
names no target jurisdiction, so applicable law cannot be determined.

Every finding that asks for a change carries at least one
:class:`~plan.review.models.Citation` with a real, resolvable ``uri`` —
honouring the house rule stated on ``Citation``: PFactory helps, never
overrides; say WHY and point at a source the engineer can read.

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


# ── the lens ───────────────────────────────────────────────────────────────


class ComplianceLens:
    """Deterministic obligation-surfacing heuristics over the plan text."""

    name = "compliance"

    def evaluate(self, plan: NormalizedPlan, epic: EpicPlan) -> LensScore:  # noqa: ARG002 - Lens protocol signature
        text = scan_text(plan)
        personal = processes_personal_data(plan)
        is_social = bool(_CONTACT_RE.search(text))

        findings: list[Finding] = []
        for rule in _RULES:
            if rule.signal is None:
                if not personal:
                    continue
            elif not rule.signal.search(text):
                continue
            if rule.addressed.search(text):
                continue
            findings.append(
                Finding(
                    title=rule.title,
                    detail=f"{rule.detail} {DISCLAIMER}",
                    severity=rule.severity,
                    source=self.name,
                    citations=list(rule.citations),
                )
            )

        # Age assurance: personal data + no age gate stated. On a social app
        # (user-to-user contact) this is high + blocking; otherwise medium.
        if personal and not _AGE_OK_RE.search(text):
            findings.append(
                Finding(
                    title="No age assurance stated",
                    detail=(
                        "The plan processes personal data but states no age "
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
                    severity="high" if is_social else "medium",
                    source=self.name,
                    blocking=is_social,
                    citations=[_CITE_COPPA, _CITE_UK_AADC, _CITE_GDPR_8],
                )
            )

        # Jurisdiction: without target markets, applicable law cannot be
        # determined at all — so every other obligation is unresolvable. Blocking.
        if personal and not declared_jurisdictions(plan):
            findings.append(
                Finding(
                    title="No target jurisdiction stated - applicable law cannot be determined",
                    detail=(
                        "The plan processes personal data but names no target "
                        "market, so which regulations apply cannot be "
                        "determined. Add a '## Jurisdictions' section naming "
                        "the target markets (e.g. UK, EU, US-California). " + DISCLAIMER
                    ),
                    severity="high",
                    source=self.name,
                    blocking=True,
                    citations=[_CITE_GDPR_6],
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
