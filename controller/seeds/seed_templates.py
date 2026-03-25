#!/usr/bin/env python3
"""Seed the workflow template registry with starter templates.

Usage:
    python -m seeds.seed_templates --db-path /path/to/db.sqlite
    python -m seeds.seed_templates --db-path /path/to/db.sqlite --dry-run
    python -m seeds.seed_templates --db-path /path/to/db.sqlite --force  # overwrite existing
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

# Add parent directory to path so controller package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import aiosqlite

from controller.workflows.models import WorkflowTemplateCreate
from controller.workflows.templates import TemplateCRUD


async def seed_templates(
    db_path: str,
    dry_run: bool = False,
    force: bool = False,
) -> None:
    """Seed workflow templates from workflow_templates.json.

    This function is idempotent: running it multiple times will skip
    templates that already exist unless --force is specified.
    """
    seeds_dir = os.path.dirname(os.path.abspath(__file__))
    fixture_path = os.path.join(seeds_dir, "workflow_templates.json")

    with open(fixture_path, encoding="utf-8") as f:
        templates_data: list[dict] = json.load(f)

    crud = TemplateCRUD(db_path)

    created = 0
    skipped = 0
    overwritten = 0

    for tmpl in templates_data:
        slug = tmpl["slug"]

        existing = await crud.get(slug)
        if existing and not force:
            print(f"  SKIP {slug} (already exists, use --force to overwrite)")
            skipped += 1
            continue

        if dry_run:
            print(f"  DRY-RUN would {'overwrite' if existing else 'create'}: {slug}")
            continue

        if existing and force:
            # Hard delete to avoid UNIQUE constraint on re-create
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    "DELETE FROM workflow_templates WHERE slug = ?", (slug,)
                )
                await db.commit()
            overwritten += 1

        await crud.create(
            WorkflowTemplateCreate(
                slug=tmpl["slug"],
                name=tmpl["name"],
                description=tmpl["description"],
                definition=tmpl["definition"],
                parameter_schema=tmpl.get("parameter_schema"),
                created_by="seed-script",
            )
        )
        print(f"  {'OVERWRITE' if existing else 'CREATED'} {slug}")
        created += 1

    action = "Would seed" if dry_run else "Seeded"
    print(
        f"\n{action} {created} templates "
        f"({skipped} skipped, {overwritten} overwritten) into {db_path}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed workflow template registry with starter templates"
    )
    parser.add_argument("--db-path", required=True, help="Path to SQLite database")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be created without writing",
    )
    parser.add_argument(
        "--force", action="store_true", help="Overwrite existing templates"
    )
    args = parser.parse_args()

    asyncio.run(seed_templates(args.db_path, dry_run=args.dry_run, force=args.force))


if __name__ == "__main__":
    main()
