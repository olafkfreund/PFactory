"""
Skills Service.

Scans the skills directory to build an in-memory index of skill files,
providing fast lookup by category, keyword search, and auto-suggestion.

Skills path resolution (first match wins):
1. APP_SKILLS_PATH env var (explicit override)
2. <project-root>/skills/  (local copy, works on host and in Docker)

Uses a JSON cache (~/.pfactory/skills-cache.json) to avoid re-scanning
6,000+ files on every startup.  The cache is invalidated when the skills
directory's modification time changes.

The cache is JSON, not pickle (#533; AIFactory #324 L1): a server-generated
cache file is an RCE primitive if its path ever becomes writable or
traversable, because the version/path guards only run *after* ``pickle.load``
has already executed arbitrary reduce code.  JSON loads inert data; we
reconstruct the dataclasses explicitly.

Migration: a pre-existing ``skills-cache.pkl`` is never opened — the cache
path itself changed, so a stale pickle is inert and the index is rebuilt
from source on first start.
"""

import contextlib
import json
import logging
import os
import re
import string
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _resolve_skills_path() -> Path:
    """Resolve the skills directory path.

    Priority:
    1. APP_SKILLS_PATH env var (explicit override)
    2. <project-root>/skills/ (local copy)

    If neither resolves, returns the local path anyway — the caller will
    log a warning when scanning finds no files.
    """
    # 1. Explicit env var
    env_path = os.environ.get("APP_SKILLS_PATH")
    if env_path:
        return Path(env_path)

    # 2. Local skills/ directory relative to project root
    # Project root is 4 levels up: services/ -> server/ -> web-server/ -> apps/ -> root
    project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
    return project_root / "skills"


DEFAULT_SKILLS_PATH = _resolve_skills_path()

# Cache location
DEFAULT_CACHE_PATH = Path.home() / ".pfactory" / "skills-cache.json"

# Cache format version — bump when _IndexEntry or SkillSummary fields change.
# Bumped to 2 for the pickle->JSON migration (#533).
_CACHE_VERSION = 2

# Stop words excluded from keyword search / suggestion scoring
STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "need",
        "to",
        "for",
        "of",
        "with",
        "in",
        "on",
        "at",
        "by",
        "from",
        "up",
        "about",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "out",
        "off",
        "over",
        "under",
        "again",
        "further",
        "then",
        "once",
        "and",
        "but",
        "or",
        "nor",
        "not",
        "so",
        "yet",
        "both",
        "either",
        "neither",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "only",
        "own",
        "same",
        "than",
        "too",
        "very",
        "just",
        "how",
        "what",
        "when",
        "where",
        "who",
        "which",
        "this",
        "that",
        "these",
        "those",
        "i",
        "we",
        "you",
        "he",
        "she",
        "it",
        "they",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "his",
        "their",
        "our",
        "build",
        "create",
        "make",
        "add",
        "use",
        "using",
        "want",
        "implement",
        "write",
        "new",
        "all",
        "any",
        "because",
        "while",
        "also",
        "if",
        "else",
        "get",
        "set",
    }
)

# Synonym map: normalises related terms to a common keyword for scoring
SYNONYMS: dict[str, list[str]] = {
    "react": ["reactjs", "react.js"],
    "vue": ["vuejs", "vue.js"],
    "angular": ["angularjs", "ng"],
    "node": ["nodejs", "node.js"],
    "next": ["nextjs", "next.js"],
    "nuxt": ["nuxtjs", "nuxt.js"],
    "svelte": ["sveltekit"],
    "auth": ["authentication", "authorization", "oauth", "jwt", "login", "signup"],
    "k8s": ["kubernetes"],
    "db": ["database"],
    "pg": ["postgres", "postgresql"],
    "mongo": ["mongodb"],
    "redis": ["cache", "caching"],
    "docker": ["container", "containers", "containerize"],
    "ci": ["cicd", "ci/cd", "pipeline"],
    "ts": ["typescript"],
    "js": ["javascript"],
    "py": ["python"],
    "api": ["rest", "restful", "graphql", "endpoint", "endpoints"],
    "test": ["testing", "tests", "jest", "pytest", "vitest", "spec"],
    "deploy": ["deployment", "deploying", "release"],
    "git": ["github", "gitlab", "version control"],
    "llm": ["ai", "ml", "openai", "anthropic", "claude", "gpt", "chatgpt"],
}

