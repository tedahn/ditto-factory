"""Tests for the Two-State Workflow Engine.

Uses in-memory SQLite with migration 004 applied.
Mocks the JobSpawner — no K8s required.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import aiosqlite
import pytest

from controller.config import Settings
from controller.workflows.engine import WorkflowEngine
from controller.workflows.models import ExecutionStatus, StepStatus, StepType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MIGRATION_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "migrations",
    "004_workflow_engine.sql",
)


async def _init_db(db_path: str) -> None:
    """Run migration 004 against an in-memory (or file) SQLite DB."""
    migration_sql = _read_migration()
    async with aiosqlite.connect(db_path) as db:
        # Create a minimal jobs table so the ALTER TABLE doesn't fail
        await db.execute(
            """CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                thread_id TEXT,
                status TEXT DEFAULT 'pending'
            )"""
        )
        # Execute migration statements one at a time (SQLite can't run
        # multiple statements with executescript via aiosqlite easily,
        # and ALTER TABLE may error harmlessly on re-run)
        for statement in _split_sql(migration_sql):
            stmt = statement.strip()
            if not stmt:
                continue
            try:
                await db.execute(stmt)
            except Exception:
                # ALTER TABLE errors are expected if columns already exist
                pass
        await db.commit()


def _read_migration() -> str:
    with open(MIGRATION_PATH) as f:
        return f.read()


def _split_sql(sql: str) -> list[str]:
    """Split SQL file into individual statements."""
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


async def _insert_template(
    db_path: str,
    slug: str = "test-workflow",
    definition: dict | None = None,
) -> str:
    """Insert a test template and return its ID."""
    template_id = uuid.uuid4().hex
    if definition is None:
        definition = _simple_sequential_definition()

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO workflow_templates
               (id, slug, name, description, version, definition,
                parameter_schema, is_active, created_by)
               VALUES (?, ?, ?, ?, 1, ?, NULL, 1, 'test')""",
            (
                template_id,
                slug,
                f"Test: {slug}",
                "Test template",
                json.dumps(definition),
            ),
        )
        await db.commit()
    return template_id


def _simple_sequential_definition() -> dict:
    """A minimal sequential workflow: one step, no fan-out."""
    return {
        "steps": [
            {
                "id": "analyze",
                "type": "sequential",
                "depends_on": [],
                "agent": {
                    "task_template": "Analyze {{ topic }}",
                    "task_type": "analysis",
                },
            }
        ]
    }


def _multi_step_definition() -> dict:
    """A workflow with search (fan-out) -> merge (aggregate) -> clean (transform)."""
    return {
        "steps": [
            {
                "id": "search",
                "type": "fan_out",
                "depends_on": [],
                "over": "regions",
                "max_parallel": 10,
                "on_failure": "collect_all",
                "agent": {
                    "task_template": "Search for events in {{ region }}",
                    "task_type": "analysis",
                },
            },
            {
                "id": "merge",
                "type": "aggregate",
                "depends_on": ["search"],
                "input": "search.*",
                "strategy": "merge_arrays",
            },
            {
                "id": "clean",
                "type": "transform",
                "depends_on": ["merge"],
                "input": "merge",
                "operations": [
                    {"op": "deduplicate", "key": "name"},
                    {"op": "sort", "field": "name", "order": "asc"},
                    {"op": "limit", "count": 5},
                ],
            },
        ]
    }


@pytest.fixture
async def db_path(tmp_path):
    """Create an in-memory-like temp DB with migrations applied."""
    path = str(tmp_path / "test_workflow.db")
    await _init_db(path)
    return path


@pytest.fixture
def settings():
    return Settings(
        max_agents_per_execution=20,
        max_concurrent_agents=50,
        workflow_step_timeout_seconds=1800,
    )


@pytest.fixture
async def engine(db_path, settings):
    return WorkflowEngine(db_path=db_path, settings=settings)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_creates_execution_and_steps(db_path, settings):
    """start() should create an execution and its steps in the DB."""
    engine = WorkflowEngine(db_path=db_path, settings=settings)
    await _insert_template(db_path)

    exec_id = await engine.start(
        template_slug="test-workflow",
        parameters={"topic": "AI safety"},
        thread_id="thread-001",
    )

    assert exec_id is not None

    execution = await engine.get_execution(exec_id)
    assert execution is not None
    assert execution.status == ExecutionStatus.RUNNING
    assert execution.thread_id == "thread-001"
    assert execution.parameters == {"topic": "AI safety"}

    steps = await engine.get_steps(exec_id)
    assert len(steps) == 1
    assert steps[0].step_id == "analyze"
    assert steps[0].step_type == StepType.SEQUENTIAL


