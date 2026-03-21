"""Tests for PerformanceTracker Phase 3: metrics computation and learning loop."""

from __future__ import annotations

import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import aiosqlite
import pytest
import pytest_asyncio

from controller.skills.tracker import PerformanceTracker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MIGRATION_002 = os.path.join(
    os.path.dirname(__file__), "..", "migrations", "002_skill_registry.sql"
)
MIGRATION_003 = os.path.join(
    os.path.dirname(__file__), "..", "migrations", "003_skill_embeddings.sql"
)


@pytest_asyncio.fixture
async def db_path():
    """Create a temp SQLite database with skill tables."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    async with aiosqlite.connect(path) as db:
        with open(MIGRATION_002) as f:
            sql = f.read()
        # Execute each statement separately (aiosqlite doesn't do executescript well with async)
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                await db.execute(stmt)
        with open(MIGRATION_003) as f:
            sql = f.read()
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    await db.execute(stmt)
                except Exception:
                    pass  # ALTER TABLE may fail if column exists
        await db.commit()
    yield path
    os.unlink(path)


@pytest_asyncio.fixture
async def tracker(db_path):
    return PerformanceTracker(db_path=db_path)


async def _insert_skill(db_path: str, skill_id: str, slug: str) -> None:
    """Insert a minimal skill row."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO skills (id, name, slug, description, content, created_by)
               VALUES (?, ?, ?, 'test', 'test content', 'test')""",
            (skill_id, slug, slug),
        )
        await db.commit()


async def _insert_usage(
    db_path: str,
    skill_id: str,
    exit_code: int | None = 0,
    commit_count: int = 1,
    pr_created: bool = False,
    completed: bool = True,
    injected_at: str | None = None,
) -> str:
    """Insert a skill_usage row and return its id."""
    usage_id = uuid.uuid4().hex
    now = injected_at or datetime.now(timezone.utc).isoformat()
    completed_at = now if completed else None
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO skill_usage
               (id, skill_id, thread_id, job_id, task_source,
                exit_code, commit_count, pr_created, injected_at, completed_at)
               VALUES (?,?,?,?,?, ?,?,?,?,?)""",
            (
                usage_id,
                skill_id,
                f"thread-{usage_id[:8]}",
                f"job-{usage_id[:8]}",
                "cli",
                exit_code,
                commit_count,
                1 if pr_created else 0,
                now,
                completed_at,
            ),
        )
        await db.commit()
    return usage_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_injection_and_outcome(tracker, db_path):
    """Inject skills, record outcome, verify rows exist."""
    skill_id = "skill-001"
    await _insert_skill(db_path, skill_id, "my-skill")

    @dataclass
    class FakeTask:
        source: str = "cli"
        repo_owner: str = "org"
        repo_name: str = "repo"

    @dataclass
    class FakeSkill:
        id: str = skill_id

    await tracker.record_injection(
        skills=[FakeSkill()],
        thread_id="t1",
        job_id="j1",
        task_request=FakeTask(),
    )

    # Verify row was inserted
    async with aiosqlite.connect(db_path) as db:
        rows = await db.execute_fetchall(
            "SELECT * FROM skill_usage WHERE thread_id = 't1'"
        )
        assert len(rows) == 1

    @dataclass
    class FakeResult:
        exit_code: int = 0
        commit_count: int = 3
        pr_url: str = "https://github.com/pr/1"

    await tracker.record_outcome("t1", "j1", FakeResult())

    async with aiosqlite.connect(db_path) as db:
        rows = await db.execute_fetchall(
            "SELECT exit_code, commit_count, pr_created, completed_at FROM skill_usage WHERE thread_id = 't1'"
        )
        assert rows[0][0] == 0
        assert rows[0][1] == 3
        assert rows[0][2] == 1
        assert rows[0][3] is not None


@pytest.mark.asyncio
async def test_get_skill_metrics(tracker, db_path):
    """Create usage data, verify metrics computation."""
    skill_id = "skill-metrics"
    await _insert_skill(db_path, skill_id, "metrics-skill")

    # 3 successful, 1 failed, 1 incomplete
    await _insert_usage(db_path, skill_id, exit_code=0, commit_count=2, pr_created=True)
    await _insert_usage(db_path, skill_id, exit_code=0, commit_count=4, pr_created=False)
    await _insert_usage(db_path, skill_id, exit_code=0, commit_count=1, pr_created=True)
    await _insert_usage(db_path, skill_id, exit_code=1, commit_count=0, pr_created=False)
    await _insert_usage(db_path, skill_id, exit_code=None, completed=False)  # incomplete

    metrics = await tracker.get_skill_metrics("metrics-skill")
    assert metrics is not None
    assert metrics.usage_count == 4  # only completed
    assert metrics.success_rate == 0.75  # 3/4
    assert metrics.avg_commits == pytest.approx(1.75)  # (2+4+1+0)/4
    assert metrics.pr_creation_rate == 0.5  # 2/4


@pytest.mark.asyncio
async def test_get_skill_metrics_none(tracker, db_path):
    """No usage data returns None."""
    await _insert_skill(db_path, "skill-empty", "empty-skill")
    metrics = await tracker.get_skill_metrics("empty-skill")
    assert metrics is None


