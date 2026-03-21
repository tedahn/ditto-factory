"""
Tests for the Skill Hotloading REST API endpoints.

Uses a mock SkillRegistry to avoid database dependencies.
Follows the same pattern as test_api.py.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Lightweight data objects to simulate SkillRegistry return values
# ---------------------------------------------------------------------------

@dataclass
class FakeSkill:
    id: str = ""
    name: str = ""
    slug: str = ""
    description: str = ""
    content: str = ""
    language: list[str] = field(default_factory=list)
    domain: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    org_id: str | None = None
    repo_pattern: str | None = None
    version: int = 1
    is_active: bool = True
    is_default: bool = False
    created_by: str = ""
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class FakeSkillVersion:
    version: int = 1
    changelog: str | None = None
    created_by: str = ""
    created_at: str | None = None
    content: str = ""


@dataclass
class FakeSearchResult:
    slug: str = ""
    name: str = ""
    similarity: float = 1.0
    usage_count: int = 0
    success_rate: float = 0.0


@dataclass
class FakeAgentType:
    name: str = ""
    image: str = ""
    description: str | None = None
    capabilities: list[str] = field(default_factory=list)
    is_default: bool = False
    resource_profile: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# In-memory SkillRegistry mock with real behavior
# ---------------------------------------------------------------------------

class InMemorySkillRegistry:
    """Lightweight in-memory implementation for testing API endpoints."""

    def __init__(self):
        self._skills: dict[str, FakeSkill] = {}
        self._versions: dict[str, list[FakeSkillVersion]] = {}
        self._agent_types: dict[str, FakeAgentType] = {}

    async def register_skill(self, **kwargs) -> FakeSkill:
        slug = kwargs["slug"]
        if slug in self._skills:
            raise ValueError(f"Skill '{slug}' already exists")
        skill = FakeSkill(
            id=uuid.uuid4().hex,
            name=kwargs["name"],
            slug=slug,
            description=kwargs.get("description", ""),
            content=kwargs.get("content", ""),
            language=kwargs.get("language", []),
            domain=kwargs.get("domain", []),
            requires=kwargs.get("requires", []),
            tags=kwargs.get("tags", []),
            org_id=kwargs.get("org_id"),
            repo_pattern=kwargs.get("repo_pattern"),
            is_default=kwargs.get("is_default", False),
            created_by=kwargs.get("created_by", ""),
            version=1,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        self._skills[slug] = skill
        self._versions[slug] = [
            FakeSkillVersion(
                version=1,
                changelog="Initial version",
                created_by=skill.created_by,
                created_at=skill.created_at,
                content=skill.content,
            )
        ]
        return skill

    async def get_skill(self, slug: str) -> FakeSkill | None:
        return self._skills.get(slug)

    async def list_skills(
        self, filters: dict | None = None, page: int = 1, per_page: int = 20
    ) -> tuple[list[FakeSkill], int]:
        skills = list(self._skills.values())

        if filters:
            if "language" in filters:
                lang = filters["language"]
                skills = [s for s in skills if lang in s.language]
            if "domain" in filters:
                dom = filters["domain"]
                skills = [s for s in skills if dom in s.domain]

        total = len(skills)
        start = (page - 1) * per_page
        end = start + per_page
        return skills[start:end], total

    async def update_skill(self, slug: str, **kwargs) -> FakeSkill:
        skill = self._skills[slug]
        new_version = skill.version + 1

        if "content" in kwargs:
            skill.content = kwargs["content"]
        if "description" in kwargs:
            skill.description = kwargs["description"]
        if "language" in kwargs:
            skill.language = kwargs["language"]
        if "domain" in kwargs:
            skill.domain = kwargs["domain"]
        if "requires" in kwargs:
            skill.requires = kwargs["requires"]
        if "tags" in kwargs:
            skill.tags = kwargs["tags"]

        skill.version = new_version
        skill.updated_at = datetime.now(timezone.utc).isoformat()

        self._versions[slug].append(
            FakeSkillVersion(
                version=new_version,
                changelog=kwargs.get("changelog"),
                created_by=kwargs.get("updated_by", ""),
                created_at=skill.updated_at,
                content=skill.content,
            )
        )
        return skill

    async def delete_skill(self, slug: str) -> None:
        if slug in self._skills:
            del self._skills[slug]

    async def list_versions(self, slug: str) -> list[FakeSkillVersion]:
        return self._versions.get(slug, [])

    async def rollback_skill(self, slug: str, target_version: int) -> FakeSkill:
        versions = self._versions.get(slug, [])
        target = None
        for v in versions:
            if v.version == target_version:
                target = v
                break
        if target is None:
            raise ValueError(f"Version {target_version} not found")

        skill = self._skills[slug]
        skill.content = target.content
        skill.version = target_version
        skill.updated_at = datetime.now(timezone.utc).isoformat()
        return skill

    async def search_skills(self, **kwargs) -> list[FakeSearchResult]:
        results = []
        language_filter = kwargs.get("language") or []
        domain_filter = kwargs.get("domain") or []
        tags_filter = kwargs.get("tags") or []
        limit = kwargs.get("limit", 10)

        for skill in self._skills.values():
            match = True
            if language_filter:
                if not any(l in skill.language for l in language_filter):
                    match = False
            if domain_filter:
                if not any(d in skill.domain for d in domain_filter):
                    match = False
            if tags_filter:
                if not any(t in skill.tags for t in tags_filter):
                    match = False
            if match:
                results.append(
                    FakeSearchResult(
                        slug=skill.slug,
                        name=skill.name,
                        similarity=1.0,
                    )
                )
        return results[:limit]

    async def list_agent_types(self) -> list[FakeAgentType]:
        return list(self._agent_types.values())

    async def create_agent_type(self, **kwargs) -> FakeAgentType:
        at = FakeAgentType(
            name=kwargs["name"],
            image=kwargs["image"],
            description=kwargs.get("description"),
            capabilities=kwargs.get("capabilities", []),
            resource_profile=kwargs.get("resource_profile", {}),
        )
        self._agent_types[at.name] = at
        return at


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def registry():
    return InMemorySkillRegistry()


@pytest.fixture
def app(registry):
    from controller.skills.api import router, get_skill_registry

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_skill_registry] = lambda: registry
    return test_app


@pytest.fixture
def client(app):
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_SKILL = {
    "name": "Python Debugging",
    "slug": "python-debugging",
    "description": "Debug Python applications",
    "content": "You are a Python debugging expert...",
    "language": ["python"],
    "domain": ["debugging"],
    "tags": ["python", "debug"],
    "created_by": "test-user",
}


def _create_skill(client, **overrides) -> dict:
    body = {**SAMPLE_SKILL, **overrides}
    resp = client.post("/api/v1/skills", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ===========================================================================
# Tests
# ===========================================================================


class TestCreateSkill:
    """POST /api/v1/skills"""

    def test_create_skill(self, client):
        resp = client.post("/api/v1/skills", json=SAMPLE_SKILL)
        assert resp.status_code == 201
        data = resp.json()
        assert data["slug"] == "python-debugging"
        assert data["name"] == "Python Debugging"
        assert data["version"] == 1
        assert data["is_active"] is True
        assert "id" in data

    def test_create_skill_missing_fields(self, client):
        resp = client.post("/api/v1/skills", json={"name": "incomplete"})
        assert resp.status_code == 422


class TestGetSkill:
    """GET /api/v1/skills/{slug}"""

    def test_get_skill(self, client):
        _create_skill(client)
        resp = client.get("/api/v1/skills/python-debugging")
        assert resp.status_code == 200
        data = resp.json()
        assert data["slug"] == "python-debugging"
        assert data["content"] == SAMPLE_SKILL["content"]

    def test_get_skill_not_found(self, client):
        resp = client.get("/api/v1/skills/nonexistent")
        assert resp.status_code == 404


class TestListSkills:
    """GET /api/v1/skills"""

    def test_list_skills(self, client):
        _create_skill(client, slug="skill-1", name="Skill 1")
        _create_skill(client, slug="skill-2", name="Skill 2")
        _create_skill(client, slug="skill-3", name="Skill 3")

        resp = client.get("/api/v1/skills")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["skills"]) == 3
        assert data["page"] == 1
        assert data["per_page"] == 20

    def test_list_skills_pagination(self, client):
        for i in range(5):
            _create_skill(client, slug=f"skill-{i}", name=f"Skill {i}")

        resp = client.get("/api/v1/skills?page=1&per_page=2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert len(data["skills"]) == 2
        assert data["page"] == 1
        assert data["per_page"] == 2

    def test_list_skills_filter_language(self, client):
        _create_skill(client, slug="py-skill", name="Py", language=["python"])
        _create_skill(client, slug="js-skill", name="JS", language=["javascript"])

        resp = client.get("/api/v1/skills?language=python")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["skills"][0]["slug"] == "py-skill"


class TestUpdateSkill:
    """PUT /api/v1/skills/{slug}"""

    def test_update_skill(self, client):
        _create_skill(client)

        update = {"content": "Updated content", "changelog": "v2 update"}
        resp = client.put("/api/v1/skills/python-debugging", json=update)
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "Updated content"
        assert data["version"] == 2

    def test_update_skill_not_found(self, client):
        resp = client.put(
            "/api/v1/skills/nonexistent",
            json={"content": "new"},
        )
        assert resp.status_code == 404


class TestDeleteSkill:
    """DELETE /api/v1/skills/{slug}"""

    def test_delete_skill(self, client):
        _create_skill(client)

        resp = client.delete("/api/v1/skills/python-debugging")
        assert resp.status_code == 204

        # Verify GET returns 404
        resp = client.get("/api/v1/skills/python-debugging")
        assert resp.status_code == 404

    def test_delete_skill_not_found(self, client):
        resp = client.delete("/api/v1/skills/nonexistent")
        assert resp.status_code == 404


class TestSearchSkills:
    """POST /api/v1/skills/search"""

    def test_search_by_language(self, client):
        _create_skill(client, slug="py-1", name="Py1", language=["python"])
        _create_skill(client, slug="js-1", name="JS1", language=["javascript"])

        resp = client.post(
            "/api/v1/skills/search",
            json={"language": ["python"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["skills"]) == 1
        assert data["skills"][0]["slug"] == "py-1"

    def test_search_by_tags(self, client):
        _create_skill(client, slug="tagged", name="Tagged", tags=["security"])
        _create_skill(client, slug="untagged", name="Untagged", tags=["other"])

        resp = client.post(
            "/api/v1/skills/search",
            json={"tags": ["security"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["skills"]) == 1
        assert data["skills"][0]["slug"] == "tagged"


class TestRollback:
    """POST /api/v1/skills/{slug}/rollback"""

    def test_rollback(self, client):
        _create_skill(client)

        # Update to v2
        client.put(
            "/api/v1/skills/python-debugging",
            json={"content": "v2 content"},
        )

        # Verify v2
        resp = client.get("/api/v1/skills/python-debugging")
        assert resp.json()["content"] == "v2 content"
        assert resp.json()["version"] == 2

        # Rollback to v1
        resp = client.post(
            "/api/v1/skills/python-debugging/rollback",
            json={"target_version": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == 1
        assert data["content"] == SAMPLE_SKILL["content"]

    def test_rollback_not_found(self, client):
        resp = client.post(
            "/api/v1/skills/nonexistent/rollback",
            json={"target_version": 1},
        )
        assert resp.status_code == 404


class TestListVersions:
    """GET /api/v1/skills/{slug}/versions"""

    def test_list_versions(self, client):
        _create_skill(client)
        client.put(
            "/api/v1/skills/python-debugging",
            json={"content": "v2", "changelog": "Second version"},
        )
        client.put(
            "/api/v1/skills/python-debugging",
            json={"content": "v3", "changelog": "Third version"},
        )

        resp = client.get("/api/v1/skills/python-debugging/versions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        assert data[0]["version"] == 1
        assert data[1]["version"] == 2
        assert data[2]["version"] == 3
        assert data[1]["changelog"] == "Second version"

    def test_list_versions_not_found(self, client):
        resp = client.get("/api/v1/skills/nonexistent/versions")
        assert resp.status_code == 404


class TestMetrics:
    """GET /api/v1/skills/{slug}/metrics"""

    def test_metrics_stub(self, client):
        _create_skill(client)
        resp = client.get("/api/v1/skills/python-debugging/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["slug"] == "python-debugging"
        assert data["usage_count"] == 0

    def test_metrics_not_found(self, client):
        resp = client.get("/api/v1/skills/nonexistent/metrics")
        assert resp.status_code == 404


class TestAgentTypes:
    """Agent type endpoints"""

    def test_create_agent_type(self, client):
        body = {
            "name": "claude-coder",
            "image": "ditto-agent:latest",
            "description": "Claude coding agent",
            "capabilities": ["code", "test"],
        }
        resp = client.post("/api/v1/agent-types", json=body)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "claude-coder"
        assert data["image"] == "ditto-agent:latest"
        assert data["capabilities"] == ["code", "test"]

    def test_list_agent_types(self, client):
        client.post(
            "/api/v1/agent-types",
            json={"name": "type-a", "image": "img-a"},
        )
        client.post(
            "/api/v1/agent-types",
            json={"name": "type-b", "image": "img-b"},
        )

        resp = client.get("/api/v1/agent-types")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        names = {at["name"] for at in data}
        assert "type-a" in names
        assert "type-b" in names