@pytest.mark.asyncio
async def test_advance_starts_next_step(db_path, settings):
    """advance() should start steps whose dependencies are met."""
    definition = _multi_step_definition()
    engine = WorkflowEngine(db_path=db_path, settings=settings)
    await _insert_template(db_path, definition=definition)

    exec_id = await engine.start(
        template_slug="test-workflow",
        parameters={"regions": ["Dallas", "Austin"]},
        thread_id="thread-002",
    )

    steps = await engine.get_steps(exec_id)
    search_step = next(s for s in steps if s.step_id == "search")
    merge_step = next(s for s in steps if s.step_id == "merge")

    # Search step should be running (started by start())
    assert search_step.status == StepStatus.RUNNING
    # Merge step should still be pending
    assert merge_step.status == StepStatus.PENDING

    # Simulate both agents completing
    await engine.handle_agent_result(
        exec_id, "search", 0, [{"name": "Event A", "location": "Dallas"}]
    )
    await engine.handle_agent_result(
        exec_id, "search", 1, [{"name": "Event B", "location": "Austin"}]
    )

    # After both agents complete, merge should have been started and completed
    steps = await engine.get_steps(exec_id)
    merge_step = next(s for s in steps if s.step_id == "merge")
    assert merge_step.status == StepStatus.COMPLETED


@pytest.mark.asyncio
async def test_handle_agent_result_completes_step(db_path, settings):
    """handle_agent_result should mark a sequential step as completed."""
    engine = WorkflowEngine(db_path=db_path, settings=settings)
    await _insert_template(db_path)

    exec_id = await engine.start(
        template_slug="test-workflow",
        parameters={"topic": "testing"},
        thread_id="thread-003",
    )

    result = {"findings": ["result1", "result2"]}
    await engine.handle_agent_result(exec_id, "analyze", 0, result)

    steps = await engine.get_steps(exec_id)
    assert steps[0].status == StepStatus.COMPLETED
    assert steps[0].output == result


@pytest.mark.asyncio
async def test_sequential_workflow_end_to_end(db_path, settings):
    """Full lifecycle: start -> agent result -> advance -> complete."""
    engine = WorkflowEngine(db_path=db_path, settings=settings)
    await _insert_template(db_path)

    exec_id = await engine.start(
        template_slug="test-workflow",
        parameters={"topic": "testing"},
        thread_id="thread-004",
    )

    # Execution should be running
    execution = await engine.get_execution(exec_id)
    assert execution.status == ExecutionStatus.RUNNING

    # Submit agent result
    result = {"summary": "Analysis complete"}
    await engine.handle_agent_result(exec_id, "analyze", 0, result)

    # Execution should be completed
    execution = await engine.get_execution(exec_id)
    assert execution.status == ExecutionStatus.COMPLETED
    assert execution.result == result


@pytest.mark.asyncio
async def test_cancel_marks_all_pending_skipped(db_path, settings):
    """cancel() should mark all pending/running steps as skipped."""
    definition = _multi_step_definition()
    engine = WorkflowEngine(db_path=db_path, settings=settings)
    await _insert_template(db_path, definition=definition)

    exec_id = await engine.start(
        template_slug="test-workflow",
        parameters={"regions": ["Dallas"]},
        thread_id="thread-005",
    )

    await engine.cancel(exec_id)

    execution = await engine.get_execution(exec_id)
    assert execution.status == ExecutionStatus.CANCELLED

    steps = await engine.get_steps(exec_id)
    for step in steps:
        if step.step_id != "search":
            # Non-started steps should be skipped
            assert step.status == StepStatus.SKIPPED


