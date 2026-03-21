"""End-to-end integration tests for the skill injection pipeline.

Tests the full flow: task classification -> skill matching -> budget enforcement
-> Redis payload construction, without requiring K8s or real Redis.

Uses:
- aiosqlite with in-memory database (real SQL migrations)
- Real SkillRegistry, TaskClassifier, SkillInjector
- Mocked: JobSpawner, StateBackend, RedisState
"""

from __future__ import annotations

import json
import tempfile
import uuid

import aiosqlite
import pytest

from unittest.mock import AsyncMock, MagicMock, patch

from controller.config import Settings
from controller.models import TaskRequest, Thread, Job, ThreadStatus, JobStatus
from controller.skills.classifier import TaskClassifier
from controller.skills.injector import SkillInjector
from controller.skills.models import SkillCreate
from controller.skills.registry import SkillRegistry
from controller.integrations.registry import IntegrationRegistry
from controller.orchestrator import Orchestrator


# ── SQL migrations applied to every test database ─────────────────────

MIGRATION_002 = """\
CREATE TABLE IF NOT EXISTS skills (
    id              TEXT PRIMARY KEY,
    name            VARCHAR(128) NOT NULL UNIQUE,
    slug            VARCHAR(128) NOT NULL UNIQUE,
    description     TEXT NOT NULL,
    content         TEXT NOT NULL,
    language        TEXT DEFAULT '[]',
    domain          TEXT DEFAULT '[]',
    requires        TEXT DEFAULT '[]',
    tags            TEXT DEFAULT '[]',
    org_id          VARCHAR(128),
    repo_pattern    VARCHAR(256),
    version         INTEGER NOT NULL DEFAULT 1,
    created_by      VARCHAR(128) NOT NULL DEFAULT '',
    is_active       BOOLEAN NOT NULL DEFAULT 1,
    is_default      BOOLEAN NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_skills_slug ON skills(slug);
CREATE INDEX IF NOT EXISTS idx_skills_active ON skills(is_active);
CREATE INDEX IF NOT EXISTS idx_skills_default ON skills(is_default);

CREATE TABLE IF NOT EXISTS skill_versions (
    id              TEXT PRIMARY KEY,
    skill_id        TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    version         INTEGER NOT NULL,
    content         TEXT NOT NULL,
    description     TEXT NOT NULL,
    changelog       TEXT,
    created_by      VARCHAR(128) NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (skill_id, version)
);

CREATE TABLE IF NOT EXISTS skill_usage (
    id              TEXT PRIMARY KEY,
    skill_id        TEXT NOT NULL REFERENCES skills(id),
    thread_id       VARCHAR(128) NOT NULL,
    job_id          VARCHAR(128) NOT NULL,
    task_source     VARCHAR(32) NOT NULL,
    repo_owner      VARCHAR(128),
    repo_name       VARCHAR(128),
    was_selected    BOOLEAN NOT NULL DEFAULT 1,
    exit_code       INTEGER,
    commit_count    INTEGER,
    pr_created      BOOLEAN DEFAULT 0,
    injected_at     TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_usage_skill ON skill_usage(skill_id);
CREATE INDEX IF NOT EXISTS idx_usage_thread ON skill_usage(thread_id);

CREATE TABLE IF NOT EXISTS agent_types (
    id              TEXT PRIMARY KEY,
    name            VARCHAR(128) NOT NULL UNIQUE,
    image           VARCHAR(256) NOT NULL,
    description     TEXT,
    capabilities    TEXT NOT NULL DEFAULT '[]',
    resource_profile TEXT NOT NULL DEFAULT '{}',
    is_default      BOOLEAN NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO agent_types (id, name, image, capabilities, is_default)
VALUES ('default', 'general', 'ditto-factory-agent:latest', '[]', 1);
"""

MIGRATION_003 = """\
ALTER TABLE skills ADD COLUMN embedding TEXT;
ALTER TABLE skill_versions ADD COLUMN embedding TEXT;
ALTER TABLE skill_usage ADD COLUMN task_embedding TEXT;
"""


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def db_path(tmp_path):
    """Return path to a temporary SQLite database file."""
    return str(tmp_path / "test_skills.db")


@pytest.fixture
async def db_ready(db_path):
    """Apply migrations and return the db_path."""
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(MIGRATION_002)
        await db.executescript(MIGRATION_003)
    return db_path


@pytest.fixture
def settings():
    """Settings with skill registry enabled, no embeddings."""
    return Settings(
        skill_registry_enabled=True,
        skill_embedding_provider="none",
        skill_max_per_task=5,
        skill_max_total_chars=16000,
        skill_min_similarity=0.5,
        redis_url="redis://localhost:6379",
        agent_image="ditto-factory-agent:latest",
    )


