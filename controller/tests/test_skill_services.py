"""Tests for skill services: registry, classifier, injector, resolver, tracker."""

from __future__ import annotations

import json
import uuid

import pytest

try:
    import aiosqlite

    HAS_AIOSQLITE = True
except ImportError:
    HAS_AIOSQLITE = False

pytestmark = [
    pytest.mark.skipif(not HAS_AIOSQLITE, reason="aiosqlite not installed"),
    pytest.mark.asyncio,
]

# ------------------------------------------------------------------
# Schema SQL (mirrors the migration being built in parallel)
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
    version INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_versions (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL REFERENCES skills(id),
    version INTEGER NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    changelog TEXT,
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_types (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    image TEXT NOT NULL,
    description TEXT,
    capabilities TEXT NOT NULL DEFAULT '[]',
    resource_profile TEXT NOT NULL DEFAULT '{}',
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS skill_usage (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL REFERENCES skills(id),
    thread_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    task_source TEXT NOT NULL DEFAULT '',
    repo_owner TEXT,
    repo_name TEXT,
    was_selected INTEGER NOT NULL DEFAULT 1,
    exit_code INTEGER,
    commit_count INTEGER,
    pr_created INTEGER NOT NULL DEFAULT 0,
    injected_at TEXT,
    completed_at TEXT
);
"""

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / f"skills_{uuid.uuid4().hex[:8]}.db")
    async with aiosqlite.connect(path) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()
    return path


@pytest.fixture
def registry(db_path):
    from controller.skills.registry import SkillRegistry

    return SkillRegistry(db_path)


@pytest.fixture
def sample_create():
    from controller.skills.models import SkillCreate

    return SkillCreate(
        name="Python Best Practices",
        slug="python-best-practices",
        description="Coding standards for Python",
        content="Always use type hints and docstrings.",
        language=["python"],
        domain=["backend"],
        tags=["style", "python"],
        created_by="test-user",
    )


class _FakeSettings:
    skill_max_per_task: int = 5
    skill_max_total_chars: int = 16000


# ------------------------------------------------------------------
# Registry tests
# ------------------------------------------------------------------


async def test_registry_create_and_get(registry, sample_create):
    skill = await registry.create(sample_create)
    assert skill.slug == "python-best-practices"
    assert skill.version == 1
    assert skill.is_active is True

    fetched = await registry.get("python-best-practices")
    assert fetched is not None
    assert fetched.id == skill.id
    assert fetched.language == ["python"]
    assert fetched.domain == ["backend"]


async def test_registry_update_creates_version(registry, sample_create):
    from controller.skills.models import SkillUpdate

    skill = await registry.create(sample_create)
    assert skill.version == 1

    updated = await registry.update(
        "python-best-practices",
        SkillUpdate(content="Updated content v2", changelog="Fixed typo"),
    )
    assert updated.version == 2
    assert updated.content == "Updated content v2"

    versions = await registry.get_versions("python-best-practices")
    assert len(versions) == 2
    assert versions[0].version == 1
    assert versions[1].version == 2
    assert versions[1].changelog == "Fixed typo"


async def test_registry_soft_delete(registry, sample_create):
    await registry.create(sample_create)

    await registry.delete("python-best-practices")

    fetched = await registry.get("python-best-practices")
    assert fetched is None


async def test_registry_search_by_tags(registry):
    from controller.skills.models import SkillCreate

    await registry.create(
        SkillCreate(
            name="Python Skill",
            slug="py-skill",
            description="Python",
            content="python stuff",
            language=["python"],
            domain=["backend"],
        )
    )
    await registry.create(
        SkillCreate(
            name="JS Skill",
            slug="js-skill",
            description="JS",
            content="js stuff",
            language=["javascript"],
            domain=["frontend"],
        )
    )

    results = await registry.search_by_tags(language=["python"])
    assert len(results) == 1
    assert results[0].slug == "py-skill"

    results = await registry.search_by_tags(domain=["frontend"])
    assert len(results) == 1
    assert results[0].slug == "js-skill"

    results = await registry.search_by_tags(language=["python", "javascript"])
    assert len(results) == 2


async def test_registry_get_defaults(registry):
    from controller.skills.models import SkillCreate

    await registry.create(
        SkillCreate(
            name="Default Skill",
            slug="default-skill",
            description="Always included",
            content="default content",
            is_default=True,
        )
    )
    await registry.create(
        SkillCreate(
            name="Optional Skill",
            slug="optional-skill",
            description="Only when matched",
            content="optional content",
            is_default=False,
        )
    )

    defaults = await registry.get_defaults()
    assert len(defaults) == 1
    assert defaults[0].slug == "default-skill"


# ------------------------------------------------------------------
# Classifier tests
# ------------------------------------------------------------------


async def test_classifier_tag_matching(registry):
    from controller.skills.classifier import TaskClassifier
    from controller.skills.models import SkillCreate

    await registry.create(
        SkillCreate(
            name="Python Style",
            slug="python-style",
            description="Style guide",
            content="Use black formatter.",
            language=["python"],
        )
    )
    await registry.create(
        SkillCreate(
            name="JS Style",
            slug="js-style",
            description="JS guide",
            content="Use prettier.",
            language=["javascript"],
        )
    )

    classifier = TaskClassifier(registry, _FakeSettings())
    result = await classifier.classify(
        task="Fix the Python API endpoint",
        language=["python"],
    )

    slugs = [s.slug for s in result.skills]
    assert "python-style" in slugs
    assert "js-style" not in slugs
    assert result.agent_type == "general"


async def test_classifier_budget_enforcement(registry):
    from controller.skills.classifier import TaskClassifier
    from controller.skills.models import SkillCreate

    # Create two skills: one small, one large
    await registry.create(
        SkillCreate(
            name="Small",
            slug="small-skill",
            description="Small",
            content="x" * 100,
            language=["python"],
            is_default=True,
        )
    )
    await registry.create(
        SkillCreate(
            name="Large",
            slug="large-skill",
            description="Large",
            content="y" * 500,
            language=["python"],
        )
    )

    settings = _FakeSettings()
    settings.skill_max_total_chars = 200  # Only room for the small one

    classifier = TaskClassifier(registry, settings)
    result = await classifier.classify(task="test", language=["python"])

    slugs = [s.slug for s in result.skills]
    assert "small-skill" in slugs
    assert "large-skill" not in slugs


# ------------------------------------------------------------------
# Injector tests
# ------------------------------------------------------------------


async def test_injector_format(registry, sample_create):
    from controller.skills.injector import SkillInjector

    skill = await registry.create(sample_create)

    injector = SkillInjector()
    payload = injector.format_for_redis([skill])

    assert len(payload) == 1
    assert payload[0]["name"] == "python-best-practices"
    assert payload[0]["content"] == skill.content


# ------------------------------------------------------------------
# Resolver tests
# ------------------------------------------------------------------


async def test_resolver_default_agent(db_path):
    from controller.skills.models import SkillCreate
    from controller.skills.resolver import AgentTypeResolver

    registry_local = __import__(
        "controller.skills.registry", fromlist=["SkillRegistry"]
    ).SkillRegistry(db_path)

    skill = await registry_local.create(
        SkillCreate(
            name="Basic",
            slug="basic-skill",
            description="No requirements",
            content="basic content",
        )
    )

    resolver = AgentTypeResolver(db_path)
    resolved = await resolver.resolve([skill], default_image="agent:latest")

    assert resolved.image == "agent:latest"
    assert resolved.agent_type == "general"


async def test_resolver_frontend_agent(db_path):
    from controller.skills.models import SkillCreate
    from controller.skills.resolver import AgentTypeResolver

    registry_local = __import__(
        "controller.skills.registry", fromlist=["SkillRegistry"]
    ).SkillRegistry(db_path)

    # Insert a frontend agent type into the DB
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO agent_types (id, name, image, description, capabilities, resource_profile, is_default)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                uuid.uuid4().hex,
                "frontend",
                "agent-frontend:latest",
                "Agent with browser",
                json.dumps(["browser", "node"]),
                json.dumps({"cpu": "2", "memory": "8Gi"}),
                0,
            ),
        )
        await db.commit()

    skill = await registry_local.create(
        SkillCreate(
            name="UI Skill",
            slug="ui-skill",
            description="Requires browser",
            content="test e2e with playwright",
            requires=["browser"],
        )
    )

    resolver = AgentTypeResolver(db_path)
    resolved = await resolver.resolve([skill], default_image="agent:latest")

    assert resolved.image == "agent-frontend:latest"
    assert resolved.agent_type == "frontend"