# Reverse synonym lookup: expanded_term -> canonical_term
_REVERSE_SYNONYMS: dict[str, str] = {}
for _canonical, _variants in SYNONYMS.items():
    for _variant in _variants:
        _REVERSE_SYNONYMS[_variant] = _canonical


@dataclass
class SkillCategory:
    """Metadata for a skill category (directory)."""

    name: str
    count: int
    description: str | None = None


@dataclass
class SkillSummary:
    """Lightweight metadata for a skill, without full content."""

    id: str  # '{category}/{skill_name}'
    name: str  # filename stem (e.g. 'alpine-js')
    category: str  # parent directory name
    description: str  # first prose paragraph after the blockquote
    source: str | None = None  # extracted from "> Source:" line


@dataclass
class SkillDetail(SkillSummary):
    """Full skill data including the raw markdown content."""

    content: str = ""


@dataclass
class SkillSuggestion:
    """A scored skill suggestion for a task description."""

    skill: SkillSummary
    relevance_score: float  # 0.0 – 1.0
    reason: str  # human-readable match explanation


# Internal index entry (not exposed to callers)
@dataclass
class _IndexEntry:
    summary: SkillSummary
    file_path: Path
    # Pre-tokenised fields for fast search
    name_tokens: frozenset[str] = field(default_factory=frozenset)
    description_tokens: frozenset[str] = field(default_factory=frozenset)


