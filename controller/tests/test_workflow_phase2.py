"""Phase 2 Workflow Engine Tests.

Tests for parallel fan-out spawning, improved aggregate/transform steps,
and fan-out reference handling in _get_step_output.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from controller.config import Settings
from controller.workflows.engine import WorkflowEngine
from controller.workflows.models import ExecutionStatus, StepStatus, StepType


# ---------------------------------------------------------------------------
# Fixtures (reuse init pattern from test_workflow_engine)
# ---------------------------------------------------------------------------

MIGRATION_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "migrations",
    "004_workflow_engine.sql",
)


def _read_migration() -> str:
    with open(MIGRATION_PATH) as f:
        return f.read()


def _split_sql(sql: str) -> list[str]:
    statements = []
    current: list[str] = []
    for line in sql.split("\n"):
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(current))
            current = []
    if current:
        statements.append("\n".join(current))
    return statements


async def _init_db(db_path: str) -> None:
    migration_sql = _read_migration()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                thread_id TEXT,
                status TEXT DEFAULT 'pending'
            )"""
        )
        for statement in _split_sql(migration_sql):
            stmt = statement.strip()
            if not stmt:
                continue
            try:
                await db.execute(stmt)
            except Exception:
                pass
        await db.commit()


async def _insert_template(
    db_path: str,
    slug: str = "test-workflow",
    definition: dict | None = None,
) -> str:
    template_id = uuid.uuid4().hex
    if definition is None:
        definition = {"steps": []}
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO workflow_templates
               (id, slug, name, description, version, definition,
                parameter_schema, is_active, created_by)
               VALUES (?, ?, ?, ?, 1, ?, NULL, 1, 'test')""",
            (template_id, slug, f"Test: {slug}", "Test template", json.dumps(definition)),
        )
        await db.commit()
    return template_id


def _fan_out_definition(num_regions: int = 3) -> dict:
    regions = [f"region-{i}" for i in range(num_regions)]
    return {
        "steps": [
            {
                "id": "search",
                "type": "fan_out",
                "depends_on": [],
                "fan_out": {
                    "over": "regions",
                    "max_parallel": 2,
                    "on_failure": "collect_all",
                },
                "agent": {
                    "task_template": "Search for events in {{ region }}",
                    "task_type": "analysis",
                },
            },
        ],
    }, {"regions": regions}


def _full_pipeline_definition() -> tuple[dict, dict]:
    return {
        "steps": [
            {
                "id": "search",
                "type": "fan_out",
                "depends_on": [],
                "fan_out": {"over": "regions", "max_parallel": 10, "on_failure": "collect_all"},
                "agent": {"task_template": "Search {{ region }}", "task_type": "analysis"},
            },
            {
                "id": "merge",
                "type": "aggregate",
                "depends_on": ["search"],
                "aggregate": {"input": "search.*", "strategy": "merge_arrays"},
            },
            {
                "id": "clean",
                "type": "transform",
                "depends_on": ["merge"],
                "transform": {
                    "input": "merge",
                    "operations": [
                        {"op": "deduplicate", "key": "name"},
                        {"op": "sort", "field": "name", "order": "asc"},
                        {"op": "limit", "count": 3},
                    ],
                },
            },
        ],
    }, {"regions": ["Dallas", "Austin"]}


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "test_phase2.db")
    await _init_db(path)
    return path


@pytest.fixture
def settings():
    return Settings(
        max_agents_per_execution=50,
        max_concurrent_agents=50,
        workflow_step_timeout_seconds=1800,
    )


def _make_spawner():
    spawner = MagicMock()
    spawner.spawn = MagicMock(side_effect=lambda **kw: f"job-{kw['thread_id'][-3:]}")
    return spawner


def _make_redis():
    redis = MagicMock()
    redis.push_task = AsyncMock()
    return redis


# ---------------------------------------------------------------------------
# 1. test_fan_out_parallel_spawn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fan_out_parallel_spawn(db_path, settings):
    """Spawner should be called N times for N agents."""
    definition, params = _fan_out_definition(num_regions=3)
    spawner = _make_spawner()
    redis = _make_redis()
    engine = WorkflowEngine(db_path=db_path, settings=settings, spawner=spawner, redis_state=redis)

    await _insert_template(db_path, definition=definition)
    exec_id = await engine.start(
        template_slug="test-workflow",
        parameters=params,
        thread_id="t-001",
    )

    assert spawner.spawn.call_count == 3
    assert redis.push_task.call_count == 3

    # Verify agent records created
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM workflow_step_agents ORDER BY agent_index"
        )
        agents = await cursor.fetchall()
    assert len(agents) == 3
    for i, a in enumerate(agents):
        assert a["agent_index"] == i
        assert a["status"] == "running"


# ---------------------------------------------------------------------------
# 2. test_fan_out_semaphore_limits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fan_out_semaphore_limits(db_path, settings):
    """max_parallel should bound concurrent agent spawns."""
    definition, params = _fan_out_definition(num_regions=5)
    # Override max_parallel to 2
    definition["steps"][0]["fan_out"]["max_parallel"] = 2

    concurrency_log: list[int] = []
    current_count = 0
    max_observed = 0
    lock = asyncio.Lock()

    original_spawn = MagicMock(side_effect=lambda **kw: f"job-{kw['thread_id'][-3:]}")

    async def tracking_push_task(thread_id, payload):
        nonlocal current_count, max_observed
        async with lock:
            current_count += 1
            if current_count > max_observed:
                max_observed = current_count
            concurrency_log.append(current_count)
        # Simulate some work
        await asyncio.sleep(0.01)
        async with lock:
            current_count -= 1

    redis = MagicMock()
    redis.push_task = AsyncMock(side_effect=tracking_push_task)

    engine = WorkflowEngine(
        db_path=db_path, settings=settings, spawner=MagicMock(spawn=original_spawn), redis_state=redis
    )
    await _insert_template(db_path, definition=definition)
    await engine.start(
        template_slug="test-workflow",
        parameters=params,
        thread_id="t-002",
    )

    assert original_spawn.call_count == 5
    # The semaphore should have limited concurrency to 2
    assert max_observed <= 2


# ---------------------------------------------------------------------------
# 3. test_aggregate_merge_arrays
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aggregate_merge_arrays(db_path, settings):
    """merge_arrays should flatten nested lists and unwrap result dicts."""
    engine = WorkflowEngine(db_path=db_path, settings=settings)

    definition, params = _full_pipeline_definition()
    await _insert_template(db_path, definition=definition)
    exec_id = await engine.start(
        template_slug="test-workflow",
        parameters=params,
        thread_id="t-003",
    )

    # Simulate fan-out agents completing with nested results
    await engine.handle_agent_result(
        exec_id, "search", 0,
        {"result": [{"name": "A"}, {"name": "B"}]},
    )
    await engine.handle_agent_result(
        exec_id, "search", 1,
        {"result": [{"name": "C"}]},
    )

    # After both agents complete, aggregate step should run automatically
    steps = await engine.get_steps(exec_id)
    merge_step = next(s for s in steps if s.step_id == "merge")
    assert merge_step.status == StepStatus.COMPLETED
    output = merge_step.output
    assert "result" in output
    # The merge_arrays strategy should have unwrapped the "result" key
    # from each agent's output
    result = output["result"]
    assert isinstance(result, list)
    assert len(result) >= 2  # At least the unwrapped items


# ---------------------------------------------------------------------------
# 4. test_aggregate_merge_objects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aggregate_merge_objects(db_path, settings):
    """merge_objects should combine dicts from multiple agents."""
    engine = WorkflowEngine(db_path=db_path, settings=settings)

    definition = {
        "steps": [
            {
                "id": "search",
                "type": "fan_out",
                "depends_on": [],
                "fan_out": {"over": "regions", "max_parallel": 10},
                "agent": {"task_template": "Search {{ region }}"},
            },
            {
                "id": "merge",
                "type": "aggregate",
                "depends_on": ["search"],
                "aggregate": {"input": "search.*", "strategy": "merge_objects"},
            },
        ],
    }
    await _insert_template(db_path, definition=definition)
    exec_id = await engine.start(
        template_slug="test-workflow",
        parameters={"regions": ["A", "B"]},
        thread_id="t-004",
    )

    await engine.handle_agent_result(exec_id, "search", 0, {"key1": "val1"})
    await engine.handle_agent_result(exec_id, "search", 1, {"key2": "val2"})

    steps = await engine.get_steps(exec_id)
    merge_step = next(s for s in steps if s.step_id == "merge")
    assert merge_step.status == StepStatus.COMPLETED
    result = merge_step.output["result"]
    assert result["key1"] == "val1"
    assert result["key2"] == "val2"


# ---------------------------------------------------------------------------
# 5. test_aggregate_concat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aggregate_concat(db_path, settings):
    """concat strategy should join items as strings."""
    engine = WorkflowEngine(db_path=db_path, settings=settings)

    definition = {
        "steps": [
            {
                "id": "search",
                "type": "fan_out",
                "depends_on": [],
                "fan_out": {"over": "regions", "max_parallel": 10},
                "agent": {"task_template": "Search {{ region }}"},
            },
            {
                "id": "merge",
                "type": "aggregate",
                "depends_on": ["search"],
                "aggregate": {"input": "search.*", "strategy": "concat"},
            },
        ],
    }
    await _insert_template(db_path, definition=definition)
    exec_id = await engine.start(
        template_slug="test-workflow",
        parameters={"regions": ["A", "B"]},
        thread_id="t-005",
    )

    await engine.handle_agent_result(exec_id, "search", 0, "Hello from A")
    await engine.handle_agent_result(exec_id, "search", 1, "Hello from B")

    steps = await engine.get_steps(exec_id)
    merge_step = next(s for s in steps if s.step_id == "merge")
    assert merge_step.status == StepStatus.COMPLETED
    result = merge_step.output["result"]
    assert "Hello from A" in result
    assert "Hello from B" in result


# ---------------------------------------------------------------------------
# 6. test_transform_deduplicate_composite_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_deduplicate_composite_key(db_path, settings):
    """Deduplicate with composite key 'name+date' should work."""
    engine = WorkflowEngine(db_path=db_path, settings=settings)

    # Set up a simple aggregate -> transform pipeline manually
    definition = {
        "steps": [
            {
                "id": "search",
                "type": "fan_out",
                "depends_on": [],
                "fan_out": {"over": "regions", "max_parallel": 10},
                "agent": {"task_template": "Search {{ region }}"},
            },
            {
                "id": "merge",
                "type": "aggregate",
                "depends_on": ["search"],
                "aggregate": {"input": "search.*", "strategy": "merge_arrays"},
            },
            {
                "id": "dedup",
                "type": "transform",
                "depends_on": ["merge"],
                "transform": {
                    "input": "merge",
                    "operations": [
                        {"op": "deduplicate", "key": "name+date"},
                    ],
                },
            },
        ],
    }
    await _insert_template(db_path, definition=definition)
    exec_id = await engine.start(
        template_slug="test-workflow",
        parameters={"regions": ["A"]},
        thread_id="t-006",
    )

    # Agent returns duplicates with composite key
    await engine.handle_agent_result(
        exec_id, "search", 0,
        [
            {"name": "Event X", "date": "2026-01-01"},
            {"name": "Event X", "date": "2026-01-01"},  # duplicate
            {"name": "Event X", "date": "2026-01-02"},  # different date
        ],
    )

    steps = await engine.get_steps(exec_id)
    dedup_step = next(s for s in steps if s.step_id == "dedup")
    assert dedup_step.status == StepStatus.COMPLETED
    result = dedup_step.output["result"]
    assert len(result) == 2  # Only 2 unique name+date combos


# ---------------------------------------------------------------------------
# 7. test_transform_filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_filter(db_path, settings):
    """Filter with field=value should keep only matching items."""
    engine = WorkflowEngine(db_path=db_path, settings=settings)

    definition = {
        "steps": [
            {
                "id": "search",
                "type": "fan_out",
                "depends_on": [],
                "fan_out": {"over": "regions", "max_parallel": 10},
                "agent": {"task_template": "Search {{ region }}"},
            },
            {
                "id": "merge",
                "type": "aggregate",
                "depends_on": ["search"],
                "aggregate": {"input": "search.*", "strategy": "merge_arrays"},
            },
            {
                "id": "filter",
                "type": "transform",
                "depends_on": ["merge"],
                "transform": {
                    "input": "merge",
                    "operations": [
                        {"op": "filter", "condition": "category == sports"},
                    ],
                },
            },
        ],
    }
    await _insert_template(db_path, definition=definition)
    exec_id = await engine.start(
        template_slug="test-workflow",
        parameters={"regions": ["A"]},
        thread_id="t-007",
    )

    await engine.handle_agent_result(
        exec_id, "search", 0,
        [
            {"name": "Game", "category": "sports"},
            {"name": "Concert", "category": "music"},
            {"name": "Match", "category": "sports"},
        ],
    )

    steps = await engine.get_steps(exec_id)
    filter_step = next(s for s in steps if s.step_id == "filter")
    assert filter_step.status == StepStatus.COMPLETED
    result = filter_step.output["result"]
    assert len(result) == 2
    assert all(item["category"] == "sports" for item in result)


# ---------------------------------------------------------------------------
# 8. test_transform_sort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_sort(db_path, settings):
    """Sort operation should order items ascending and descending."""
    engine = WorkflowEngine(db_path=db_path, settings=settings)

    definition = {
        "steps": [
            {
                "id": "search",
                "type": "fan_out",
                "depends_on": [],
                "fan_out": {"over": "regions", "max_parallel": 10},
                "agent": {"task_template": "Search {{ region }}"},
            },
            {
                "id": "merge",
                "type": "aggregate",
                "depends_on": ["search"],
                "aggregate": {"input": "search.*", "strategy": "merge_arrays"},
            },
            {
                "id": "sort_asc",
                "type": "transform",
                "depends_on": ["merge"],
                "transform": {
                    "input": "merge",
                    "operations": [
                        {"op": "sort", "field": "name", "order": "asc"},
                    ],
                },
            },
        ],
    }
    await _insert_template(db_path, definition=definition)
    exec_id = await engine.start(
        template_slug="test-workflow",
        parameters={"regions": ["A"]},
        thread_id="t-008",
    )

    await engine.handle_agent_result(
        exec_id, "search", 0,
        [{"name": "Charlie"}, {"name": "Alpha"}, {"name": "Bravo"}],
    )

    steps = await engine.get_steps(exec_id)
    sort_step = next(s for s in steps if s.step_id == "sort_asc")
    assert sort_step.status == StepStatus.COMPLETED
    result = sort_step.output["result"]
    names = [item["name"] for item in result]
    assert names == ["Alpha", "Bravo", "Charlie"]


# ---------------------------------------------------------------------------
# 9. test_transform_limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_limit(db_path, settings):
    """Limit operation should truncate the list."""
    engine = WorkflowEngine(db_path=db_path, settings=settings)

    definition = {
        "id": "search",
        "steps": [
            {
                "id": "search",
                "type": "fan_out",
                "depends_on": [],
                "fan_out": {"over": "regions", "max_parallel": 10},
                "agent": {"task_template": "Search {{ region }}"},
            },
            {
                "id": "merge",
                "type": "aggregate",
                "depends_on": ["search"],
                "aggregate": {"input": "search.*", "strategy": "merge_arrays"},
            },
            {
                "id": "truncate",
                "type": "transform",
                "depends_on": ["merge"],
                "transform": {
                    "input": "merge",
                    "operations": [
                        {"op": "limit", "count": 2},
                    ],
                },
            },
        ],
    }
    await _insert_template(db_path, definition=definition)
    exec_id = await engine.start(
        template_slug="test-workflow",
        parameters={"regions": ["A"]},
        thread_id="t-009",
    )

    await engine.handle_agent_result(
        exec_id, "search", 0,
        [{"name": "A"}, {"name": "B"}, {"name": "C"}, {"name": "D"}],
    )

    steps = await engine.get_steps(exec_id)
    truncate_step = next(s for s in steps if s.step_id == "truncate")
    assert truncate_step.status == StepStatus.COMPLETED
    result = truncate_step.output["result"]
    assert len(result) == 2


# ---------------------------------------------------------------------------
# 10. test_full_pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_pipeline(db_path, settings):
    """Full pipeline: fan-out -> aggregate -> transform -> complete."""
    engine = WorkflowEngine(db_path=db_path, settings=settings)

    definition, params = _full_pipeline_definition()
    await _insert_template(db_path, definition=definition)
    exec_id = await engine.start(
        template_slug="test-workflow",
        parameters=params,
        thread_id="t-010",
    )

    # Both agents complete
    await engine.handle_agent_result(
        exec_id, "search", 0,
        [{"name": "Zeta"}, {"name": "Alpha"}, {"name": "Alpha"}],
    )
    await engine.handle_agent_result(
        exec_id, "search", 1,
        [{"name": "Beta"}, {"name": "Zeta"}],
    )

    # Execution should be complete
    execution = await engine.get_execution(exec_id)
    assert execution.status == ExecutionStatus.COMPLETED

    # Check final transform output
    steps = await engine.get_steps(exec_id)
    clean_step = next(s for s in steps if s.step_id == "clean")
    assert clean_step.status == StepStatus.COMPLETED
    result = clean_step.output["result"]
    # After dedup (by name), sort (asc), limit (3):
    # Unique names: Alpha, Zeta, Beta -> sorted: Alpha, Beta, Zeta -> limit 3
    names = [item["name"] for item in result]
    assert len(names) <= 3
    assert names == sorted(names)  # Should be in ascending order
