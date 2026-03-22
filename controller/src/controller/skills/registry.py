"""Skill Registry with tag-based and embedding-based search.

Phase 1: tag-based matching via search_by_tags.
Phase 2: semantic search via search_by_embedding with cosine similarity.

For SQLite (dev): embeddings stored as JSON text, similarity computed in Python.
For Postgres + pgvector (production): use vector(1024) type and <=> operator.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime

import aiosqlite

from controller.skills.embedding import EmbeddingProvider
from controller.skills.models import (
    Skill,
    SkillCreate,
    SkillFilters,
    SkillUpdate,
    ScoredSkill,
)

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Manages skill CRUD and search operations against SQLite."""

    def __init__(
        self,
        db_path: str,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._db_path = db_path
        self._embedder = embedding_provider

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create(self, skill_create: SkillCreate) -> Skill:
        """Create a new skill and optionally generate its embedding."""
        import uuid as _uuid
        skill_id = _uuid.uuid4().hex
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO skills
                    (id, slug, name, description, content, language, domain,
                     requires, tags, org_id, repo_pattern, is_default, is_active, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    skill_id,
                    skill_create.slug,
                    skill_create.name,
                    skill_create.description,
                    skill_create.content,
                    json.dumps(skill_create.language),
                    json.dumps(skill_create.domain),
                    json.dumps(skill_create.requires),
                    json.dumps(skill_create.tags),
                    skill_create.org_id,
                    skill_create.repo_pattern,
                    int(skill_create.is_default),
                    skill_create.created_by,
                ),
            )
            # Insert initial version
            ver_id = _uuid.uuid4().hex
            await db.execute(
                """INSERT INTO skill_versions
                   (id, skill_id, version, content, description, created_by)
                   VALUES (?, ?, 1, ?, ?, ?)""",
                (ver_id, skill_id, skill_create.content, skill_create.description, skill_create.created_by),
            )
            await db.commit()

        # Auto-embed if provider available
        if self._embedder:
            try:
                text = f"{skill_create.name} {skill_create.description} {skill_create.content}"
                embedding = await self._embedder.embed(text)
                await self.store_embedding(skill_create.slug, embedding)
            except Exception:
                logger.warning("Failed to embed skill %s on create", skill_create.slug)

        skill = await self.get(skill_create.slug)
        assert skill is not None
        return skill

    async def get(self, slug: str) -> Skill | None:
        """Retrieve a skill by slug."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM skills WHERE slug = ? AND is_active = 1",
                (slug,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_skill(row)

    async def update(self, slug: str, update: SkillUpdate) -> Skill | None:
        """Update an existing skill and optionally re-embed."""
        sets: list[str] = []
        params: list[object] = []

        if update.description is not None:
            sets.append("description = ?")
            params.append(update.description)
        if update.content is not None:
            sets.append("content = ?")
            params.append(update.content)
        if update.language is not None:
            sets.append("language = ?")
            params.append(json.dumps(update.language))
        if update.domain is not None:
            sets.append("domain = ?")
            params.append(json.dumps(update.domain))
        if update.tags is not None:
            sets.append("tags = ?")
            params.append(json.dumps(update.tags))
        if hasattr(update, 'requires') and update.requires is not None:
            sets.append("requires = ?")
            params.append(json.dumps(update.requires))

        if not sets:
            return await self.get(slug)

        sets.append("version = version + 1")
        sets.append("updated_at = datetime('now')")
        params.append(slug)

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                f"UPDATE skills SET {', '.join(sets)} WHERE slug = ? AND is_active = 1",
                params,
            )
            # Insert version record
            import uuid as _uuid
            skill = await self._get_raw(db, slug)
            if skill:
                ver_id = _uuid.uuid4().hex
                await db.execute(
                    """INSERT INTO skill_versions
                       (id, skill_id, version, content, description, changelog, created_by)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (ver_id, skill["id"], skill["version"], skill["content"],
                     skill["description"], update.changelog, update.updated_by),
                )
            await db.commit()

        # Re-embed after update if content or description changed
        if self._embedder and (update.content is not None or update.description is not None):
            try:
                skill = await self.get(slug)
                if skill:
                    text = f"{skill.name} {skill.description} {skill.content}"
                    embedding = await self._embedder.embed(text)
                    await self.store_embedding(slug, embedding)
            except Exception:
                logger.warning("Failed to re-embed skill %s on update", slug)

        return await self.get(slug)

    async def delete(self, slug: str) -> bool:
        """Soft-delete a skill."""
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "UPDATE skills SET is_active = 0, updated_at = datetime('now') WHERE slug = ? AND is_active = 1",
                (slug,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def list_all(self, org_id: str | None = None) -> list[Skill]:
        """List all active skills, optionally filtered by org."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            if org_id:
                cursor = await db.execute(
                    "SELECT * FROM skills WHERE is_active = 1 AND (org_id IS NULL OR org_id = ?) ORDER BY name",
                    (org_id,),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM skills WHERE is_active = 1 ORDER BY name"
                )
            rows = await cursor.fetchall()
        return [self._row_to_skill(row) for row in rows]

    async def get_by_slugs(self, slugs: list[str]) -> list[Skill]:
        """Retrieve multiple skills by their slugs, preserving order."""
        if not slugs:
            return []
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            placeholders = ", ".join("?" for _ in slugs)
            cursor = await db.execute(
                f"SELECT * FROM skills WHERE slug IN ({placeholders}) AND is_active = 1",
                slugs,
            )
            rows = await cursor.fetchall()
        # Preserve requested order
        skill_map = {self._row_to_skill(row).slug: self._row_to_skill(row) for row in rows}
        return [skill_map[s] for s in slugs if s in skill_map]

    async def get_defaults(self) -> list[Skill]:
        """Get all default skills."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM skills WHERE is_active = 1 AND is_default = 1 ORDER BY name"
            )
            rows = await cursor.fetchall()
        return [self._row_to_skill(row) for row in rows]

    async def get_versions(self, slug: str) -> list:
        """Get version history for a skill."""
        from controller.skills.models import SkillVersion
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT sv.* FROM skill_versions sv
                   JOIN skills s ON sv.skill_id = s.id
                   WHERE s.slug = ? ORDER BY sv.version DESC""",
                (slug,),
            )
            rows = await cursor.fetchall()
        return [
            SkillVersion(
                id=row["id"],
                skill_id=row["skill_id"],
                version=row["version"],
                content=row["content"],
                description=row["description"],
                changelog=row.get("changelog") if hasattr(row, "get") else row["changelog"],
                created_by=row["created_by"],
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Tag-based search (Phase 1)
    # ------------------------------------------------------------------

    async def search_by_tags(
        self,
        language: list[str] | None = None,
        domain: list[str] | None = None,
        limit: int = 20,
    ) -> list[Skill]:
        """Search skills by language and domain tags."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM skills WHERE is_active = 1"
            )
            rows = await cursor.fetchall()

        results: list[Skill] = []
        for row in rows:
            skill = self._row_to_skill(row)
            if language and not set(language) & set(skill.language):
                continue
            if domain and not set(domain) & set(skill.domain):
                continue
            results.append(skill)
            if len(results) >= limit:
                break

        return results

    # ------------------------------------------------------------------
    # Embedding-based search (Phase 2)
    # ------------------------------------------------------------------

    async def store_embedding(self, slug: str, embedding: list[float]) -> None:
        """Store embedding vector for a skill.

        For SQLite: stored as JSON text array.
        For Postgres+pgvector: would use parameterized vector insert.
        """
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE skills SET embedding = ? WHERE slug = ? AND is_active = 1",
                (json.dumps(embedding), slug),
            )
            await db.commit()

    async def search_by_embedding(
        self,
        task_embedding: list[float],
        filters: SkillFilters | None = None,
        limit: int = 20,
    ) -> list[ScoredSkill]:
        """Search skills by embedding similarity.

        For SQLite: loads all embeddings and computes cosine similarity in Python.
        For Postgres+pgvector: would use SQL ``ORDER BY embedding <=> $1 LIMIT $2``.
        """
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM skills WHERE is_active = 1 AND embedding IS NOT NULL"
            params: list[object] = []

            if filters and filters.org_id:
                query += " AND (org_id IS NULL OR org_id = ?)"
                params.append(filters.org_id)

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

        scored: list[ScoredSkill] = []
        for row in rows:
            skill = self._row_to_skill(row)
            skill_embedding = json.loads(row["embedding"])
            similarity = self._cosine_similarity(task_embedding, skill_embedding)

            # Apply language/domain filter
            if filters:
                if filters.language and not set(filters.language) & set(skill.language):
                    continue
                if filters.domain and not set(filters.domain) & set(skill.domain):
                    continue

            scored.append(ScoredSkill(skill=skill, score=similarity))

        # Sort by similarity descending, take top N
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:limit]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _get_raw(db, slug: str):
        """Get raw row from an open db connection."""
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM skills WHERE slug = ? AND is_active = 1", (slug,)
        )
        return await cursor.fetchone()

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if len(a) != len(b) or len(a) == 0:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)

    @staticmethod
    def _row_to_skill(row: aiosqlite.Row) -> Skill:
        """Convert a database row to a Skill dataclass."""
        row_keys = row.keys()
        return Skill(
            id=row["id"] if "id" in row_keys else row["slug"],
            slug=row["slug"],
            name=row["name"],
            description=row["description"],
            content=row["content"],
            language=json.loads(row["language"]),
            domain=json.loads(row["domain"]),
            requires=json.loads(row["requires"]) if "requires" in row_keys else [],
            tags=json.loads(row["tags"]),
            org_id=row["org_id"],
            version=row["version"] if "version" in row_keys else 1,
            is_default=bool(row["is_default"]),
            is_active=bool(row["is_active"]),
            repo_pattern=row["repo_pattern"] if "repo_pattern" in row_keys else None,
            created_by=row["created_by"],
        )