@pytest.mark.asyncio
async def test_estimate_returns_agent_count(db_path, settings):
    """estimate() should return correct agent count without executing."""
    definition = _multi_step_definition()
    engine = WorkflowEngine(db_path=db_path, settings=settings)
    await _insert_template(db_path, definition=definition)

    est = await engine.estimate(
        template_slug="test-workflow",
        parameters={"regions": ["Dallas", "Austin", "Houston"]},
    )

    assert est["estimated_agents"] == 3  # 3 regions
    assert est["estimated_steps"] == 3   # search, merge, clean


@pytest.mark.asyncio
async def test_cas_prevents_double_advance(db_path, settings):
    """Concurrent advance() calls should not double-start a step."""
    engine = WorkflowEngine(db_path=db_path, settings=settings)
    await _insert_template(db_path)

    exec_id = await engine.start(
        template_slug="test-workflow",
        parameters={"topic": "concurrency"},
        thread_id="thread-006",
    )

    # Manually reset step to pending and remove agent records to test CAS
    steps = await engine.get_steps(exec_id)
    step = steps[0]

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE workflow_steps SET status = 'pending' WHERE id = ?",
            (step.id,),
        )
        await db.execute(
            "DELETE FROM workflow_step_agents WHERE step_id = ?",
            (step.id,),
        )
        await db.commit()

    # Call advance concurrently — only one should start the step
    await asyncio.gather(
        engine.advance(exec_id),
        engine.advance(exec_id),
    )

    # Verify CAS worked: the step should have been started exactly once
    # (no errors from duplicate execution)
    steps = await engine.get_steps(exec_id)
    for s in steps:
        assert s.error is None

    # Count agent records — should be exactly 1 (not 2)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM workflow_step_agents WHERE step_id = ?",
            (step.id,),
        )
        row = await cursor.fetchone()
        assert row["cnt"] == 1


@pytest.mark.asyncio
async def test_aggregate_merges_arrays(db_path, settings):
    """Aggregate step with merge_arrays strategy should flatten arrays."""
    definition = _multi_step_definition()
    engine = WorkflowEngine(db_path=db_path, settings=settings)
    await _insert_template(db_path, definition=definition)

    exec_id = await engine.start(
        template_slug="test-workflow",
        parameters={"regions": ["Dallas", "Austin"]},
        thread_id="thread-007",
    )

    # Submit fan-out agent results (arrays of events)
    await engine.handle_agent_result(
        exec_id, "search", 0,
        [{"name": "Event A"}, {"name": "Event B"}],
    )
    await engine.handle_agent_result(
        exec_id, "search", 1,
        [{"name": "Event C"}, {"name": "Event D"}],
    )

    # Merge step should have completed with merged arrays
    steps = await engine.get_steps(exec_id)
    merge_step = next(s for s in steps if s.step_id == "merge")
    assert merge_step.status == StepStatus.COMPLETED

    # Should be a flat list of 4 events
    output = merge_step.output
    assert isinstance(output, list)
    assert len(output) == 4
    names = [e["name"] for e in output]
    assert "Event A" in names
    assert "Event D" in names


@pytest.mark.asyncio
async def test_transform_deduplicates(db_path, settings):
    """Transform step should deduplicate by key."""
    definition = _multi_step_definition()
    engine = WorkflowEngine(db_path=db_path, settings=settings)
    await _insert_template(db_path, definition=definition)

    exec_id = await engine.start(
        template_slug="test-workflow",
        parameters={"regions": ["Dallas", "Austin"]},
        thread_id="thread-008",
    )

    # Submit results with duplicates
    await engine.handle_agent_result(
        exec_id, "search", 0,
        [{"name": "Event A"}, {"name": "Event B"}],
    )
    await engine.handle_agent_result(
        exec_id, "search", 1,
        [{"name": "Event A"}, {"name": "Event C"}],  # "Event A" is duplicate
    )

    # After merge + transform, duplicates should be removed
    steps = await engine.get_steps(exec_id)
    clean_step = next(s for s in steps if s.step_id == "clean")
    assert clean_step.status == StepStatus.COMPLETED

    output = clean_step.output
    assert isinstance(output, list)
    names = [e["name"] for e in output]
    # Deduplication by "name" should remove the duplicate "Event A"
    assert names.count("Event A") == 1
    assert "Event B" in names
    assert "Event C" in names
    # Sorted ascending by name
    assert names == sorted(names)
