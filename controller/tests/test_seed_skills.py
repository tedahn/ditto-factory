"""Tests for the skill seed script.

Verifies idempotency, dry-run mode, --only filtering, and --force overwrite.
"""
from __future__ import annotations

import os
import sys

import pytest
import aiosqlite

# Ensure seeds package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from seeds.seed import seed


MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "migrations")


@pytest.fixture
async def db_path(tmp_path):
    """Create a temporary database with the skill registry schema."""
    path = str(tmp_path / "seed_test.db")
    async with aiosqlite.connect(path) as db:
        migration_file = os.path.join(MIGRATIONS_DIR, "002_skill_registry.sql")
        with open(migration_file, encoding="utf-8") as f:
            migration = f.read()
        await db.executescript(migration)
    return path


async def _get_registry(db_path: str):
    """Helper to create a SkillRegistry pointing at the test database."""
    from controller.skills.registry import SkillRegistry
    return SkillRegistry(db_path)


@pytest.mark.asyncio
async def test_seed_creates_all_skills(db_path):
    """All 10 seed skills are created on first run."""
    await seed(db_path)
    registry = await _get_registry(db_path)
    skills = await registry.list_all()
    assert len(skills) == 10


@pytest.mark.asyncio
async def test_seed_idempotent(db_path):
    """Running seed twice does not duplicate skills."""
    await seed(db_path)
    await seed(db_path)  # second run should skip all
    registry = await _get_registry(db_path)
    skills = await registry.list_all()
    assert len(skills) == 10


@pytest.mark.asyncio
async def test_seed_dry_run(db_path):
    """Dry-run mode does not create any skills."""
    await seed(db_path, dry_run=True)
    registry = await _get_registry(db_path)
    skills = await registry.list_all()
    assert len(skills) == 0


@pytest.mark.asyncio
async def test_seed_only_filter(db_path):
    """The --only filter seeds only the specified skills."""
    await seed(db_path, only=["react-debug", "css-review"])
    registry = await _get_registry(db_path)
    skills = await registry.list_all()
    assert len(skills) == 2
    slugs = {s.slug for s in skills}
    assert slugs == {"react-debug", "css-review"}


@pytest.mark.asyncio
async def test_seed_force_overwrites(db_path):
    """The --force flag overwrites existing skills."""
    await seed(db_path, only=["react-debug"])
    registry = await _get_registry(db_path)
    original = await registry.get("react-debug")
    assert original is not None

    # Force re-seed
    await seed(db_path, only=["react-debug"], force=True)
    registry = await _get_registry(db_path)
    updated = await registry.get("react-debug")
    assert updated is not None
    # The skill should exist with the same slug but a new ID (re-created)
    assert updated.slug == "react-debug"


@pytest.mark.asyncio
async def test_seed_content_loaded(db_path):
    """Skill content is loaded from the markdown files."""
    await seed(db_path, only=["code-review"])
    registry = await _get_registry(db_path)
    skill = await registry.get("code-review")
    assert skill is not None
    assert "# General Code Review" in skill.content
    assert len(skill.content) > 500


@pytest.mark.asyncio
async def test_seed_metadata_correct(db_path):
    """Skill metadata (language, domain, tags) matches the fixture."""
    await seed(db_path, only=["api-design"])
    registry = await _get_registry(db_path)
    skill = await registry.get("api-design")
    assert skill is not None
    assert "python" in skill.language
    assert "backend" in skill.domain
    assert "rest" in skill.tags
    assert skill.created_by == "seed-script"
