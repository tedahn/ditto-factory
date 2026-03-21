"""Tests for skill registry data models."""

import pytest

from controller.skills.models import (
    Skill,
    SkillCreate,
    SkillFilters,
    SkillUpdate,
    ClassificationResult,
    ResolvedAgent,
    SkillMetrics,
    AgentType,
    SkillVersion,
    SkillUsage,
    ScoredSkill,
)


class TestSkillDataclass:
    def test_skill_creation_with_defaults(self):
        skill = Skill(
            id="sk-001",
            name="Python Linter",
            slug="python-linter",
            description="Lints Python code",
            content="# lint instructions",
        )
        assert skill.id == "sk-001"
        assert skill.name == "Python Linter"
        assert skill.slug == "python-linter"
        assert skill.language == []
        assert skill.domain == []
        assert skill.requires == []
        assert skill.tags == []
        assert skill.org_id is None
        assert skill.repo_pattern is None
        assert skill.version == 1
        assert skill.created_by == ""
        assert skill.is_active is True
        assert skill.is_default is False
        assert skill.created_at is None
        assert skill.updated_at is None

    def test_skill_creation_with_all_fields(self):
        skill = Skill(
            id="sk-002",
            name="React Reviewer",
            slug="react-reviewer",
            description="Reviews React components",
            content="# review steps",
            language=["typescript", "javascript"],
            domain=["frontend"],
            requires=["eslint"],
            tags=["react", "review"],
            org_id="org-123",
            repo_pattern="*/web-*",
            version=3,
            created_by="user-1",
            is_active=True,
            is_default=True,
        )
        assert skill.language == ["typescript", "javascript"]
        assert skill.domain == ["frontend"]
        assert skill.version == 3
        assert skill.is_default is True
        assert skill.org_id == "org-123"


class TestSkillCreate:
    def test_skill_create_defaults(self):
        create = SkillCreate(
            name="Test Skill",
            slug="test-skill",
            description="A test",
            content="# content",
        )
        assert create.language == []
        assert create.domain == []
        assert create.requires == []
        assert create.tags == []
        assert create.org_id is None
        assert create.repo_pattern is None
        assert create.is_default is False
        assert create.created_by == ""

    def test_skill_create_to_skill_mapping(self):
        create = SkillCreate(
            name="Go Backend",
            slug="go-backend",
            description="Go backend skill",
            content="# go instructions",
            language=["go"],
            domain=["backend"],
            tags=["go", "api"],
            created_by="admin",
        )
        skill = Skill(
            id="sk-new",
            name=create.name,
            slug=create.slug,
            description=create.description,
            content=create.content,
            language=create.language,
            domain=create.domain,
            requires=create.requires,
            tags=create.tags,
            org_id=create.org_id,
            repo_pattern=create.repo_pattern,
            is_default=create.is_default,
            created_by=create.created_by,
        )
        assert skill.name == "Go Backend"
        assert skill.language == ["go"]
        assert skill.domain == ["backend"]
        assert skill.created_by == "admin"


class TestSkillUpdate:
    def test_skill_update_all_none(self):
        update = SkillUpdate()
        assert update.content is None
        assert update.description is None
        assert update.language is None
        assert update.domain is None
        assert update.requires is None
        assert update.tags is None
        assert update.changelog is None
        assert update.updated_by == ""

    def test_skill_update_partial(self):
        update = SkillUpdate(content="new content", changelog="fixed typo")
        assert update.content == "new content"
        assert update.changelog == "fixed typo"
        assert update.description is None


class TestSkillFilters:
    def test_filters_all_none(self):
        filters = SkillFilters()
        assert filters.language is None
        assert filters.domain is None
        assert filters.org_id is None
        assert filters.is_active is True

    def test_filters_with_values(self):
        filters = SkillFilters(
            language=["python", "rust"],
            domain=["backend"],
            org_id="org-456",
            is_active=False,
        )
        assert filters.language == ["python", "rust"]
        assert filters.org_id == "org-456"
        assert filters.is_active is False


class TestClassificationResult:
    def test_classification_defaults(self):
        result = ClassificationResult(skills=[])
        assert result.skills == []
        assert result.agent_type == "general"
        assert result.task_embedding is None

    def test_classification_with_skills(self):
        skill = Skill(
            id="sk-1",
            name="Test",
            slug="test",
            description="test",
            content="content",
        )
        result = ClassificationResult(
            skills=[skill],
            agent_type="frontend",
        )
        assert len(result.skills) == 1
        assert result.agent_type == "frontend"


class TestResolvedAgent:
    def test_resolved_agent_defaults(self):
        agent = ResolvedAgent(image="agent:v1")
        assert agent.image == "agent:v1"
        assert agent.agent_type == "general"

    def test_resolved_agent_custom_type(self):
        agent = ResolvedAgent(image="agent:v2", agent_type="gpu")
        assert agent.agent_type == "gpu"


class TestSkillMetrics:
    def test_metrics_creation(self):
        metrics = SkillMetrics(
            skill_slug="python-linter",
            usage_count=42,
            success_rate=0.95,
            avg_commits=2.3,
            pr_creation_rate=0.88,
        )
        assert metrics.skill_slug == "python-linter"
        assert metrics.usage_count == 42
        assert metrics.success_rate == 0.95


class TestScoredSkill:
    def test_scored_skill(self):
        skill = Skill(
            id="sk-1",
            name="Test",
            slug="test",
            description="test",
            content="content",
        )
        scored = ScoredSkill(skill=skill, score=0.87)
        assert scored.score == 0.87
        assert scored.skill.id == "sk-1"
