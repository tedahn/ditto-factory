"""Tests for TemplateCRUD — workflow template CRUD operations.

Uses file-based SQLite with migration 004 applied.
"""

from __future__ import annotations

import os

import aiosqlite
import pytest

from controller.workflows.models import WorkflowTemplateCreate, WorkflowTemplateUpdate
from controller.workflows.templates import TemplateCRUD


# ---------------------------------------------------------------------------
# Fixtures
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
    statements: list[str] = []
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
        # Create stub jobs table (required by ALTER TABLE in migration)
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


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "test_templates.db")
    await _init_db(path)
    return path


@pytest.fixture
async def crud(db_path):
    return TemplateCRUD(db_path)


SAMPLE_DEFINITION = {
    "steps": [
        {
            "id": "search",
            "type": "fan_out",
            "agent": {"task_template": "Search {{ region }}", "task_type": "analysis"},
            "fan_out": {"over": "regions", "max_parallel": 5},
        }
    ]
}

SAMPLE_SCHEMA = {
    "type": "object",
    "required": ["regions"],
    "properties": {"regions": {"type": "array"}},
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_template(crud):
    payload = WorkflowTemplateCreate(
        slug="intel-scan",
        name="Intelligence Scan",
        description="Multi-region scan",
        definition=SAMPLE_DEFINITION,
        parameter_schema=SAMPLE_SCHEMA,
        created_by="test-user",
    )
    template = await crud.create(payload)

    assert template.slug == "intel-scan"
    assert template.name == "Intelligence Scan"
    assert template.version == 1
    assert template.definition == SAMPLE_DEFINITION
    assert template.parameter_schema == SAMPLE_SCHEMA
    assert template.is_active is True


@pytest.mark.asyncio
async def test_get_template(crud):
    payload = WorkflowTemplateCreate(
        slug="get-test",
        name="Get Test",
        description="Test get",
        definition=SAMPLE_DEFINITION,
        created_by="test",
    )
    await crud.create(payload)

    result = await crud.get("get-test")
    assert result is not None
    assert result.slug == "get-test"

    # Non-existent slug
    missing = await crud.get("does-not-exist")
    assert missing is None


@pytest.mark.asyncio
async def test_update_template(crud):
    payload = WorkflowTemplateCreate(
        slug="update-test",
        name="Update Test",
        description="Original description",
        definition=SAMPLE_DEFINITION,
        created_by="test",
    )
    await crud.create(payload)

    new_def = {"steps": [{"id": "step2", "type": "sequential"}]}
    update = WorkflowTemplateUpdate(
        definition=new_def,
        description="Updated description",
        changelog="Changed step structure",
        updated_by="updater",
    )
    updated = await crud.update("update-test", update)

    assert updated is not None
    assert updated.version == 2
    assert updated.description == "Updated description"
    assert updated.definition == new_def


@pytest.mark.asyncio
async def test_delete_template(crud):
    payload = WorkflowTemplateCreate(
        slug="delete-test",
        name="Delete Test",
        description="To be deleted",
        definition=SAMPLE_DEFINITION,
        created_by="test",
    )
    await crud.create(payload)

    deleted = await crud.delete("delete-test")
    assert deleted is True

    # Should not be retrievable after soft-delete
    result = await crud.get("delete-test")
    assert result is None

    # Double delete returns False
    deleted_again = await crud.delete("delete-test")
    assert deleted_again is False


@pytest.mark.asyncio
async def test_list_templates(crud):
    for i in range(3):
        payload = WorkflowTemplateCreate(
            slug=f"list-test-{i}",
            name=f"List Test {i}",
            description=f"Template {i}",
            definition=SAMPLE_DEFINITION,
            created_by="test",
        )
        await crud.create(payload)

    templates = await crud.list_all()
    assert len(templates) == 3
    # Should be ordered by name
    names = [t.name for t in templates]
    assert names == sorted(names)


@pytest.mark.asyncio
async def test_rollback_template(crud):
    payload = WorkflowTemplateCreate(
        slug="rollback-test",
        name="Rollback Test",
        description="Version 1",
        definition={"steps": [{"id": "v1", "type": "sequential"}]},
        created_by="test",
    )
    await crud.create(payload)

    # Update to v2
    await crud.update(
        "rollback-test",
        WorkflowTemplateUpdate(
            definition={"steps": [{"id": "v2", "type": "sequential"}]},
            changelog="v2 changes",
            updated_by="test",
        ),
    )

    # Verify v2
    t = await crud.get("rollback-test")
    assert t is not None
    assert t.version == 2
    assert t.definition["steps"][0]["id"] == "v2"

    # Rollback to v1
    rolled_back = await crud.rollback("rollback-test", target_version=1)
    assert rolled_back is not None
    assert rolled_back.version == 3  # new version after rollback
    assert rolled_back.definition["steps"][0]["id"] == "v1"

    # Version history should have 3 entries
    versions = await crud.get_versions("rollback-test")
    assert len(versions) == 3
    assert versions[0]["version"] == 3
    assert "Rollback" in (versions[0]["changelog"] or "")
