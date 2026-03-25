"""Workflow Template CRUD registry.

Manages versioned workflow template definitions with full CRUD,
version history, and rollback support. Follows the SkillRegistry pattern.

For SQLite (dev): JSON fields stored as TEXT.
For Postgres (production): use JSONB columns.
"""

from __future__ import annotations

import json
import logging
import uuid as _uuid

import aiosqlite

from controller.workflows.models import (
    WorkflowTemplate,
    WorkflowTemplateCreate,
    WorkflowTemplateUpdate,
)

logger = logging.getLogger(__name__)


class TemplateCRUD:
    """Manages workflow template CRUD operations against SQLite."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create(self, template: WorkflowTemplateCreate) -> WorkflowTemplate:
        """Insert template + initial version record."""
        template_id = _uuid.uuid4().hex
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO workflow_templates
                    (id, slug, name, description, version, definition,
                     parameter_schema, is_active, created_by)
                VALUES (?, ?, ?, ?, 1, ?, ?, 1, ?)
                """,
                (
                    template_id,
                    template.slug,
                    template.name,
                    template.description,
                    json.dumps(template.definition),
                    json.dumps(template.parameter_schema) if template.parameter_schema else None,
                    template.created_by,
                ),
            )
            # Insert initial version record
            ver_id = _uuid.uuid4().hex
            await db.execute(
                """INSERT INTO workflow_template_versions
                   (id, template_id, version, definition, parameter_schema, created_by)
                   VALUES (?, ?, 1, ?, ?, ?)""",
                (
                    ver_id,
                    template_id,
                    json.dumps(template.definition),
                    json.dumps(template.parameter_schema) if template.parameter_schema else None,
                    template.created_by,
                ),
            )
            await db.commit()

        result = await self.get(template.slug)
        assert result is not None
        return result

    async def get(self, slug: str) -> WorkflowTemplate | None:
        """Get active template by slug."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM workflow_templates WHERE slug = ? AND is_active = 1",
                (slug,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_template(row)

    async def update(self, slug: str, update: WorkflowTemplateUpdate) -> WorkflowTemplate | None:
        """Update template, create new version, increment version counter."""
        sets: list[str] = []
        params: list[object] = []

        if update.description is not None:
            sets.append("description = ?")
            params.append(update.description)
        if update.definition is not None:
            sets.append("definition = ?")
            params.append(json.dumps(update.definition))
        if update.parameter_schema is not None:
            sets.append("parameter_schema = ?")
            params.append(json.dumps(update.parameter_schema))

        if not sets:
            return await self.get(slug)

        sets.append("version = version + 1")
        sets.append("updated_at = datetime('now')")
        params.append(slug)

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                f"UPDATE workflow_templates SET {', '.join(sets)} WHERE slug = ? AND is_active = 1",
                params,
            )
            # Insert version record
            tmpl = await self._get_raw(db, slug)
            if tmpl:
                ver_id = _uuid.uuid4().hex
                await db.execute(
                    """INSERT INTO workflow_template_versions
                       (id, template_id, version, definition, parameter_schema, changelog, created_by)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ver_id,
                        tmpl["id"],
                        tmpl["version"],
                        tmpl["definition"],
                        tmpl["parameter_schema"],
                        update.changelog,
                        update.updated_by,
                    ),
                )
            await db.commit()

        return await self.get(slug)

    async def delete(self, slug: str) -> bool:
        """Soft delete (set is_active=0)."""
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "UPDATE workflow_templates SET is_active = 0, updated_at = datetime('now') WHERE slug = ? AND is_active = 1",
                (slug,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def list_all(self) -> list[WorkflowTemplate]:
        """List all active templates."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM workflow_templates WHERE is_active = 1 ORDER BY name"
            )
            rows = await cursor.fetchall()
        return [self._row_to_template(row) for row in rows]

    async def get_versions(self, slug: str) -> list[dict]:
        """Get version history for a template."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT tv.* FROM workflow_template_versions tv
                   JOIN workflow_templates t ON tv.template_id = t.id
                   WHERE t.slug = ? ORDER BY tv.version DESC""",
                (slug,),
            )
            rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "template_id": row["template_id"],
                "version": row["version"],
                "definition": json.loads(row["definition"]),
                "parameter_schema": json.loads(row["parameter_schema"]) if row["parameter_schema"] else None,
                "changelog": row["changelog"],
                "created_by": row["created_by"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    async def rollback(self, slug: str, target_version: int) -> WorkflowTemplate | None:
        """Restore a previous version as current.

        Finds the target version in history, applies its definition and
        parameter_schema to the template, and creates a new version record
        documenting the rollback.
        """
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row

            # Get template id
            tmpl = await self._get_raw(db, slug)
            if tmpl is None:
                return None

            template_id = tmpl["id"]

            # Find the target version
            cursor = await db.execute(
                """SELECT * FROM workflow_template_versions
                   WHERE template_id = ? AND version = ?""",
                (template_id, target_version),
            )
            target = await cursor.fetchone()
            if target is None:
                return None

            # Update template with target version's data, bump version
            new_version = tmpl["version"] + 1
            await db.execute(
                """UPDATE workflow_templates
                   SET definition = ?, parameter_schema = ?,
                       version = ?, updated_at = datetime('now')
                   WHERE id = ? AND is_active = 1""",
                (
                    target["definition"],
                    target["parameter_schema"],
                    new_version,
                    template_id,
                ),
            )

            # Record the rollback as a new version
            ver_id = _uuid.uuid4().hex
            await db.execute(
                """INSERT INTO workflow_template_versions
                   (id, template_id, version, definition, parameter_schema, changelog, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    ver_id,
                    template_id,
                    new_version,
                    target["definition"],
                    target["parameter_schema"],
                    f"Rollback to version {target_version}",
                    "",
                ),
            )
            await db.commit()

        return await self.get(slug)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _get_raw(db: aiosqlite.Connection, slug: str):
        """Get raw row from an open db connection."""
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM workflow_templates WHERE slug = ? AND is_active = 1",
            (slug,),
        )
        return await cursor.fetchone()

    @staticmethod
    def _row_to_template(row: aiosqlite.Row) -> WorkflowTemplate:
        """Convert DB row to WorkflowTemplate dataclass."""
        return WorkflowTemplate(
            id=row["id"],
            slug=row["slug"],
            name=row["name"],
            description=row["description"] or "",
            version=row["version"],
            definition=json.loads(row["definition"]),
            parameter_schema=json.loads(row["parameter_schema"]) if row["parameter_schema"] else None,
            is_active=bool(row["is_active"]),
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
