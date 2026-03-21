"""Skill CRUD and tag-based search using aiosqlite."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

import aiosqlite

from controller.skills.models import Skill, SkillCreate, SkillFilters, SkillUpdate, SkillVersion

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Manages skill persistence with CRUD operations and tag-based search."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _now_str() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _parse_dt(val: str | None) -> datetime | None:
        if val is None:
            return None
        return datetime.fromisoformat(val)

    def _row_to_skill(self, row: aiosqlite.Row) -> Skill:
        return Skill(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            description=row["description"],
            content=row["content"],
            language=json.loads(row["language"] or "[]"),
            domain=json.loads(row["domain"] or "[]"),
            requires=json.loads(row["requires"] or "[]"),
            tags=json.loads(row["tags"] or "[]"),
            org_id=row["org_id"],
            repo_pattern=row["repo_pattern"],
            version=row["version"],
            created_by=row["created_by"],
            is_active=bool(row["is_active"]),
            is_default=bool(row["is_default"]),
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _row_to_version(self, row: aiosqlite.Row) -> SkillVersion:
        return SkillVersion(
            id=row["id"],
            skill_id=row["skill_id"],
            version=row["version"],
            content=row["content"],
            description=row["description"],
            changelog=row["changelog"],
            created_by=row["created_by"],
            created_at=self._parse_dt(row["created_at"]),
        )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create(self, skill_create: SkillCreate) -> Skill:
        skill_id = uuid.uuid4().hex
        version_id = uuid.uuid4().hex
        now = self._now_str()

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT INTO skills
                   (id, name, slug, description, content,
                    language, domain, requires, tags,
                    org_id, repo_pattern, version, created_by,
                    is_active, is_default, created_at, updated_at)
                   VALUES (?,?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?,?,?)""",
                (
                    skill_id,
                    skill_create.name,
                    skill_create.slug,
                    skill_create.description,
                    skill_create.content,
                    json.dumps(skill_create.language),
                    json.dumps(skill_create.domain),
                    json.dumps(skill_create.requires),
                    json.dumps(skill_create.tags),
                    skill_create.org_id,
                    skill_create.repo_pattern,
                    1,
                    skill_create.created_by,
                    1,
                    1 if skill_create.is_default else 0,
                    now,
                    now,
                ),
            )
            await db.execute(
                """INSERT INTO skill_versions
                   (id, skill_id, version, content, description, changelog, created_by, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    version_id,
                    skill_id,
                    1,
                    skill_create.content,
                    skill_create.description,
                    "Initial version",
                    skill_create.created_by,
                    now,
                ),
            )
            await db.commit()

        return Skill(
            id=skill_id,
            name=skill_create.name,
            slug=skill_create.slug,
            description=skill_create.description,
            content=skill_create.content,
            language=list(skill_create.language),
            domain=list(skill_create.domain),
            requires=list(skill_create.requires),
            tags=list(skill_create.tags),
            org_id=skill_create.org_id,
            repo_pattern=skill_create.repo_pattern,
            version=1,
            created_by=skill_create.created_by,
            is_active=True,
            is_default=skill_create.is_default,
            created_at=self._parse_dt(now),
            updated_at=self._parse_dt(now),
        )

    async def get(self, slug: str) -> Skill | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM skills WHERE slug = ? AND is_active = 1",
                (slug,),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return None
            return self._row_to_skill(row)

    async def list(
        self,
        filters: SkillFilters | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[Skill], int]:
        conditions: list[str] = []
        params: list[object] = []

        is_active = True
        if filters is not None:
            is_active = filters.is_active
        conditions.append("is_active = ?")
        params.append(1 if is_active else 0)

        if filters is not None:
            if filters.language:
                placeholders = ",".join("?" for _ in filters.language)
                conditions.append(
                    f"EXISTS (SELECT 1 FROM json_each(skills.language) WHERE json_each.value IN ({placeholders}))"
                )
                params.extend(filters.language)

            if filters.domain:
                placeholders = ",".join("?" for _ in filters.domain)
                conditions.append(
                    f"EXISTS (SELECT 1 FROM json_each(skills.domain) WHERE json_each.value IN ({placeholders}))"
                )
                params.extend(filters.domain)

            if filters.org_id is not None:
                conditions.append("org_id = ?")
                params.append(filters.org_id)

        where = " AND ".join(conditions) if conditions else "1=1"
        count_sql = f"SELECT COUNT(*) FROM skills WHERE {where}"
        select_sql = (
            f"SELECT * FROM skills WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )

        offset = (page - 1) * per_page

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute(count_sql, params) as cur:
                total = (await cur.fetchone())[0]

            async with db.execute(
                select_sql, [*params, per_page, offset]
            ) as cur:
                rows = await cur.fetchall()

        return [self._row_to_skill(r) for r in rows], total

    async def update(self, slug: str, update: SkillUpdate) -> Skill:
        now = self._now_str()
        version_id = uuid.uuid4().hex

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row

            # Fetch current skill
            async with db.execute(
                "SELECT * FROM skills WHERE slug = ? AND is_active = 1", (slug,)
            ) as cur:
                row = await cur.fetchone()
            if not row:
                raise ValueError(f"Skill '{slug}' not found")

            current = self._row_to_skill(row)
            new_version = current.version + 1

            # Merge fields
            new_content = update.content if update.content is not None else current.content
            new_description = (
                update.description if update.description is not None else current.description
            )
            new_language = update.language if update.language is not None else current.language
            new_domain = update.domain if update.domain is not None else current.domain
            new_requires = update.requires if update.requires is not None else current.requires
            new_tags = update.tags if update.tags is not None else current.tags

            await db.execute(
                """UPDATE skills SET
                   content=?, description=?, language=?, domain=?, requires=?, tags=?,
                   version=?, updated_at=?
                   WHERE slug=? AND is_active=1""",
                (
                    new_content,
                    new_description,
                    json.dumps(new_language),
                    json.dumps(new_domain),
                    json.dumps(new_requires),
                    json.dumps(new_tags),
                    new_version,
                    now,
                    slug,
                ),
            )

            await db.execute(
                """INSERT INTO skill_versions
                   (id, skill_id, version, content, description, changelog, created_by, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    version_id,
                    current.id,
                    new_version,
                    new_content,
                    new_description,
                    update.changelog,
                    update.updated_by,
                    now,
                ),
            )
            await db.commit()

        # Return updated skill
        updated = await self.get(slug)
        assert updated is not None
        return updated

    async def delete(self, slug: str) -> None:
        now = self._now_str()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE skills SET is_active = 0, updated_at = ? WHERE slug = ?",
                (now, slug),
            )
            await db.commit()

    async def rollback(self, slug: str, target_version: int) -> Skill:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row

            # Get skill id
            async with db.execute(
                "SELECT id, version FROM skills WHERE slug = ? AND is_active = 1", (slug,)
            ) as cur:
                row = await cur.fetchone()
            if not row:
                raise ValueError(f"Skill '{slug}' not found")

            skill_id = row["id"]
            current_version = row["version"]

            # Fetch target version content
            async with db.execute(
                "SELECT content, description FROM skill_versions WHERE skill_id = ? AND version = ?",
                (skill_id, target_version),
            ) as cur:
                ver_row = await cur.fetchone()
            if not ver_row:
                raise ValueError(
                    f"Version {target_version} not found for skill '{slug}'"
                )

        # Create a new version with the restored content via update
        return await self.update(
            slug,
            SkillUpdate(
                content=ver_row["content"],
                description=ver_row["description"],
                changelog=f"Rolled back to version {target_version}",
            ),
        )

    async def get_versions(self, slug: str) -> list[SkillVersion]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute(
                """SELECT sv.* FROM skill_versions sv
                   JOIN skills s ON sv.skill_id = s.id
                   WHERE s.slug = ?
                   ORDER BY sv.version ASC""",
                (slug,),
            ) as cur:
                rows = await cur.fetchall()

        return [self._row_to_version(r) for r in rows]

    async def search_by_tags(
        self,
        language: list[str] | None = None,
        domain: list[str] | None = None,
        limit: int = 10,
    ) -> list[Skill]:
        conditions: list[str] = ["is_active = 1"]
        params: list[object] = []

        if language:
            placeholders = ",".join("?" for _ in language)
            conditions.append(
                f"EXISTS (SELECT 1 FROM json_each(skills.language) WHERE json_each.value IN ({placeholders}))"
            )
            params.extend(language)

        if domain:
            placeholders = ",".join("?" for _ in domain)
            conditions.append(
                f"EXISTS (SELECT 1 FROM json_each(skills.domain) WHERE json_each.value IN ({placeholders}))"
            )
            params.extend(domain)

        where = " AND ".join(conditions)
        sql = f"SELECT * FROM skills WHERE {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cur:
                rows = await cur.fetchall()

        return [self._row_to_skill(r) for r in rows]

    async def get_defaults(self) -> list[Skill]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM skills WHERE is_default = 1 AND is_active = 1"
            ) as cur:
                rows = await cur.fetchall()

        return [self._row_to_skill(r) for r in rows]
