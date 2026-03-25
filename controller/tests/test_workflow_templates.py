"""Tests for TemplateCRUD workflow template registry."""

from __future__ import annotations

import os
import tempfile

import aiosqlite
import pytest
import pytest_asyncio

from controller.workflows.models import (
    WorkflowTemplateCreate,
    WorkflowTemplateUpdate,
)
from controller.workflows.templates import TemplateCRUD

MIGRATION_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    os.pardir,
    "migrations",
    "004_workflow_engine.sql",
)


@pytest_asyncio.fixture
async def crud(tmp_path):
    """Create a TemplateCRUD backed by a temporary SQLite database."""
    db_path = str(tmp_path / "test.db")
    # Read and apply migration SQL (skip ALTER TABLE lines that need the jobs table)
    with open(MIGRATION_PATH) as f:
        migration_sql = f.read()

    # Strip SQL comment lines before splitting into statements
    lines = [line for line in migration_sql.splitlines() if not line.strip().startswith("--")]
    clean_sql = "\n".join(lines)

    async with aiosqlite.connect(db_path) as db:
        for statement in clean_sql.split(";"):
            stmt = statement.strip()
            if not stmt:
                continue
            # Skip ALTER TABLE statements that reference the jobs table
            if "ALTER TABLE jobs" in stmt:
                continue
            try:
                await db.execute(stmt)
            except Exception:
                pass  # Ignore errors from index-only statements
        await db.commit()

    return TemplateCRUD(db_path)


def _sample_create(**overrides) -> WorkflowTemplateCreate:
    """Build a sample WorkflowTemplateCreate with defaults."""
    defaults = dict(
        slug="security-scan",
        name="Security Scan",
        description="Multi-region security scan workflow",
        definition={
            "steps": [
                {"id": "search", "type": "fan_out"},
                {"id": "aggregate", "type": "aggregate"},
            ]
        },
        parameter_schema={"type": "object", "properties": {"regions": {"type": "array"}}},
        created_by="test-user",
    )
    defaults.update(overrides)
    return WorkflowTemplateCreate(**defaults)


@pytest.mark.asyncio
async def test_create_and_get(crud: TemplateCRUD):
    """Create a template and retrieve it by slug."""
    created = await crud.create(_sample_create())

    assert created.slug == "security-scan"
    assert created.name == "Security Scan"
    assert created.version == 1
    assert created.is_active is True
    assert created.definition["steps"][0]["id"] == "search"
    assert created.parameter_schema is not None
    assert created.created_by == "test-user"

    fetched = await crud.get("security-scan")
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.definition == created.definition

    # Non-existent slug returns None
    assert await crud.get("nonexistent") is None


@pytest.mark.asyncio
async def test_update_creates_version(crud: TemplateCRUD):
    """Updating a template should bump the version and create a version record."""
    await crud.create(_sample_create())

    updated = await crud.update(
        "security-scan",
        WorkflowTemplateUpdate(
            definition={"steps": [{"id": "search-v2", "type": "fan_out"}]},
            description="Updated description",
            changelog="Changed search step",
            updated_by="updater",
        ),
    )

    assert updated is not None
    assert updated.version == 2
    assert updated.description == "Updated description"
    assert updated.definition["steps"][0]["id"] == "search-v2"

    # Should have 2 version records
    versions = await crud.get_versions("security-scan")
    assert len(versions) == 2
    assert versions[0]["version"] == 2  # Descending order
    assert versions[1]["version"] == 1


@pytest.mark.asyncio
async def test_soft_delete(crud: TemplateCRUD):
    """Deleting a template should soft-delete it (is_active=0)."""
    await crud.create(_sample_create())

    result = await crud.delete("security-scan")
    assert result is True

    # Should not be retrievable
    assert await crud.get("security-scan") is None

    # Deleting again returns False
    assert await crud.delete("security-scan") is False


@pytest.mark.asyncio
async def test_list_all(crud: TemplateCRUD):
    """List all active templates."""
    await crud.create(_sample_create(slug="alpha", name="Alpha"))
    await crud.create(_sample_create(slug="beta", name="Beta"))
    await crud.create(_sample_create(slug="gamma", name="Gamma"))

    # Soft-delete one
    await crud.delete("beta")

    templates = await crud.list_all()
    slugs = [t.slug for t in templates]
    assert "alpha" in slugs
    assert "gamma" in slugs
    assert "beta" not in slugs
    assert len(templates) == 2


@pytest.mark.asyncio
async def test_rollback(crud: TemplateCRUD):
    """Rollback should restore a previous version's definition."""
    await crud.create(_sample_create())

    # Update to v2
    await crud.update(
        "security-scan",
        WorkflowTemplateUpdate(
            definition={"steps": [{"id": "v2-step", "type": "sequential"}]},
            changelog="v2 changes",
        ),
    )

    # Rollback to v1
    rolled = await crud.rollback("security-scan", target_version=1)
    assert rolled is not None
    assert rolled.version == 3  # v1 -> v2 -> v3 (rollback)
    assert rolled.definition["steps"][0]["id"] == "search"  # Original definition

    # Version history should have 3 entries
    versions = await crud.get_versions("security-scan")
    assert len(versions) == 3
    assert versions[0]["changelog"] == "Rollback to version 1"

    # Rollback to non-existent version returns None
    assert await crud.rollback("security-scan", target_version=99) is None

    # Rollback non-existent slug returns None
    assert await crud.rollback("nonexistent", target_version=1) is None


@pytest.mark.asyncio
async def test_get_versions(crud: TemplateCRUD):
    """Get version history returns versions in descending order."""
    await crud.create(_sample_create())
    await crud.update(
        "security-scan",
        WorkflowTemplateUpdate(
            definition={"steps": [{"id": "v2", "type": "fan_out"}]},
            changelog="Second version",
            updated_by="editor",
        ),
    )
    await crud.update(
        "security-scan",
        WorkflowTemplateUpdate(
            definition={"steps": [{"id": "v3", "type": "fan_out"}]},
            changelog="Third version",
            updated_by="editor",
        ),
    )

    versions = await crud.get_versions("security-scan")
    assert len(versions) == 3
    assert [v["version"] for v in versions] == [3, 2, 1]
    assert versions[0]["changelog"] == "Third version"
    assert versions[2]["changelog"] is None  # Initial version has no changelog

    # Each version has expected fields
    for v in versions:
        assert "id" in v
        assert "template_id" in v
        assert "definition" in v
        assert "created_at" in v
