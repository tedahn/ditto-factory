"""Tests for Workflow Engine crash recovery (reconcile).

Uses in-memory SQLite with migration 004 applied.
Inserts test data directly via SQL to simulate crash scenarios.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from controller.config import Settings
from controller.workflows.engine import WorkflowEngine
from controller.workflows.models import ExecutionStatus, StepStatus, StepType


# ---------------------------------------------------------------------------
# Fixtures (reuse pattern from test_workflow_engine.py)
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


async def _insert_template(db_path: str) -> str:
    template_id = uuid.uuid4().hex
    definition = {
        "steps": [
            {
                "id": "step1",
                "type": "sequential",
                "depends_on": [],
                "agent": {"task_template": "Do work", "task_type": "analysis"},
            }
        ]
    }
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO workflow_templates
               (id, slug, name, description, version, definition,
                parameter_schema, is_active, created_by)
               VALUES (?, 'test-recovery', 'Test Recovery', 'test', 1, ?, NULL, 1, 'test')""",
            (template_id, json.dumps(definition)),
        )
        await db.commit()
    return template_id


async def _insert_execution(
    db_path: str,
    template_id: str,
    status: str = "running",
) -> str:
    execution_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO workflow_executions
               (id, template_id, template_version, thread_id, parameters,
                status, started_at)
               VALUES (?, ?, 1, 'thread-1', '{}', ?, ?)""",
            (execution_id, template_id, status, now),
        )
        await db.commit()
    return execution_id


async def _insert_step(
    db_path: str,
    execution_id: str,
    step_id: str = "step1",
    step_type: str = "sequential",
    status: str = "pending",
    started_at: str | None = None,
    agent_jobs: str = "[]",
    input_data: dict | None = None,
) -> str:
    row_id = uuid.uuid4().hex
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO workflow_steps
               (id, execution_id, step_id, step_type, status, input,
                agent_jobs, started_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row_id,
                execution_id,
                step_id,
                step_type,
                status,
                json.dumps(input_data) if input_data else None,
                agent_jobs,
                started_at,
            ),
        )
        await db.commit()
    return row_id


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_recovery.db")


@pytest.fixture
def settings():
    return Settings(
        workflow_step_timeout_seconds=300,  # 5 minutes for tests
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_no_orphans(db_path, settings):
    """No running executions -> returns zeros."""
    await _init_db(db_path)
    engine = WorkflowEngine(db_path=db_path, settings=settings)

    result = await engine.reconcile()

    assert result == {"reconciled": 0, "failed": 0, "completed": 0}


@pytest.mark.asyncio
async def test_reconcile_timed_out_step(db_path, settings):
    """A running agent step past timeout gets marked failed."""
    await _init_db(db_path)
    template_id = await _insert_template(db_path)
    execution_id = await _insert_execution(db_path, template_id, status="running")

    # Insert a step that started 10 minutes ago (past 5-min timeout)
    past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    step_row_id = await _insert_step(
        db_path,
        execution_id,
        step_id="step1",
        step_type="sequential",
        status="running",
        started_at=past,
        agent_jobs=json.dumps(["job-abc"]),
    )

    engine = WorkflowEngine(db_path=db_path, settings=settings)
    result = await engine.reconcile()

    assert result["failed"] >= 1

    # Verify the step was marked failed
    steps = await engine.get_steps(execution_id)
    step = [s for s in steps if s.id == step_row_id][0]
    assert step.status == StepStatus.FAILED
    assert "timed out" in (step.error or "")


@pytest.mark.asyncio
async def test_reconcile_stuck_deterministic_step(db_path, settings):
    """An aggregate step stuck in running gets marked failed."""
    await _init_db(db_path)
    template_id = await _insert_template(db_path)
    execution_id = await _insert_execution(db_path, template_id, status="running")

    step_row_id = await _insert_step(
        db_path,
        execution_id,
        step_id="merge",
        step_type="aggregate",
        status="running",
    )

    engine = WorkflowEngine(db_path=db_path, settings=settings)
    result = await engine.reconcile()

    assert result["failed"] >= 1

    steps = await engine.get_steps(execution_id)
    step = [s for s in steps if s.id == step_row_id][0]
    assert step.status == StepStatus.FAILED
    assert "Deterministic step" in (step.error or "")


@pytest.mark.asyncio
async def test_reconcile_advances_pending(db_path, settings):
    """When a running step is failed and pending steps exist, advance is called."""
    await _init_db(db_path)
    template_id = await _insert_template(db_path)
    execution_id = await _insert_execution(db_path, template_id, status="running")

    # A deterministic step stuck running (will be marked failed by reconcile)
    await _insert_step(
        db_path,
        execution_id,
        step_id="merge",
        step_type="aggregate",
        status="running",
    )
    # A pending step with no dependencies (should be advanced)
    await _insert_step(
        db_path,
        execution_id,
        step_id="report",
        step_type="report",
        status="pending",
        input_data={"depends_on": []},
    )

    engine = WorkflowEngine(db_path=db_path, settings=settings)
    result = await engine.reconcile()

    # The stuck step was failed, then advance was called for pending steps
    assert result["failed"] >= 1
    # After reconcile, execution should have been advanced
    # (advance will try to start the pending report step, which will
    # fail or complete depending on state, but reconciled count should be > 0
    # OR completed count should be > 0)
    assert (result["reconciled"] + result["completed"]) >= 1


@pytest.mark.asyncio
async def test_reconcile_completes_execution(db_path, settings):
    """All steps completed -> execution marked completed."""
    await _init_db(db_path)
    template_id = await _insert_template(db_path)
    execution_id = await _insert_execution(db_path, template_id, status="running")

    # Insert a single completed step
    now = datetime.now(timezone.utc).isoformat()
    await _insert_step(
        db_path,
        execution_id,
        step_id="step1",
        step_type="sequential",
        status="completed",
        started_at=now,
    )

    engine = WorkflowEngine(db_path=db_path, settings=settings)
    result = await engine.reconcile()

    assert result["completed"] == 1

    # Verify execution is now completed
    execution = await engine.get_execution(execution_id)
    assert execution.status == ExecutionStatus.COMPLETED
