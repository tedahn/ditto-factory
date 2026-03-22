"""Tests for open-question resolutions: classifier overrides, skill scope tiers, reindex endpoint."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest

try:
    import aiosqlite

    HAS_AIOSQLITE = True
except ImportError:
    HAS_AIOSQLITE = False

try:
    from httpx import AsyncClient, ASGITransport

    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

pytestmark = [
    pytest.mark.skipif(not HAS_AIOSQLITE, reason="aiosqlite not installed"),
    pytest.mark.asyncio,
]

# ------------------------------------------------------------------
# Schema SQL
# ------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '[]',
    domain TEXT NOT NULL DEFAULT '[]',
    requires TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',
    org_id TEXT,
    repo_pattern TEXT,
    embedding TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS skill_versions (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL REFERENCES skills(id),
    version INTEGER NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    changelog TEXT,
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

from controller.skills.registry import SkillRegistry
from controller.skills.classifier import TaskClassifier
from controller.skills.models import (
    ClassificationResult,
    Skill,
    SkillCreate,
    SkillFilters,
)
from controller.models import TaskRequest


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "test_oq.db")
    async with aiosqlite.connect(path) as db:
        await db.executescript(SCHEMA_SQL)
    return path


@pytest.fixture
async def registry(db_path):
    return SkillRegistry(db_path=db_path)


@pytest.fixture
async def seed_skills(registry):
    """Create skills across different scopes for testing."""
    # Global skill
    await registry.create(SkillCreate(
        name="Global Lint",
        slug="global-lint",
        description="Global linting rules",
        content="Always run linter.",
        language=["python"],
        domain=["backend"],
        tags=["linting"],
        org_id=None,
        repo_pattern=None,
        created_by="test",
    ))
    # Org-scoped skill
    await registry.create(SkillCreate(
        name="Acme Style",
        slug="acme-style",
        description="Acme org coding style",
        content="Follow Acme conventions.",
        language=["python"],
        domain=["backend"],
        tags=["style"],
        org_id="acme",
        repo_pattern=None,
        created_by="test",
    ))
    # Repo-scoped skill
    await registry.create(SkillCreate(
        name="Frontend Debug",
        slug="frontend-debug",
        description="Debug React apps in acme/frontend-app",
        content="Use React DevTools.",
        language=["typescript"],
        domain=["frontend"],
        tags=["debugging"],
        org_id="acme",
        repo_pattern="acme/frontend-*",
        created_by="test",
    ))
    # Org-scoped skill with same slug as a repo-scoped one (for override testing)
    await registry.create(SkillCreate(
        name="Shared Debug Org",
        slug="shared-debug",
        description="Org-level debug skill",
        content="Generic debug approach.",
        language=["python"],
        domain=["backend"],
        tags=["debugging"],
        org_id="acme",
        repo_pattern=None,
        created_by="test",
    ))
    # Repo-scoped skill with same slug to test narrower scope wins
    await registry.create(SkillCreate(
        name="Shared Debug Repo",
        slug="shared-debug-repo",
        description="Repo-level debug skill",
        content="Repo-specific debug.",
        language=["python"],
        domain=["backend"],
        tags=["debugging"],
        org_id="acme",
        repo_pattern="acme/api-*",
        created_by="test",
    ))
    # Different org skill (should NOT match acme)
    await registry.create(SkillCreate(
        name="Other Org Skill",
        slug="other-org-skill",
        description="Belongs to other-org",
        content="Other org content.",
        language=["python"],
        domain=["backend"],
        tags=["style"],
        org_id="other-org",
        repo_pattern=None,
        created_by="test",
    ))


# ------------------------------------------------------------------
# Q6: Classifier Override Tests
# ------------------------------------------------------------------


class TestClassifierOverrideSkills:
    """TaskRequest with skill_overrides should bypass the classifier."""

    async def test_classifier_override_skills(self, registry, seed_skills):
        """When skill_overrides is set, those skills are fetched directly."""
        classifier = TaskClassifier(registry=registry)

        # Manually fetch by slugs to simulate what orchestrator does
        skills = await registry.get_by_slugs(["global-lint", "acme-style"])

        assert len(skills) == 2
        assert skills[0].slug == "global-lint"
        assert skills[1].slug == "acme-style"

    async def test_classifier_override_missing_slug(self, registry, seed_skills):
        """Missing slugs are silently skipped."""
        skills = await registry.get_by_slugs(["global-lint", "nonexistent-skill"])

        assert len(skills) == 1
        assert skills[0].slug == "global-lint"

    async def test_classifier_override_empty(self, registry, seed_skills):
        """Empty overrides list returns empty."""
        skills = await registry.get_by_slugs([])
        assert skills == []


class TestClassifierOverrideAgentType:
    """agent_type_override should be used directly."""

    async def test_agent_type_override_on_task_request(self):
        """TaskRequest accepts agent_type_override field."""
        tr = TaskRequest(
            thread_id="t1",
            source="github",
            source_ref={},
            repo_owner="acme",
            repo_name="api",
            task="Fix the bug",
            agent_type_override="frontend",
        )
        assert tr.agent_type_override == "frontend"

    async def test_skill_overrides_on_task_request(self):
        """TaskRequest accepts skill_overrides field."""
        tr = TaskRequest(
            thread_id="t1",
            source="github",
            source_ref={},
            repo_owner="acme",
            repo_name="api",
            task="Fix the bug",
            skill_overrides=["debug-react", "typescript-testing"],
        )
        assert tr.skill_overrides == ["debug-react", "typescript-testing"]

    async def test_overrides_default_none(self):
        """Override fields default to None (backward compatible)."""
        tr = TaskRequest(
            thread_id="t1",
            source="github",
            source_ref={},
            repo_owner="acme",
            repo_name="api",
            task="Fix the bug",
        )
        assert tr.skill_overrides is None
        assert tr.agent_type_override is None


# ------------------------------------------------------------------
# Q2: Skill Scope Tier Tests
# ------------------------------------------------------------------


class TestScopeGlobalSkillsAlwaysIncluded:
    """Global skills (no org_id, no repo_pattern) should always be returned."""

    async def test_scope_global_skills_always_included(self, registry, seed_skills):
        classifier = TaskClassifier(registry=registry)
        result = await classifier.classify(
            task="lint code",
            language=["python"],
            domain=["backend"],
            org_id=None,
            repo_owner=None,
            repo_name=None,
        )
        slugs = [s.slug for s in result.skills]
        assert "global-lint" in slugs

    async def test_scope_global_only_when_no_org(self, registry, seed_skills):
        """Without org context, org-scoped skills are excluded."""
        classifier = TaskClassifier(registry=registry)
        result = await classifier.classify(
            task="lint code",
            language=["python"],
            domain=["backend"],
            org_id=None,
            repo_owner=None,
            repo_name=None,
        )
        slugs = [s.slug for s in result.skills]
        assert "acme-style" not in slugs
        assert "other-org-skill" not in slugs


class TestScopeOrgSkillsFiltered:
    """Org-scoped skills should only be returned for matching org."""

    async def test_scope_org_skills_filtered(self, registry, seed_skills):
        classifier = TaskClassifier(registry=registry)
        result = await classifier.classify(
            task="style check",
            language=["python"],
            domain=["backend"],
            org_id="acme",
            repo_owner="acme",
            repo_name="some-repo",
        )
        slugs = [s.slug for s in result.skills]
        assert "acme-style" in slugs
        # Other org skill should NOT appear
        assert "other-org-skill" not in slugs

    async def test_scope_org_excludes_wrong_org(self, registry, seed_skills):
        """Skills from a different org are excluded."""
        classifier = TaskClassifier(registry=registry)
        result = await classifier.classify(
            task="style check",
            language=["python"],
            domain=["backend"],
            org_id="acme",
            repo_owner="acme",
            repo_name="api",
        )
        slugs = [s.slug for s in result.skills]
        assert "other-org-skill" not in slugs


class TestScopeRepoWinsOverOrg:
    """Repo-scoped skill with same slug should override org-scoped."""

    async def test_scope_repo_pattern_match(self, registry, seed_skills):
        """Repo-scoped skills are included when repo matches the glob pattern."""
        classifier = TaskClassifier(registry=registry)
        result = await classifier.classify(
            task="debug frontend",
            language=["typescript"],
            domain=["frontend"],
            org_id="acme",
            repo_owner="acme",
            repo_name="frontend-app",
        )
        slugs = [s.slug for s in result.skills]
        assert "frontend-debug" in slugs

    async def test_scope_repo_no_match(self, registry, seed_skills):
        """Repo-scoped skills are excluded when repo does NOT match."""
        classifier = TaskClassifier(registry=registry)
        result = await classifier.classify(
            task="debug frontend",
            language=["typescript"],
            domain=["frontend"],
            org_id="acme",
            repo_owner="acme",
            repo_name="backend-service",
        )
        slugs = [s.slug for s in result.skills]
        assert "frontend-debug" not in slugs

    async def test_filter_by_scope_narrower_wins(self):
        """When two skills share a slug, narrower scope (repo) wins over org."""
        org_skill = Skill(
            id="s1", name="Debug Org", slug="debug", description="org",
            content="org", org_id="acme", repo_pattern=None,
        )
        repo_skill = Skill(
            id="s2", name="Debug Repo", slug="debug", description="repo",
            content="repo", org_id="acme", repo_pattern="acme/api-*",
        )
        filtered = TaskClassifier._filter_by_scope(
            [org_skill, repo_skill],
            org_id="acme",
            repo_full_name="acme/api-service",
        )
        assert len(filtered) == 1
        assert filtered[0].id == "s2"  # repo wins


# ------------------------------------------------------------------
# Q3: Reindex Endpoint Test
# ------------------------------------------------------------------


class TestReindexEndpoint:
    """POST /api/v1/skills/reindex should return 202."""

    @pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
    async def test_reindex_endpoint(self, registry, seed_skills):
        from fastapi import FastAPI
        from controller.skills.api import router, get_skill_registry

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_skill_registry] = lambda: registry

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/skills/reindex")

        assert resp.status_code == 202
        data = resp.json()
        assert "reindexed" in data
        assert "failed" in data
        # No embedder configured, so all should fail
        assert data["failed"] > 0
        assert data["reindexed"] == 0

    @pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
    async def test_reindex_with_embedder(self, db_path, seed_skills):
        """With an embedder, reindex should succeed."""
        from fastapi import FastAPI
        from controller.skills.api import router, get_skill_registry

        mock_embedder = AsyncMock()
        mock_embedder.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])

        reg = SkillRegistry(db_path=db_path, embedding_provider=mock_embedder)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_skill_registry] = lambda: reg

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/skills/reindex")

        assert resp.status_code == 202
        data = resp.json()
        assert data["reindexed"] > 0
        assert data["failed"] == 0
