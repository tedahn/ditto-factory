"""Tests for workflow template seeding."""
from __future__ import annotations

import os
import tempfile

import aiosqlite
import pytest

from controller.workflows.compiler import WorkflowCompiler
from controller.workflows.templates import TemplateCRUD

# Import the seed function; seeds dir is outside the package
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "seeds"))
from seed_templates import seed_templates  # noqa: E402


MIGRATION_PATH = os.path.join(
    os.path.dirname(__file__), "..", "migrations", "004_workflow_engine.sql"
)


@pytest.fixture
async def db_path():
    """Create a temporary SQLite database with workflow engine schema."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        path = f.name

    with open(MIGRATION_PATH, encoding="utf-8") as f:
        migration_sql = f.read()

    async with aiosqlite.connect(path) as db:
        # The migration tries to ALTER a 'jobs' table that doesn't exist
        # in isolation. Run only the CREATE TABLE statements.
        for statement in migration_sql.split(";"):
            stmt = statement.strip()
            if not stmt:
                continue
            if stmt.upper().startswith("ALTER"):
                continue  # skip ALTER TABLE jobs (table doesn't exist in test)
            try:
                await db.execute(stmt)
            except Exception:
                pass  # ignore index-on-missing-table errors
        await db.commit()

    yield path
    os.unlink(path)


@pytest.mark.asyncio
async def test_seed_creates_all_templates(db_path):
    """Seeding should create all three starter templates."""
    await seed_templates(db_path)

    crud = TemplateCRUD(db_path)
    templates = await crud.list_all()
    slugs = {t.slug for t in templates}

    assert len(templates) == 3
    assert slugs == {"single-task", "geo-search", "multi-source-analysis"}


@pytest.mark.asyncio
async def test_seed_idempotent(db_path):
    """Running seed twice should not duplicate templates."""
    await seed_templates(db_path)
    await seed_templates(db_path)

    crud = TemplateCRUD(db_path)
    templates = await crud.list_all()
    assert len(templates) == 3


@pytest.mark.asyncio
async def test_seed_force_overwrites(db_path):
    """Seeding with force=True should overwrite existing templates."""
    await seed_templates(db_path)

    crud = TemplateCRUD(db_path)
    original = await crud.get("geo-search")
    assert original is not None
    original_id = original.id

    await seed_templates(db_path, force=True)

    updated = await crud.get("geo-search")
    assert updated is not None
    # Force re-creates, so the ID should change
    assert updated.id != original_id


@pytest.mark.asyncio
async def test_seed_dry_run_creates_nothing(db_path):
    """Dry-run should not create any templates."""
    await seed_templates(db_path, dry_run=True)

    crud = TemplateCRUD(db_path)
    templates = await crud.list_all()
    assert len(templates) == 0


@pytest.mark.asyncio
async def test_single_task_template_structure(db_path):
    """Verify the single-task template has the expected structure."""
    await seed_templates(db_path)

    crud = TemplateCRUD(db_path)
    template = await crud.get("single-task")
    assert template is not None
    assert template.name == "Single Task (Default)"

    steps = template.definition["steps"]
    assert len(steps) == 1
    assert steps[0]["id"] == "execute"
    assert steps[0]["type"] == "sequential"
    assert steps[0]["agent"]["task_template"] == "{{ task }}"


@pytest.mark.asyncio
async def test_single_task_template_compiles(db_path):
    """Verify the single-task template compiles with sample params."""
    await seed_templates(db_path)

    crud = TemplateCRUD(db_path)
    template = await crud.get("single-task")
    assert template is not None

    compiler = WorkflowCompiler(max_agents_per_execution=20)
    steps = compiler.compile(
        template.definition,
        {"task": "Fix the login bug"},
        template.parameter_schema,
    )
    assert len(steps) == 1
    assert steps[0].step_id == "execute"
    assert steps[0].input["task"] == "Fix the login bug"


@pytest.mark.asyncio
async def test_geo_search_template_compiles(db_path):
    """Verify the geo-search template can be compiled with sample params."""
    await seed_templates(db_path)

    crud = TemplateCRUD(db_path)
    template = await crud.get("geo-search")
    assert template is not None

    compiler = WorkflowCompiler(max_agents_per_execution=20)
    steps = compiler.compile(
        template.definition,
        {
            "query": "music",
            "regions": ["dallas", "plano"],
            "sources": ["google", "eventbrite"],
        },
        template.parameter_schema,
    )
    # 4 steps: search (fan_out), merge (aggregate), dedupe (transform), deliver (report)
    assert len(steps) == 4
    step_ids = [s.step_id for s in steps]
    assert step_ids == ["search", "merge", "dedupe", "deliver"]

    # Fan-out should produce 2 regions x 2 sources = 4 agents
    search_step = steps[0]
    assert len(search_step.input["agents"]) == 4


@pytest.mark.asyncio
async def test_multi_source_analysis_template_compiles(db_path):
    """Verify the multi-source-analysis template compiles."""
    await seed_templates(db_path)

    crud = TemplateCRUD(db_path)
    template = await crud.get("multi-source-analysis")
    assert template is not None

    compiler = WorkflowCompiler(max_agents_per_execution=20)
    steps = compiler.compile(
        template.definition,
        {"topic": "AI trends", "sources": ["arxiv", "hacker-news", "reddit"]},
        template.parameter_schema,
    )
    # 3 steps: research (fan_out), merge (aggregate), deliver (report)
    assert len(steps) == 3

    # Fan-out should produce 3 agents (one per source)
    research_step = steps[0]
    assert len(research_step.input["agents"]) == 3