@pytest.fixture
def registry(db_ready):
    """Real SkillRegistry backed by in-memory SQLite."""
    return SkillRegistry(db_path=db_ready, embedding_provider=None)


@pytest.fixture
def classifier(registry, settings):
    """Real TaskClassifier with no embedding provider (tag-only)."""
    return TaskClassifier(
        registry=registry,
        embedding_provider=None,
        settings=settings,
    )


@pytest.fixture
def injector():
    """Real SkillInjector."""
    return SkillInjector()


@pytest.fixture
def mock_state():
    """Mock StateBackend that provides thread/job/lock operations."""
    state = AsyncMock()
    state.get_thread.return_value = None
    state.upsert_thread.return_value = None
    state.get_active_job_for_thread.return_value = None
    state.try_acquire_lock.return_value = True
    state.release_lock.return_value = None
    state.create_job.return_value = None
    state.update_thread_status.return_value = None
    state.append_conversation.return_value = None
    state.get_conversation.return_value = []
    return state


@pytest.fixture
def mock_redis():
    """Mock RedisState that records push_task calls."""
    redis = AsyncMock()
    redis.push_task.return_value = None
    redis.queue_message.return_value = None
    return redis


@pytest.fixture
def mock_spawner():
    """Mock JobSpawner that returns a fake job name."""
    spawner = MagicMock()
    spawner.spawn.return_value = "test-job-001"
    return spawner


@pytest.fixture
def mock_monitor():
    """Mock JobMonitor."""
    return MagicMock()


@pytest.fixture
def integration_registry():
    """IntegrationRegistry with a test integration."""
    reg = IntegrationRegistry()
    mock_integration = MagicMock()
    mock_integration.name = "test"
    reg.register(mock_integration)
    return reg


@pytest.fixture
def orchestrator(
    settings,
    mock_state,
    mock_redis,
    integration_registry,
    mock_spawner,
    mock_monitor,
    classifier,
    injector,
):
    """Orchestrator wired with real skill services and mocked infra."""
    return Orchestrator(
        settings=settings,
        state=mock_state,
        redis_state=mock_redis,
        registry=integration_registry,
        spawner=mock_spawner,
        monitor=mock_monitor,
        classifier=classifier,
        injector=injector,
    )


def _make_task_request(
    task: str = "fix the Python API endpoint",
    thread_id: str | None = None,
) -> TaskRequest:
    """Helper to create a TaskRequest for testing."""
    return TaskRequest(
        thread_id=thread_id or uuid.uuid4().hex,
        source="test",
        source_ref={},
        repo_owner="ditto-factory",
        repo_name="e2e-test-target",
        task=task,
    )


# ── Helper to seed skills ────────────────────────────────────────────

async def _seed_skill(
    registry: SkillRegistry,
    slug: str,
    name: str,
    content: str = "Skill content placeholder.",
    language: list[str] | None = None,
    domain: list[str] | None = None,
    is_default: bool = False,
) -> None:
    """Insert a skill into the registry."""
    await registry.create(
        SkillCreate(
            name=name,
            slug=slug,
            description=f"Description for {name}",
            content=content,
            language=language or [],
            domain=domain or [],
            is_default=is_default,
            created_by="test",
        )
    )


# ── Test Scenarios ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_skills_injected(registry, orchestrator, mock_redis, mock_spawner):
    """Skills matching the task language appear in the Redis payload."""
    # Seed 3 skills: python-debugging matches python, others do not
    await _seed_skill(registry, "python-debugging", "Python Debugging", language=["python"])
    await _seed_skill(registry, "react-review", "React Review", language=["javascript"])
    await _seed_skill(registry, "sql-optimization", "SQL Optimization", language=["sql"])

    task_req = _make_task_request(task="fix the Python API endpoint")
    # Provide language hint via source_ref so _detect_language returns ["python"]
    task_req.source_ref = {"language": "python"}

    await orchestrator.handle_task(task_req)

    # Verify push_task was called
    mock_redis.push_task.assert_called_once()
    call_args = mock_redis.push_task.call_args
    thread_id_arg = call_args[0][0]
    task_payload = call_args[0][1]

    assert thread_id_arg == task_req.thread_id
    assert "skills" in task_payload
    skills = task_payload["skills"]
    assert isinstance(skills, list)

    slugs = [s["name"] for s in skills]
    assert "python-debugging" in slugs
    # Non-matching languages should not appear
    assert "react-review" not in slugs
    assert "sql-optimization" not in slugs

    # Verify spawner was called
    mock_spawner.spawn.assert_called_once()


