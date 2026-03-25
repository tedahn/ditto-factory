"""Tests for the report step executor in the workflow engine.

Validates that the report step:
- Stores final result on the execution
- Runs quality checks and includes score
- Marks execution as completed
- Handles empty input gracefully
"""

from __future__ import annotations

import json
import os
import uuid

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


async def _init_db(db_path: str) -> None:
    """Run migration 004 against a temp SQLite DB."""
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


def _report_workflow_definition() -> dict:
    """Workflow: sequential -> report."""
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
            },
            {
                "id": "deliver",
                "type": "report",
                "depends_on": ["analyze"],
                "report": {
                    "input": "analyze",
                },
            },
        ]
    }


async def _insert_template(db_path: str, definition: dict) -> str:
    template_id = uuid.uuid4().hex
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO workflow_templates
               (id, slug, name, description, version, definition,
                parameter_schema, is_active, created_by)
               VALUES (?, ?, ?, ?, 1, ?, NULL, 1, 'test')""",
            (
                template_id,
                "test-report",
                "Test Report Workflow",
                "Test",
                json.dumps(definition),
            ),
        )
        await db.commit()
    return template_id


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "test_report.db")
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
# Helper to set up a workflow execution with a completed analyze step
# ---------------------------------------------------------------------------


async def _setup_execution_with_analyze_done(
    db_path: str, engine: WorkflowEngine, analyze_output: dict | list | None = None,
) -> str:
    """Start a workflow and simulate the analyze step completing."""
    await _insert_template(db_path, _report_workflow_definition())

    execution_id = await engine.start(
        template_slug="test-report",
        parameters={"topic": "AI safety"},
        thread_id="thread-1",
    )

    # Simulate agent result for the sequential "analyze" step
    if analyze_output is None:
        analyze_output = {
            "result": [
                {"name": "Finding 1", "source": "google", "url": "https://a.com"},
                {"name": "Finding 2", "source": "bing", "url": "https://b.com"},
            ]
        }

    await engine.handle_agent_result(
        execution_id=execution_id,
        step_id="analyze",
        agent_index=0,
        result=analyze_output,
    )
    return execution_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_step_stores_result(db_path, engine):
    """Report step stores final result on the execution."""
    execution_id = await _setup_execution_with_analyze_done(db_path, engine)

    execution = await engine.get_execution(execution_id)
    assert execution is not None
    assert execution.result is not None
    assert "data" in execution.result
    assert len(execution.result["data"]) == 2


@pytest.mark.asyncio
async def test_report_step_runs_quality_checks(db_path, engine):
    """Quality score is included in the execution result."""
    execution_id = await _setup_execution_with_analyze_done(db_path, engine)

    execution = await engine.get_execution(execution_id)
    assert execution is not None
    assert "quality" in execution.result
    quality = execution.result["quality"]
    assert "score" in quality
    assert 0.0 <= quality["score"] <= 1.0
    assert "checks" in quality
    assert "total_items" in quality
    assert "valid_items" in quality


@pytest.mark.asyncio
async def test_report_step_marks_execution_complete(db_path, engine):
    """Execution status is COMPLETED after report step runs."""
    execution_id = await _setup_execution_with_analyze_done(db_path, engine)

    execution = await engine.get_execution(execution_id)
    assert execution is not None
    assert execution.status == ExecutionStatus.COMPLETED

    # Report step itself should also be completed
    steps = await engine.get_steps(execution_id)
    report_step = next(s for s in steps if s.step_id == "deliver")
    assert report_step.status == StepStatus.COMPLETED
    assert report_step.output is not None
    assert report_step.output.get("delivered") is True
    assert "quality_score" in report_step.output


@pytest.mark.asyncio
async def test_report_step_with_empty_data(db_path, engine):
    """Report step handles empty input gracefully."""
    execution_id = await _setup_execution_with_analyze_done(
        db_path, engine, analyze_output={"result": []}
    )

    execution = await engine.get_execution(execution_id)
    assert execution is not None
    assert execution.status == ExecutionStatus.COMPLETED
    quality = execution.result["quality"]
    assert quality["score"] == 0.0
    assert quality["total_items"] == 0
    assert "Empty dataset" in quality["warnings"]
