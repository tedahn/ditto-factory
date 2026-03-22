#!/usr/bin/env python3
"""Seed the skill registry with starter skills.

Usage:
    python -m seeds.seed --db-path /path/to/db.sqlite
    python -m seeds.seed --db-path /path/to/db.sqlite --dry-run
    python -m seeds.seed --db-path /path/to/db.sqlite --only react-debug,css-review
    python -m seeds.seed --db-path /path/to/db.sqlite --force  # overwrite existing
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

# Add parent directory to path so controller package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from controller.skills.models import SkillCreate
from controller.skills.registry import SkillRegistry


async def seed(
    db_path: str,
    dry_run: bool = False,
    only: list[str] | None = None,
    force: bool = False,
) -> None:
    """Seed skills into the registry from skills.json and content files.

    This function is idempotent: running it multiple times will skip
    skills that already exist unless --force is specified.
    """
    seeds_dir = os.path.dirname(os.path.abspath(__file__))

    # Load fixture
    fixture_path = os.path.join(seeds_dir, "skills.json")
    with open(fixture_path, encoding="utf-8") as f:
        skills_data: list[dict] = json.load(f)

    registry = SkillRegistry(db_path)

    created = 0
    skipped = 0
    overwritten = 0

    for skill_data in skills_data:
        slug = skill_data["slug"]

        if only and slug not in only:
            continue

        # Load content from the corresponding markdown file
        content_file = skill_data.pop("content_file")
        content_path = os.path.join(seeds_dir, "skills", content_file)
        with open(content_path, encoding="utf-8") as f:
            skill_data["content"] = f.read()

        skill_data["created_by"] = "seed-script"

        # Check if skill already exists
        existing = await registry.get(slug)
        if existing and not force:
            print(f"  SKIP {slug} (already exists, use --force to overwrite)")
            skipped += 1
            continue

        if dry_run:
            print(f"  DRY-RUN would {'overwrite' if existing else 'create'}: {slug}")
            continue

        if existing and force:
            # Hard delete (not soft) to avoid UNIQUE constraint on re-create
            import aiosqlite
            async with aiosqlite.connect(db_path) as db:
                await db.execute("DELETE FROM skills WHERE slug = ?", (slug,))
                await db.commit()
            overwritten += 1

        await registry.create(SkillCreate(**skill_data))
        print(f"  {'OVERWRITE' if existing else 'CREATED'} {slug}")
        created += 1

    action = "Would seed" if dry_run else "Seeded"
    print(f"\n{action} {created} skills ({skipped} skipped, {overwritten} overwritten) into {db_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed skill registry with starter skills")
    parser.add_argument("--db-path", required=True, help="Path to SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be created without writing")
    parser.add_argument("--only", help="Comma-separated list of skill slugs to seed")
    parser.add_argument("--force", action="store_true", help="Overwrite existing skills")
    args = parser.parse_args()

    only = args.only.split(",") if args.only else None
    asyncio.run(seed(args.db_path, dry_run=args.dry_run, only=only, force=args.force))


if __name__ == "__main__":
    main()
