"""
REST API endpoints for the Skill Hotloading System.

  POST   /api/v1/skills                  - Create a skill
  GET    /api/v1/skills                  - List skills (filterable)
  GET    /api/v1/skills/{slug}           - Get skill by slug
  PUT    /api/v1/skills/{slug}           - Update skill (new version)
  DELETE /api/v1/skills/{slug}           - Soft-delete skill
  GET    /api/v1/skills/{slug}/versions  - List versions
  POST   /api/v1/skills/{slug}/rollback  - Rollback to version
  POST   /api/v1/skills/search           - Search skills
  GET    /api/v1/skills/{slug}/metrics   - Usage metrics (stub)
  GET    /api/v1/agent-types             - List agent types
  POST   /api/v1/agent-types             - Create agent type
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic request/response schemas
# ---------------------------------------------------------------------------

class SkillCreateRequest(BaseModel):
    name: str
    slug: str
    description: str
    content: str
    language: list[str] = []
    domain: list[str] = []
    requires: list[str] = []
    tags: list[str] = []
    org_id: str | None = None
    repo_pattern: str | None = None
    is_default: bool = False
    created_by: str = ""


class SkillUpdateRequest(BaseModel):
    content: str | None = None
    description: str | None = None
    language: list[str] | None = None
    domain: list[str] | None = None
    requires: list[str] | None = None
    tags: list[str] | None = None
    changelog: str | None = None
    updated_by: str = ""


class SkillResponse(BaseModel):
    id: str
    name: str
    slug: str
    description: str
    content: str
    language: list[str]
    domain: list[str]
    requires: list[str]
    tags: list[str]
    org_id: str | None
    version: int
    is_active: bool
    is_default: bool
    created_at: str | None
    updated_at: str | None


class SkillListResponse(BaseModel):
    skills: list[SkillResponse]
    total: int
    page: int
    per_page: int


class SkillSearchRequest(BaseModel):
    query: str | None = None  # For semantic search (Phase 2)
    language: list[str] | None = None
    domain: list[str] | None = None
    tags: list[str] | None = None
    limit: int = 10
    min_similarity: float = 0.5


class SkillSearchResult(BaseModel):
    slug: str
    name: str
    similarity: float
    usage_count: int = 0
    success_rate: float = 0.0


class SkillSearchResponse(BaseModel):
    skills: list[SkillSearchResult]


class RollbackRequest(BaseModel):
    target_version: int


class SkillVersionResponse(BaseModel):
    version: int
    changelog: str | None
    created_by: str
    created_at: str | None


class SkillMetricsResponse(BaseModel):
    skill_slug: str
    usage_count: int = 0
    success_rate: float = 0.0
    avg_commits: float = 0.0
    pr_creation_rate: float = 0.0


class AgentTypeCreateRequest(BaseModel):
    name: str
    image: str
    description: str | None = None
    capabilities: list[str] = []
    resource_profile: dict = {}


class AgentTypeResponse(BaseModel):
    name: str
    image: str
    description: str | None
    capabilities: list[str]
    is_default: bool


class CandidateInfoResponse(BaseModel):
    name: str
    capabilities: list[str]
    coverage: int
    covers_all: bool = False


class ResolutionEventResponse(BaseModel):
    thread_id: str
    timestamp: str | None
    required_capabilities: list[str]
    candidates_considered: list[CandidateInfoResponse]
    selected: str
    reason: str


class AgentTypeSummaryResponse(BaseModel):
    id: str
    name: str
    image: str
    description: str | None = None
    capabilities: list[str] = []
    is_default: bool = False
    created_at: str | None = None
    job_count: int = 0
    recent_resolutions: list[ResolutionEventResponse] = []
    mapped_skills: list[str] = []


# ---------------------------------------------------------------------------
# Dependency injection helpers (overridden in main.py / tests)
# ---------------------------------------------------------------------------

def get_skill_registry():
    """Provide the skill registry -- overridden via dependency_overrides."""
    raise NotImplementedError("Must be overridden via dependency_overrides")


def get_performance_tracker():
    """Provide the performance tracker -- overridden via dependency_overrides."""
    raise NotImplementedError("Must be overridden via dependency_overrides")


def get_state_backend():
    """Provide the state backend -- overridden via dependency_overrides."""
    raise NotImplementedError("Must be overridden via dependency_overrides")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------------------
# Skill CRUD
# ---------------------------------------------------------------------------

@router.post("/skills", response_model=SkillResponse, status_code=201)
async def create_skill(
    body: SkillCreateRequest,
    registry=Depends(get_skill_registry),
):
    """Register a new skill in the registry."""
    from controller.skills.models import SkillCreate
    skill = await registry.create(SkillCreate(
        name=body.name,
        slug=body.slug,
        description=body.description,
        content=body.content,
        language=body.language or [],
        domain=body.domain or [],
        requires=body.requires or [],
        tags=body.tags or [],
        org_id=body.org_id,
        repo_pattern=body.repo_pattern,
        is_default=body.is_default or False,
        created_by=body.created_by or "",
    ))
    return _skill_to_response(skill)


@router.get("/skills", response_model=SkillListResponse)
async def list_skills(
    language: str | None = Query(default=None, description="Filter by language"),
    domain: str | None = Query(default=None, description="Filter by domain"),
    page: int = Query(default=1, ge=1, description="Page number"),
    per_page: int = Query(default=20, ge=1, le=100, description="Items per page"),
    registry=Depends(get_skill_registry),
):
    """List skills with optional filtering and pagination."""
    filters: dict[str, Any] = {}
    if language:
        filters["language"] = language
    if domain:
        filters["domain"] = domain

    all_skills = await registry.list_all()

    # Apply filters in-memory
    if filters.get("language"):
        all_skills = [s for s in all_skills if getattr(s, "language", None) == filters["language"]]
    if filters.get("domain"):
        all_skills = [s for s in all_skills if getattr(s, "domain", None) == filters["domain"]]

    total = len(all_skills)
    start = (page - 1) * per_page
    skills = all_skills[start : start + per_page]

    return SkillListResponse(
        skills=[_skill_to_response(s) for s in skills],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/skills/{slug}", response_model=SkillResponse)
async def get_skill(
    slug: str,
    registry=Depends(get_skill_registry),
):
    """Get a skill by its slug."""
    skill = await registry.get(slug)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{slug}' not found")
    return _skill_to_response(skill)


@router.put("/skills/{slug}", response_model=SkillResponse)
async def update_skill(
    slug: str,
    body: SkillUpdateRequest,
    registry=Depends(get_skill_registry),
):
    """Update a skill, creating a new version."""
    existing = await registry.get(slug)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Skill '{slug}' not found")

    from controller.skills.models import SkillUpdate
    update_obj = SkillUpdate(
        content=body.content,
        description=body.description,
        language=body.language,
        domain=body.domain,
        requires=body.requires,
        tags=body.tags,
        changelog=body.changelog,
        updated_by=body.updated_by or "",
    )
    skill = await registry.update(slug, update_obj)
    return _skill_to_response(skill)


@router.delete("/skills/{slug}", status_code=204)
async def delete_skill(
    slug: str,
    registry=Depends(get_skill_registry),
):
    """Soft-delete a skill."""
    existing = await registry.get(slug)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Skill '{slug}' not found")

    await registry.delete(slug)
    return None


# ---------------------------------------------------------------------------
# Versions & Rollback
# ---------------------------------------------------------------------------

@router.get("/skills/{slug}/versions", response_model=list[SkillVersionResponse])
async def list_versions(
    slug: str,
    registry=Depends(get_skill_registry),
):
    """List all versions of a skill."""
    existing = await registry.get(slug)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Skill '{slug}' not found")

    versions = await registry.list_versions(slug)
    return [
        SkillVersionResponse(
            version=v.version,
            changelog=getattr(v, "changelog", None),
            created_by=getattr(v, "created_by", ""),
            created_at=_format_dt(getattr(v, "created_at", None)),
        )
        for v in versions
    ]


@router.post("/skills/{slug}/rollback", response_model=SkillResponse)
async def rollback_skill(
    slug: str,
    body: RollbackRequest,
    registry=Depends(get_skill_registry),
):
    """Rollback a skill to a specific version."""
    existing = await registry.get(slug)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Skill '{slug}' not found")

    # Get the target version content and restore it via update
    versions = await registry.get_versions(slug)
    target = next((v for v in versions if v.version == body.target_version), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Target version not found")

    from controller.skills.models import SkillUpdate
    skill = await registry.update(slug, SkillUpdate(
        content=target.content,
        description=target.description,
        changelog=f"Rollback to version {body.target_version}",
    ))
    return _skill_to_response(skill)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@router.post("/skills/search", response_model=SkillSearchResponse)
async def search_skills(
    body: SkillSearchRequest,
    registry=Depends(get_skill_registry),
):
    """Search skills by tags, language, domain, or semantic query (Phase 2)."""
    # Use tag-based search (Phase 1) — semantic search requires embedding provider
    language_filter = [body.language] if body.language else None
    domain_filter = [body.domain] if body.domain else None
    results = await registry.search_by_tags(
        language=language_filter,
        domain=domain_filter,
        limit=body.limit or 20,
    )
    # Filter by tags if provided
    if body.tags:
        tag_set = set(body.tags)
        results = [r for r in results if tag_set & set(r.tags)]
    # Filter by query (simple substring match)
    if body.query:
        q = body.query.lower()
        results = [r for r in results if q in r.name.lower() or q in r.description.lower()]

    return SkillSearchResponse(
        skills=[
            SkillSearchResult(
                slug=r.slug,
                name=r.name,
                similarity=1.0,
                usage_count=getattr(r, "usage_count", 0),
                success_rate=getattr(r, "success_rate", 0.0),
            )
            for r in results
        ]
    )


# ---------------------------------------------------------------------------
# Reindex (batch re-embedding)
# ---------------------------------------------------------------------------


class ReindexResponse(BaseModel):
    reindexed: int
    failed: int


@router.post("/skills/reindex", response_model=ReindexResponse, status_code=202)
async def reindex_skills(
    registry=Depends(get_skill_registry),
):
    """Re-embed all active skills. Useful after changing embedding provider."""
    all_skills = await registry.list_all()
    reindexed = 0
    failed = 0
    for skill in all_skills:
        try:
            if registry._embedder:
                text = f"{skill.name} {skill.description} {skill.content}"
                embedding = await registry._embedder.embed(text)
                await registry.store_embedding(skill.slug, embedding)
                reindexed += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    return ReindexResponse(reindexed=reindexed, failed=failed)


# ---------------------------------------------------------------------------
# Metrics (stub for Phase 3)
# ---------------------------------------------------------------------------

@router.get("/skills/{slug}/metrics", response_model=SkillMetricsResponse)
async def get_skill_metrics(
    slug: str,
    registry=Depends(get_skill_registry),
    tracker=Depends(get_performance_tracker),
):
    """Get usage metrics for a skill."""
    existing = await registry.get(slug)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Skill '{slug}' not found")

    metrics = await tracker.get_skill_metrics(slug)
    if not metrics:
        return SkillMetricsResponse(skill_slug=slug)
    return SkillMetricsResponse(
        skill_slug=metrics.skill_slug,
        usage_count=metrics.usage_count,
        success_rate=metrics.success_rate,
        avg_commits=metrics.avg_commits,
        pr_creation_rate=metrics.pr_creation_rate,
    )


# ---------------------------------------------------------------------------
# Agent Types
# ---------------------------------------------------------------------------

@router.get("/agent-types", response_model=list[AgentTypeResponse])
async def list_agent_types(
    registry=Depends(get_skill_registry),
):
    """List all registered agent types."""
    agent_types = await registry.list_agent_types()
    return [
        AgentTypeResponse(
            name=at.name,
            image=at.image,
            description=getattr(at, "description", None),
            capabilities=getattr(at, "capabilities", []),
            is_default=getattr(at, "is_default", False),
        )
        for at in agent_types
    ]


@router.post("/agent-types", response_model=AgentTypeResponse, status_code=201)
async def create_agent_type(
    body: AgentTypeCreateRequest,
    registry=Depends(get_skill_registry),
):
    """Register a new agent type."""
    agent_type = await registry.create_agent_type(
        name=body.name,
        image=body.image,
        description=body.description,
        capabilities=body.capabilities,
        resource_profile=body.resource_profile,
    )
    return AgentTypeResponse(
        name=agent_type.name,
        image=agent_type.image,
        description=getattr(agent_type, "description", None),
        capabilities=getattr(agent_type, "capabilities", []),
        is_default=getattr(agent_type, "is_default", False),
    )


@router.get("/agents/types/summary", response_model=list[AgentTypeSummaryResponse])
async def agent_types_summary(
    registry=Depends(get_skill_registry),
    state=Depends(get_state_backend),
):
    """List agent types with usage stats and recent resolution events."""
    agent_types = await registry.list_agent_types()
    all_skills = await registry.list_all()

    results = []
    for at in agent_types:
        at_caps = set(getattr(at, "capabilities", []))

        # Find skills whose requires overlap with this type's capabilities
        mapped = [
            s.slug for s in all_skills
            if s.requires and at_caps and set(s.requires) & at_caps
        ]

        # Count jobs and get recent resolutions
        job_count = 0
        recent_resolutions = []
        try:
            jobs = await state.list_jobs_by_agent_type(at.name, limit=20)
            job_count = await state.count_jobs_by_agent_type(at.name)
            for job in jobs:
                if job.resolution_diagnostics:
                    recent_resolutions.append(ResolutionEventResponse(
                        thread_id=job.thread_id,
                        timestamp=job.started_at.isoformat() if job.started_at else None,
                        required_capabilities=job.resolution_diagnostics.get("required_capabilities", []),
                        candidates_considered=[
                            CandidateInfoResponse(**c)
                            for c in job.resolution_diagnostics.get("candidates_considered", [])
                        ],
                        selected=job.resolution_diagnostics.get("selected", ""),
                        reason=job.resolution_diagnostics.get("reason", ""),
                    ))
        except Exception:
            logger.exception("Failed to fetch job stats for agent type %s", at.name)

        results.append(AgentTypeSummaryResponse(
            id=at.id,
            name=at.name,
            image=at.image,
            description=getattr(at, "description", None),
            capabilities=getattr(at, "capabilities", []),
            is_default=getattr(at, "is_default", False),
            created_at=_format_dt(getattr(at, "created_at", None)),
            job_count=job_count,
            recent_resolutions=recent_resolutions,
            mapped_skills=mapped,
        ))

    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_dt(dt: Any) -> str | None:
    """Format a datetime to ISO string, handling None."""
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.isoformat()


def _skill_to_response(skill: Any) -> SkillResponse:
    """Convert an internal skill object to SkillResponse."""
    return SkillResponse(
        id=getattr(skill, "id", ""),
        name=skill.name,
        slug=skill.slug,
        description=getattr(skill, "description", ""),
        content=getattr(skill, "content", ""),
        language=getattr(skill, "language", []),
        domain=getattr(skill, "domain", []),
        requires=getattr(skill, "requires", []),
        tags=getattr(skill, "tags", []),
        org_id=getattr(skill, "org_id", None),
        version=getattr(skill, "version", 1),
        is_active=getattr(skill, "is_active", True),
        is_default=getattr(skill, "is_default", False),
        created_at=_format_dt(getattr(skill, "created_at", None)),
        updated_at=_format_dt(getattr(skill, "updated_at", None)),
    )