@pytest.mark.asyncio
async def test_no_skills_match(registry, orchestrator, mock_redis, mock_spawner):
    """When no skills match the task language, payload has empty skills array and job still spawns."""
    # Only seed rust skills
    await _seed_skill(registry, "rust-lifetimes", "Rust Lifetimes", language=["rust"])
    await _seed_skill(registry, "rust-async", "Rust Async", language=["rust"])

    task_req = _make_task_request(task="fix Python code")
    task_req.source_ref = {"language": "python"}

    await orchestrator.handle_task(task_req)

    # Verify push_task was called
    mock_redis.push_task.assert_called_once()
    task_payload = mock_redis.push_task.call_args[0][1]

    assert "skills" in task_payload
    assert task_payload["skills"] == []

    # Job still spawns
    mock_spawner.spawn.assert_called_once()


@pytest.mark.asyncio
async def test_classifier_failure_graceful(
    settings, mock_state, mock_redis, integration_registry, mock_spawner, mock_monitor, injector
):
    """When the classifier raises an exception, the job still spawns with no skills."""
    # Create a classifier that always raises
    failing_classifier = AsyncMock()
    failing_classifier.classify.side_effect = RuntimeError("Classifier exploded")

    orch = Orchestrator(
        settings=settings,
        state=mock_state,
        redis_state=mock_redis,
        registry=integration_registry,
        spawner=mock_spawner,
        monitor=mock_monitor,
        classifier=failing_classifier,
        injector=injector,
    )

    task_req = _make_task_request(task="fix the Python API endpoint")

    await orch.handle_task(task_req)

    # Job still spawns despite classifier failure
    mock_spawner.spawn.assert_called_once()

    # Redis payload has empty skills
    task_payload = mock_redis.push_task.call_args[0][1]
    assert task_payload["skills"] == []


@pytest.mark.asyncio
async def test_budget_exceeded_trims_skills(db_ready, settings, mock_state, mock_redis, integration_registry, mock_spawner, mock_monitor, injector):
    """When skills exceed the character budget, excess skills are trimmed."""
    # Override settings with a tight budget
    settings.skill_max_total_chars = 10000
    settings.skill_max_per_task = 10  # Allow many skills by count so budget is the limiting factor

    reg = SkillRegistry(db_path=db_ready, embedding_provider=None)
    classifier = TaskClassifier(registry=reg, embedding_provider=None, settings=settings)

    orch = Orchestrator(
        settings=settings,
        state=mock_state,
        redis_state=mock_redis,
        registry=integration_registry,
        spawner=mock_spawner,
        monitor=mock_monitor,
        classifier=classifier,
        injector=injector,
    )

    # Create 5 skills each with 5000 chars of content, all matching python
    for i in range(5):
        await _seed_skill(
            reg,
            slug=f"big-skill-{i}",
            name=f"Big Skill {i}",
            content="x" * 5000,
            language=["python"],
        )

    task_req = _make_task_request(task="fix the Python API endpoint")
    task_req.source_ref = {"language": "python"}

    await orch.handle_task(task_req)

    task_payload = mock_redis.push_task.call_args[0][1]
    skills = task_payload["skills"]

    # Budget is 10000 chars, each skill is 5000 chars, so only 2 fit
    assert len(skills) == 2

    # Verify total content fits within budget
    total_chars = sum(len(s["content"]) for s in skills)
    assert total_chars <= 10000

    # Job still spawns
    mock_spawner.spawn.assert_called_once()


@pytest.mark.asyncio
async def test_default_skills_always_included(db_ready, settings, mock_state, mock_redis, integration_registry, mock_spawner, mock_monitor, injector):
    """Default skills appear in the payload alongside matched skills."""
    reg = SkillRegistry(db_path=db_ready, embedding_provider=None)
    classifier = TaskClassifier(registry=reg, embedding_provider=None, settings=settings)

    orch = Orchestrator(
        settings=settings,
        state=mock_state,
        redis_state=mock_redis,
        registry=integration_registry,
        spawner=mock_spawner,
        monitor=mock_monitor,
        classifier=classifier,
        injector=injector,
    )

    # Seed a default skill (always included regardless of language match)
    await _seed_skill(
        reg,
        slug="code-style-guide",
        name="Code Style Guide",
        content="Always use consistent formatting.",
        is_default=True,
    )

    # Seed a matching skill
    await _seed_skill(
        reg,
        slug="python-debugging",
        name="Python Debugging",
        content="Debug Python code using pdb and logging.",
        language=["python"],
    )

    task_req = _make_task_request(task="fix the Python API endpoint")
    task_req.source_ref = {"language": "python"}

    await orch.handle_task(task_req)

    task_payload = mock_redis.push_task.call_args[0][1]
    skills = task_payload["skills"]
    slugs = [s["name"] for s in skills]

    # Both default and matched skills should appear
    assert "code-style-guide" in slugs
    assert "python-debugging" in slugs
