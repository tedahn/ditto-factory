from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Skill:
    id: str
    name: str
    slug: str
    description: str
    content: str
    language: list[str] = field(default_factory=list)
    domain: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    org_id: str | None = None
    repo_pattern: str | None = None
    version: int = 1
    created_by: str = ""
    is_active: bool = True
    is_default: bool = False
    source_toolkit_id: str | None = None
    source_component_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class SkillVersion:
    id: str
    skill_id: str
    version: int
    content: str
    description: str
    changelog: str | None = None
    created_by: str = ""
    created_at: datetime | None = None


@dataclass
class AgentType:
    id: str
    name: str
    image: str
    description: str | None = None
    capabilities: list[str] = field(default_factory=list)
    resource_profile: dict = field(default_factory=dict)
    is_default: bool = False
    created_at: datetime | None = None


@dataclass
class SkillUsage:
    id: str
    skill_id: str
    thread_id: str
    job_id: str
    task_source: str
    repo_owner: str | None = None
    repo_name: str | None = None
    was_selected: bool = True
    exit_code: int | None = None
    commit_count: int | None = None
    pr_created: bool = False
    injected_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass
class SkillCreate:
    name: str
    slug: str
    description: str
    content: str
    language: list[str] = field(default_factory=list)
    domain: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    org_id: str | None = None
    repo_pattern: str | None = None
    is_default: bool = False
    created_by: str = ""


@dataclass
class SkillUpdate:
    content: str | None = None
    description: str | None = None
    language: list[str] | None = None
    domain: list[str] | None = None
    requires: list[str] | None = None
    tags: list[str] | None = None
    changelog: str | None = None
    updated_by: str = ""


@dataclass
class SkillFilters:
    language: list[str] | None = None
    domain: list[str] | None = None
    org_id: str | None = None
    is_active: bool = True


@dataclass
class ScoredSkill:
    skill: Skill
    score: float  # similarity score or tag match score


@dataclass
class ClassificationDiagnostics:
    """Captures classification reasoning for tracing."""
    method: str  # "semantic" or "tag_fallback"
    candidates_evaluated: int
    scores: list[dict]  # [{"skill_slug": "...", "score": 0.87, "boosted_score": 0.92}, ...]
    threshold: float
    embedding_cached: bool = False
    fallback_reason: str | None = None


@dataclass
class ClassificationResult:
    skills: list[Skill]
    agent_type: str = "general"
    task_embedding: list[float] | None = None
    diagnostics: ClassificationDiagnostics | None = None


@dataclass
class ResolvedAgent:
    image: str
    agent_type: str = "general"
    diagnostics: dict = field(default_factory=dict)


@dataclass
class SkillMetrics:
    skill_slug: str
    usage_count: int
    success_rate: float
    avg_commits: float
    pr_creation_rate: float