@pytest.mark.asyncio
async def test_compute_boost_high_success(tracker, db_path):
    """Skills with >80% success rate get a positive boost."""
    skill_id = "skill-high"
    await _insert_skill(db_path, skill_id, "high-success")

    # 10 events, 9 successful (90% success rate)
    for i in range(9):
        await _insert_usage(db_path, skill_id, exit_code=0)
    await _insert_usage(db_path, skill_id, exit_code=1)

    boosted = await tracker.compute_boost(skill_id, 0.8)
    # 90% success -> boost = ((0.9 - 0.8) / 0.2) * 0.1 = 0.05
    assert boosted == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_compute_boost_low_success(tracker, db_path):
    """Skills with <40% success rate get a negative penalty."""
    skill_id = "skill-low"
    await _insert_skill(db_path, skill_id, "low-success")

    # 10 events, 2 successful (20% success rate)
    for i in range(2):
        await _insert_usage(db_path, skill_id, exit_code=0)
    for i in range(8):
        await _insert_usage(db_path, skill_id, exit_code=1)

    boosted = await tracker.compute_boost(skill_id, 0.8)
    # 20% success -> penalty = -((0.4 - 0.2) / 0.4) * 0.1 = -0.05
    assert boosted == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_compute_boost_insufficient_data(tracker, db_path):
    """Skills with <10 events return base score unchanged."""
    skill_id = "skill-few"
    await _insert_skill(db_path, skill_id, "few-events")

    # Only 5 events
    for i in range(5):
        await _insert_usage(db_path, skill_id, exit_code=0)

    boosted = await tracker.compute_boost(skill_id, 0.8)
    assert boosted == 0.8  # unchanged


@pytest.mark.asyncio
async def test_compute_boost_neutral_zone(tracker, db_path):
    """Skills in the 40-80% zone get no boost."""
    skill_id = "skill-mid"
    await _insert_skill(db_path, skill_id, "mid-success")

    # 10 events, 6 successful (60% success rate)
    for i in range(6):
        await _insert_usage(db_path, skill_id, exit_code=0)
    for i in range(4):
        await _insert_usage(db_path, skill_id, exit_code=1)

    boosted = await tracker.compute_boost(skill_id, 0.8)
    assert boosted == 0.8  # no change in neutral zone


@pytest.mark.asyncio
async def test_get_trend(tracker, db_path):
    """Verify current vs previous period comparison."""
    skill_id = "skill-trend"
    await _insert_skill(db_path, skill_id, "trend-skill")

    now = datetime.now(timezone.utc)

    # Current period (last 7 days): 3 events, 2 success
    for i in range(2):
        ts = (now - timedelta(days=2)).isoformat()
        await _insert_usage(db_path, skill_id, exit_code=0, injected_at=ts)
    ts = (now - timedelta(days=3)).isoformat()
    await _insert_usage(db_path, skill_id, exit_code=1, injected_at=ts)

    # Previous period (7-14 days ago): 2 events, 1 success
    ts = (now - timedelta(days=10)).isoformat()
    await _insert_usage(db_path, skill_id, exit_code=0, injected_at=ts)
    ts = (now - timedelta(days=11)).isoformat()
    await _insert_usage(db_path, skill_id, exit_code=1, injected_at=ts)

    trend = await tracker.get_trend("trend-skill", days=7)
    assert trend["current"]["usage"] == 3
    assert trend["current"]["success_rate"] == pytest.approx(2 / 3)
    assert trend["previous"]["usage"] == 2
    assert trend["previous"]["success_rate"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_get_all_metrics(tracker, db_path):
    """Get metrics for multiple skills at once."""
    await _insert_skill(db_path, "s1", "skill-a")
    await _insert_skill(db_path, "s2", "skill-b")

    await _insert_usage(db_path, "s1", exit_code=0, commit_count=2)
    await _insert_usage(db_path, "s1", exit_code=1, commit_count=0)
    await _insert_usage(db_path, "s2", exit_code=0, commit_count=5)

    all_metrics = await tracker.get_all_metrics()
    assert "skill-a" in all_metrics
    assert "skill-b" in all_metrics
    assert all_metrics["skill-a"].usage_count == 2
    assert all_metrics["skill-a"].success_rate == 0.5
    assert all_metrics["skill-b"].usage_count == 1
    assert all_metrics["skill-b"].success_rate == 1.0


@pytest.mark.asyncio
async def test_metrics_api_endpoint(db_path):
    """Test the API endpoint returns real metrics."""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    from controller.skills.api import router, get_skill_registry, get_performance_tracker

    skill_id = "skill-api"
    await _insert_skill(db_path, skill_id, "api-skill")
    await _insert_usage(db_path, skill_id, exit_code=0, commit_count=3, pr_created=True)
    await _insert_usage(db_path, skill_id, exit_code=0, commit_count=1, pr_created=False)
    await _insert_usage(db_path, skill_id, exit_code=1, commit_count=0, pr_created=False)

    tracker = PerformanceTracker(db_path=db_path)

    # Mock registry
    mock_registry = AsyncMock()

    @dataclass
    class FakeSkill:
        slug: str = "api-skill"

    mock_registry.get_skill.return_value = FakeSkill()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_skill_registry] = lambda: mock_registry
    app.dependency_overrides[get_performance_tracker] = lambda: tracker

    client = TestClient(app)
    resp = client.get("/api/v1/skills/api-skill/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["skill_slug"] == "api-skill"
    assert data["usage_count"] == 3
    assert data["success_rate"] == pytest.approx(2 / 3)
    assert data["avg_commits"] == pytest.approx(4 / 3)
    assert data["pr_creation_rate"] == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_metrics_api_not_found(db_path):
    """Test 404 for non-existent skill."""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    from controller.skills.api import router, get_skill_registry, get_performance_tracker

    tracker = PerformanceTracker(db_path=db_path)
    mock_registry = AsyncMock()
    mock_registry.get_skill.return_value = None

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_skill_registry] = lambda: mock_registry
    app.dependency_overrides[get_performance_tracker] = lambda: tracker

    client = TestClient(app)
    resp = client.get("/api/v1/skills/nonexistent/metrics")
    assert resp.status_code == 404
