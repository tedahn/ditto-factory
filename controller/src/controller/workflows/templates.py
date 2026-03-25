"""Workflow Template CRUD operations.

Manages versioned workflow templates in SQLite with soft-delete,
version history, and rollback support. Follows the SkillRegistry
pattern from controller.skills.registry.
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
    """Manages workflow template CRUD against SQLite."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create(self, payload: WorkflowTemplateCreate) -> WorkflowTemplate:
        """Create a new workflow template with initial version record."""
        template_id = _uuid.uuid4().hex
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT INTO workflow_templates
                   (id, slug, name, description, version, definition,
                    parameter_schema, is_active, created_by)
                   VALUES (?, ?, ?, ?, 1, ?, ?, 1, ?)""",
                (
                    template_id,
                    payload.slug,
                    payload.name,
                    payload.description,
                    json.dumps(payload.definition),
                    json.dumps(payload.parameter_schema) if payload.parameter_schema else None,
                    payload.created_by,
                ),
            )
            # Insert initial version record
            ver_id = _uuid.uuid4().hex
            await db.execute(
                """INSERT INTO workflow_template_versions
                   (id, template_id, version, definition, parameter_schema,
                    changelog, created_by)
                   VALUES (?, ?, 1, ?, ?, 'Initial version', ?)""",
                (
                    ver_id,
                    template_id,
                    json.dumps(payload.definition),
                    json.dumps(payload.parameter_schema) if payload.parameter_schema else None,
                    payload.created_by,
                ),
            )
            await db.commit()

        template = await self.get(payload.slug)
        assert template is not None
        return template

    async def get(self, slug: str) -> WorkflowTemplate | None:
        """Retrieve a workflow template by slug."""
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

    async def update(
        self, slug: str, update: WorkflowTemplateUpdate
    ) -> WorkflowTemplate | None:
        """Update an existing template and create a new version record."""
        sets: list[str] = []
        params: list[object] = []

        if update.definition is not None:
            sets.append("definition = ?")
            params.append(json.dumps(update.definition))
        if update.parameter_schema is not None:
            sets.append("parameter_schema = ?")
            params.append(json.dumps(update.parameter_schema))
        if update.description is not None:
            sets.append("description = ?")
            params.append(update.description)

        if not sets:
            return await self.get(slug)

        sets.append("version = version + 1")
        sets.append("updated_at = datetime('now')")
        params.append(slug)

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                f"UPDATE workflow_templates SET {', '.join(sets)} "
                "WHERE slug = ? AND is_active = 1",
                params,
            )
            # Fetch updated row for version record
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM workflow_templates WHERE slug = ? AND is_active = 1",
                (slug,),
            )
            row = await cursor.fetchone()
            if row is not None:
                ver_id = _uuid.uuid4().hex
                await db.execute(
                    """INSERT INTO workflow_template_versions
                       (id, template_id, version, definition, parameter_schema,
                        changelog, created_by)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ver_id,
                        row["id"],
                        row["version"],
                        row["definition"],
                        row["parameter_schema"],
                        update.changelog,
                        update.updated_by,
                    ),
                )
            await db.commit()

        return await self.get(slug)

    async def delete(self, slug: str) -> bool:
        """Soft-delete a workflow template."""
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "UPDATE workflow_templates SET is_active = 0, updated_at = datetime('now') "
                "WHERE slug = ? AND is_active = 1",
                (slug,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def list_all(self) -> list[WorkflowTemplate]:
        """List all active workflow templates."""
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
                """SELECT v.* FROM workflow_template_versions v
                   JOIN workflow_templates t ON v.template_id = t.id
                   WHERE t.slug = ? ORDER BY v.version DESC""",
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
        """Rollback a template to a previous version.

        Loads the definition from the target version and creates a new
        version record with the rolled-back content.
        """
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            # Get template id
            cursor = await db.execute(
                "SELECT id FROM workflow_templates WHERE slug = ? AND is_active = 1",
                (slug,),
            )
            tmpl_row = await cursor.fetchone()
            if tmpl_row is None:
                return None
            template_id = tmpl_row["id"]

            # Get target version
            cursor = await db.execute(
                """SELECT * FROM workflow_template_versions
                   WHERE template_id = ? AND version = ?""",
                (template_id, target_version),
            )
            ver_row = await cursor.fetchone()
            if ver_row is None:
                raise ValueError(
                    f"Version {target_version} not found for template '{slug}'"
                )

            # Update template with rolled-back definition
            await db.execute(
                """UPDATE workflow_templates
                   SET definition = ?, parameter_schema = ?,
                       version = version + 1, updated_at = datetime('now')
                   WHERE id = ?""",
                (ver_row["definition"], ver_row["parameter_schema"], template_id),
            )

            # Fetch new version number
            cursor = await db.execute(
                "SELECT version FROM workflow_templates WHERE id = ?",
                (template_id,),
            )
            new_row = await cursor.fetchone()
            new_version = new_row["version"]

            # Insert rollback version record
            ver_id = _uuid.uuid4().hex
            await db.execute(
                """INSERT INTO workflow_template_versions
                   (id, template_id, version, definition, parameter_schema,
                    changelog, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, '')""",
                (
                    ver_id,
                    template_id,
                    new_version,
                    ver_row["definition"],
                    ver_row["parameter_schema"],
                    f"Rollback to version {target_version}",
                ),
            )
            await db.commit()

        return await self.get(slug)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_template(row: aiosqlite.Row) -> WorkflowTemplate:
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