class SkillsService:
    """
    In-memory index of skills from a directory tree.

    Directory layout expected::

        <skills_base_path>/
            <category>/
                <skill-name>.md
                ...
            ...

    Call ``build_index()`` (or let ``__init__`` do it) before using any
    query methods.  All methods return empty results gracefully when the
    skills path is absent.
    """

    def __init__(
        self,
        skills_base_path: Path = DEFAULT_SKILLS_PATH,
        cache_path: Path = DEFAULT_CACHE_PATH,
    ) -> None:
        self._base_path = skills_base_path
        self._cache_path = cache_path
        # category name -> list of index entries
        self._index: dict[str, list[_IndexEntry]] = {}
        self._built = False
        self.build_index()

    # ------------------------------------------------------------------
    # Index construction
    # ------------------------------------------------------------------

    def build_index(self) -> None:
        """Build the in-memory index, loading from cache when possible."""
        if not self._base_path.exists() or not self._base_path.is_dir():
            logger.warning(
                "Skills directory not found at %s – skills system will return empty results",
                self._base_path,
            )
            self._built = True
            return

        # Try loading from cache first
        if self._load_cache():
            return

        # Cache miss — full scan
        self._scan_and_build()

        # Persist for next startup
        self._save_cache()

    def _get_dir_mtime(self) -> float:
        """Get the newest mtime across the skills base dir and its category subdirs."""
        newest = os.path.getmtime(self._base_path)
        try:
            for entry in os.scandir(self._base_path):
                if entry.is_dir():
                    newest = max(newest, entry.stat().st_mtime)
        except OSError:
            pass
        return newest

    def _load_cache(self) -> bool:
        """Try to load the index from the JSON cache. Returns True on success."""
        try:
            if not self._cache_path.exists():
                logger.info("No skills cache found — will scan directory")
                return False

            cache_mtime = os.path.getmtime(self._cache_path)
            dir_mtime = self._get_dir_mtime()

            if dir_mtime > cache_mtime:
                logger.info(
                    "Skills cache is stale (dir mtime %.0f > cache mtime %.0f) — rebuilding",
                    dir_mtime,
                    cache_mtime,
                )
                return False

            t0 = time.monotonic()
            with open(self._cache_path, encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict) or data.get("version") != _CACHE_VERSION:
                logger.info("Skills cache version mismatch — rebuilding")
                return False

            if data.get("base_path") != str(self._base_path):
                logger.info("Skills cache base path mismatch — rebuilding")
                return False

            self._index = self._index_from_json(data["index"])
            self._built = True
            total = sum(len(entries) for entries in self._index.values())
            elapsed = time.monotonic() - t0
            logger.info(
                "SkillsService loaded from cache: %d categories, %d skills in %.2fs",
                len(self._index),
                total,
                elapsed,
            )
            return True

        except Exception as exc:
            logger.warning("Failed to load skills cache: %s — rebuilding", exc)
            return False

    def _save_cache(self) -> None:
        """Persist the current index to the JSON cache."""
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": _CACHE_VERSION,
                "base_path": str(self._base_path),
                "index": self._index_to_json(),
            }
            with open(self._cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            # 0600 — the cache is read back at startup; keep it owner-only.
            with contextlib.suppress(OSError):
                self._cache_path.chmod(0o600)
            logger.info("Skills cache saved to %s", self._cache_path)
        except Exception as exc:
            logger.warning("Failed to save skills cache: %s", exc)

    def _index_to_json(self) -> dict[str, list[dict[str, object]]]:
        """Serialize the in-memory index to JSON-safe primitives.

        ``frozenset`` token fields become sorted lists; ``Path`` becomes a
        string; ``SkillSummary`` becomes a plain dict.
        """
        out: dict[str, list[dict[str, object]]] = {}
        for category, entries in self._index.items():
            out[category] = [
                {
                    "summary": asdict(e.summary),
                    "file_path": str(e.file_path),
                    "name_tokens": sorted(e.name_tokens),
                    "description_tokens": sorted(e.description_tokens),
                }
                for e in entries
            ]
        return out

    @staticmethod
    def _index_from_json(
        raw: dict[str, list[dict[str, Any]]],
    ) -> dict[str, list["_IndexEntry"]]:
        """Reconstruct the index dataclasses from JSON primitives."""
        index: dict[str, list[_IndexEntry]] = {}
        for category, entries in raw.items():
            index[category] = [
                _IndexEntry(
                    summary=SkillSummary(**e["summary"]),
                    file_path=Path(e["file_path"]),
                    name_tokens=frozenset(e["name_tokens"]),
                    description_tokens=frozenset(e["description_tokens"]),
                )
                for e in entries
            ]
        return index

    def _scan_and_build(self) -> None:
        """Full scan of the skills directory (the slow path)."""
        t0 = time.monotonic()
        self._index = {}
        total = 0

        for category_dir in sorted(self._base_path.iterdir()):
            if not category_dir.is_dir():
                continue
            category = category_dir.name
            entries: list[_IndexEntry] = []

            for skill_file in sorted(category_dir.glob("*.md")):
                try:
                    entry = self._parse_skill_file(skill_file, category)
                    entries.append(entry)
                    total += 1
                except Exception as exc:
                    logger.debug("Failed to parse skill file %s: %s", skill_file, exc)

            if entries:
                self._index[category] = entries

        self._built = True
        elapsed = time.monotonic() - t0
        logger.info(
            "SkillsService index built from scan: %d categories, %d skills in %.1fs",
            len(self._index),
            total,
            elapsed,
        )

    def _parse_skill_file(self, path: Path, category: str) -> _IndexEntry:
        """Parse a skill .md file and return an index entry."""
        name = path.stem  # filename without .md
        content = path.read_text(encoding="utf-8", errors="replace")
        description, source = self._extract_metadata(content)

        summary = SkillSummary(
            id=f"{category}/{name}",
            name=name,
            category=category,
            description=description,
            source=source,
        )

        return _IndexEntry(
            summary=summary,
            file_path=path,
            name_tokens=self._tokenize(name.replace("-", " ").replace("_", " ")),
            description_tokens=self._tokenize(description),
        )

    @staticmethod
    def _extract_metadata(content: str) -> tuple[str, str | None]:
        """
        Extract (description, source) from skill markdown content.

        Expected structure::

            # skill-name

            > Source: <url> | Stars: ...

            ---

            # Section Title

            First prose paragraph (used as description).

        Returns the first non-empty, non-heading paragraph that appears
        after the ``---`` divider.  Falls back to an empty string if
        nothing suitable is found.
        """
        source: str | None = None
        description = ""

        # Extract source URL from the blockquote
        source_match = re.search(r">\s*Source:\s*(\S+)", content)
        if source_match:
            raw_source = source_match.group(1).rstrip("|").rstrip()
            # Strip markdown link syntax [text](url) -> url
            link_match = re.match(r"\[.*?\]\((.*?)\)", raw_source)
            source = link_match.group(1) if link_match else raw_source

        # Split on the first horizontal rule to get the "body"
        parts = re.split(r"^\s*---\s*$", content, maxsplit=1, flags=re.MULTILINE)
        body = parts[1] if len(parts) > 1 else content

        # Find the first non-empty paragraph that is not a heading
        for paragraph in re.split(r"\n{2,}", body):
            stripped = paragraph.strip()
            if not stripped:
                continue
            # Skip headings (lines beginning with #)
            if stripped.startswith("#"):
                continue
            # Skip block-level markdown artefacts
            if stripped.startswith(("```", ">", "---", "===", "---")):
                continue
            # Use the first line of the paragraph as the description
            first_line = stripped.splitlines()[0].strip()
            if len(first_line) > 10:
                description = first_line
                break

        return description, source

    # ------------------------------------------------------------------
    # Public query API
    # ------------------------------------------------------------------

    def list_categories(self) -> list[SkillCategory]:
        """Return all categories with their skill counts."""
        return [
            SkillCategory(name=cat, count=len(entries))
            for cat, entries in sorted(self._index.items())
        ]

    def list_skills(self, category: str) -> list[SkillSummary]:
        """Return all skill summaries for a category."""
        entries = self._index.get(category, [])
        return [e.summary for e in entries]

    def search_skills(
        self,
        query: str,
        category: str | None = None,
        limit: int = 50,
    ) -> list[SkillSummary]:
        """
        Keyword search across skill name and description fields.

        Matching is case-insensitive substring search on the raw query
        as well as token-level matching after stop-word removal.
        """
        if not query or not query.strip():
            return []

        query_lower = query.lower().strip()
        query_tokens = self._tokenize(query)

        candidates = self._get_candidates(category)
        results: list[tuple[int, SkillSummary]] = []

        for entry in candidates:
            score = 0
            name_lower = entry.summary.name.lower().replace("-", " ").replace("_", " ")
            desc_lower = entry.summary.description.lower()

            # Substring match on raw query
            if query_lower in name_lower:
                score += 4
            if query_lower in desc_lower:
                score += 2

            # Token-level match
            name_matches = query_tokens & entry.name_tokens
            desc_matches = query_tokens & entry.description_tokens
            score += len(name_matches) * 3
            score += len(desc_matches)

            if score > 0:
                results.append((score, entry.summary))

        results.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in results[:limit]]

    def get_skill(self, category: str, name: str) -> SkillSummary | None:
        """Return skill summary for a specific category/name, or None."""
        entry = self._find_entry(category, name)
        return entry.summary if entry else None

    def get_skill_content(self, category: str, name: str) -> str | None:
        """Return the full markdown content of a skill file, or None."""
        entry = self._find_entry(category, name)
        if entry is None:
            return None
        try:
            return entry.file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Failed to read skill file %s: %s", entry.file_path, exc)
            return None

    def get_skill_detail(self, category: str, name: str) -> SkillDetail | None:
        """Return SkillDetail (summary + full content) for a skill, or None."""
        entry = self._find_entry(category, name)
        if entry is None:
            return None
        content = self.get_skill_content(category, name) or ""
        return SkillDetail(
            id=entry.summary.id,
            name=entry.summary.name,
            category=entry.summary.category,
            description=entry.summary.description,
            source=entry.summary.source,
            content=content,
        )

    def suggest_skills(
        self,
        task_description: str,
        max_results: int = 10,
    ) -> list[SkillSuggestion]:
        """
        Auto-suggest skills relevant to a task description.

        Scoring algorithm:
        - Exact name match:            5 pts
        - Name contains keyword:       3 pts per unique keyword
        - Category name matches:       2 pts per unique keyword
        - Description contains kw:     1 pt  per unique keyword

        Synonyms are expanded before scoring.
        """
        if not task_description or len(task_description.strip()) < 10:
            return []

        keywords = self._tokenize(task_description)
        expanded = self._expand_synonyms(keywords)

        if not expanded:
            return []

        scored: list[tuple[float, list[str], SkillSummary]] = []

        for category, entries in self._index.items():
            cat_tokens = self._tokenize(category.replace("-", " ").replace("_", " "))
            cat_matches = expanded & cat_tokens

            for entry in entries:
                score = 0
                matched_keywords: list[str] = []

                name_lower = entry.summary.name.lower().replace("-", " ").replace("_", " ")
                name_exact = name_lower.replace(" ", "")

                # Exact name match (after normalisation)
                for kw in expanded:
                    if kw == name_lower or kw == name_exact:
                        score += 5
                        matched_keywords.append(f"exact:{kw}")

                # Name token overlap
                name_matches = expanded & entry.name_tokens
                for kw in name_matches:
                    if f"exact:{kw}" not in matched_keywords:
                        score += 3
                        matched_keywords.append(f"name:{kw}")

                # Category name overlap
                for kw in cat_matches:
                    score += 2
                    if f"category:{kw}" not in matched_keywords:
                        matched_keywords.append(f"category:{kw}")

                # Description token overlap
                desc_matches = expanded & entry.description_tokens
                for kw in desc_matches:
                    score += 1
                    if f"desc:{kw}" not in matched_keywords:
                        matched_keywords.append(f"desc:{kw}")

                if score > 0:
                    scored.append((score, matched_keywords, entry.summary))

        scored.sort(key=lambda x: x[0], reverse=True)

        suggestions: list[SkillSuggestion] = []
        max_score = scored[0][0] if scored else 1.0

        for raw_score, matched_kws, summary in scored[:max_results]:
            relevance = min(round(raw_score / max(max_score, 1.0), 3), 1.0)
            # Build human-readable reason from matched keyword labels
            kw_labels = sorted({kw.split(":", 1)[1] for kw in matched_kws})
            reason = "Matched: " + ", ".join(kw_labels[:5])
            suggestions.append(
                SkillSuggestion(
                    skill=summary,
                    relevance_score=relevance,
                    reason=reason,
                )
            )

        return suggestions

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_entry(self, category: str, name: str) -> _IndexEntry | None:
        """Locate an index entry by category and skill name."""
        for entry in self._index.get(category, []):
            if entry.summary.name == name:
                return entry
        return None

    def _get_candidates(self, category: str | None) -> list[_IndexEntry]:
        """Return all index entries, optionally filtered to a category."""
        if category:
            return self._index.get(category, [])
        return [entry for entries in self._index.values() for entry in entries]

    @staticmethod
    def _tokenize(text: str) -> frozenset[str]:
        """
        Lowercase, strip punctuation, split into tokens, remove stop words.
        """
        # Replace hyphens/underscores with spaces before stripping punctuation
        text = text.replace("-", " ").replace("_", " ")
        # Remove all remaining punctuation except spaces
        text = text.translate(str.maketrans("", "", string.punctuation))
        tokens = text.lower().split()
        return frozenset(t for t in tokens if t not in STOP_WORDS and len(t) > 1)

    @staticmethod
    def _expand_synonyms(tokens: frozenset[str]) -> frozenset[str]:
        """
        Expand a token set with synonyms so that, e.g., 'auth' also
        covers 'authentication', 'jwt', etc.
        """
        expanded = set(tokens)
        for token in tokens:
            canonical = _REVERSE_SYNONYMS.get(token, token)
            expanded.add(canonical)
            # Also add all variants of the canonical term
            for variant in SYNONYMS.get(canonical, []):
                expanded.add(variant)
            # And all variants of the original token if it is a canonical key
            for variant in SYNONYMS.get(token, []):
                expanded.add(variant)
        return frozenset(expanded)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_skills_service: SkillsService | None = None


def get_skills_service() -> SkillsService:
    """Return the global SkillsService singleton, creating it if needed."""
    global _skills_service
    if _skills_service is None:
        _skills_service = SkillsService()
    return _skills_service


def init_skills_service(
    skills_base_path: Path = DEFAULT_SKILLS_PATH,
    cache_path: Path = DEFAULT_CACHE_PATH,
) -> SkillsService:
    """
    Initialise (or re-initialise) the global SkillsService singleton.

    Call this once at application startup to control the base path.
    """
    global _skills_service
    _skills_service = SkillsService(
        skills_base_path=skills_base_path,
        cache_path=cache_path,
    )
    return _skills_service
