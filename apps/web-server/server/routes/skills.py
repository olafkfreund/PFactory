"""
Skills API routes.

Exposes the SkillsService through a REST API for browsing, searching,
and suggesting skills from the local skills/ directory (or wherever
APP_SKILLS_PATH points).
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..services.skills_service import get_skills_service

router = APIRouter()


# --------------------------------------------------------------------------
# Response Models
# --------------------------------------------------------------------------


class SkillCategory(BaseModel):
    """Summary metadata for a skill category."""

    name: str = Field(..., description="Category directory name")
    count: int = Field(..., description="Number of skills in this category")
    description: Optional[str] = Field(None, description="Optional category description")


class SkillSummary(BaseModel):
    """Lightweight skill metadata, without full content."""

    id: str = Field(..., description="Unique skill identifier: '{category}/{skill_name}'")
    name: str = Field(..., description="Skill file stem (e.g. 'alpine-js')")
    category: str = Field(..., description="Parent category name")
    description: str = Field(..., description="First prose paragraph from the skill file")
    source: Optional[str] = Field(None, description="Source URL extracted from skill metadata")


class SkillDetail(SkillSummary):
    """Full skill data including the raw markdown content."""

    content: str = Field(..., description="Full markdown content of the skill file")


class SkillSuggestion(BaseModel):
    """A scored skill suggestion derived from a task description."""

    skill: SkillSummary
    relevance_score: float = Field(..., ge=0.0, le=1.0, description="Relevance score (0-1)")
    reason: str = Field(..., description="Human-readable explanation of why this skill was matched")


class PaginatedSkillList(BaseModel):
    """Paginated list of skill summaries."""

    items: list[SkillSummary]
    total: int = Field(..., description="Total number of skills matching the query")
    page: int = Field(..., description="Current page (1-based)")
    limit: int = Field(..., description="Items per page")
    has_next: bool = Field(..., description="Whether there is a next page")


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


@router.get(
    "/categories",
    response_model=list[SkillCategory],
    summary="List all skill categories",
    description="Returns all categories from the skills knowledge base with their skill counts.",
)
async def list_categories() -> list[SkillCategory]:
    """Return all skill categories with counts."""
    service = get_skills_service()
    service_categories = service.list_categories()
    return [
        SkillCategory(name=cat.name, count=cat.count, description=cat.description)
        for cat in service_categories
    ]


@router.get(
    "/list",
    response_model=PaginatedSkillList,
    summary="List skills for a category",
    description="Returns a paginated list of skills for a given category.",
)
async def list_skills(
    category: str = Query(..., description="Category name to list skills for"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    limit: int = Query(20, ge=1, le=100, description="Number of results per page"),
) -> PaginatedSkillList:
    """Return paginated skills for the given category."""
    service = get_skills_service()

    # Validate the category exists
    categories = {cat.name for cat in service.list_categories()}
    if category not in categories:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category '{category}' not found",
        )

    all_skills = service.list_skills(category)
    total = len(all_skills)

    offset = (page - 1) * limit
    page_items = all_skills[offset : offset + limit]

    return PaginatedSkillList(
        items=[
            SkillSummary(
                id=s.id,
                name=s.name,
                category=s.category,
                description=s.description,
                source=s.source,
            )
            for s in page_items
        ],
        total=total,
        page=page,
        limit=limit,
        has_next=(offset + limit) < total,
    )


@router.get(
    "/search",
    response_model=PaginatedSkillList,
    summary="Search skills by keyword",
    description="Full-text keyword search across skill names and descriptions.",
)
async def search_skills(
    q: str = Query(..., min_length=1, description="Search query string"),
    category: Optional[str] = Query(None, description="Optional category filter"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    limit: int = Query(20, ge=1, le=100, description="Number of results per page"),
) -> PaginatedSkillList:
    """Search skills by keyword, optionally filtered to a category."""
    service = get_skills_service()

    # Validate category if supplied
    if category is not None:
        categories = {cat.name for cat in service.list_categories()}
        if category not in categories:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Category '{category}' not found",
            )

    # Fetch all matching results (service handles the scoring/ranking)
    all_results = service.search_skills(q, category=category, limit=10_000)
    total = len(all_results)

    offset = (page - 1) * limit
    page_items = all_results[offset : offset + limit]

    return PaginatedSkillList(
        items=[
            SkillSummary(
                id=s.id,
                name=s.name,
                category=s.category,
                description=s.description,
                source=s.source,
            )
            for s in page_items
        ],
        total=total,
        page=page,
        limit=limit,
        has_next=(offset + limit) < total,
    )


@router.get(
    "/suggest",
    response_model=list[SkillSuggestion],
    summary="Auto-suggest skills for a task description",
    description=(
        "Analyses the task description and returns ranked skill suggestions "
        "using keyword matching and synonym expansion."
    ),
)
async def suggest_skills(
    task_description: str = Query(..., min_length=1, description="Task description text"),
    limit: int = Query(10, ge=1, le=50, description="Maximum number of suggestions to return"),
) -> list[SkillSuggestion]:
    """Return ranked skill suggestions based on a task description."""
    service = get_skills_service()
    suggestions = service.suggest_skills(task_description, max_results=limit)
    return [
        SkillSuggestion(
            skill=SkillSummary(
                id=s.skill.id,
                name=s.skill.name,
                category=s.skill.category,
                description=s.skill.description,
                source=s.skill.source,
            ),
            relevance_score=s.relevance_score,
            reason=s.reason,
        )
        for s in suggestions
    ]


@router.get(
    "/{category}/{skill_name}",
    response_model=SkillDetail,
    summary="Get full skill documentation",
    description="Returns the complete markdown content of a specific skill.",
)
async def get_skill_detail(
    category: str,
    skill_name: str,
) -> SkillDetail:
    """Return full skill documentation for a specific category/skill_name pair."""
    service = get_skills_service()
    detail = service.get_skill_detail(category, skill_name)

    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{category}/{skill_name}' not found",
        )

    return SkillDetail(
        id=detail.id,
        name=detail.name,
        category=detail.category,
        description=detail.description,
        source=detail.source,
        content=detail.content,
    )
