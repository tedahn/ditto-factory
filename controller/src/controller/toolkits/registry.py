"""Toolkit Registry -- CRUD operations for toolkit sources, toolkits, and versions.

Uses aiosqlite for async database access with JSON serialization for
structured fields (config, tags, dependencies, metadata).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone

import aiosqlite

from controller.toolkits.models import (
    DiscoveredItem,
    LoadStrategy,
    RiskLevel,
    Toolkit,
    ToolkitSource,
    ToolkitStatus,
    ToolkitType,
    ToolkitVersion,
)

logger = logging.getLogger(__name__)


class ToolkitRegistry:
    """Manages toolkit source, toolkit, and version CRUD against SQLite."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # Source CRUD
    # ------------------------------------------------------------------

    async def create_source(
        self,
        github_url: str,
        owner: str,
        repo: str,
        branch: str = "main",
        commit_sha: str | None = None,
        metadata: dict | None = None,
    ) -> ToolkitSource:
        source_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO toolkit_sources
                    (id, github_url, github_owner, github_repo, branch,
                     last_commit_sha, last_synced_at, status, metadata,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    source_id,
                    github_url,
                    owner,
                    repo,
                    branch,
                    commit_sha,
                    now if commit_sha else None,
                    json.dumps(metadata or {}),
                    now,
                    now,
                ),
            )
            await db.commit()
        source = await self.get_source(source_id)
        assert source is not None
        return source

    async def get_source(self, source_id: str) -> ToolkitSource | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM toolkit_sources WHERE id = ?",
                (source_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_source(row)

    async def list_sources(self) -> list[ToolkitSource]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM toolkit_sources ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()
        return [self._row_to_source(row) for row in rows]

    async def delete_source(self, source_id: str) -> bool:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "DELETE FROM toolkit_sources WHERE id = ?",
                (source_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def update_source_sync(
        self, source_id: str, commit_sha: str
    ) -> ToolkitSource | None:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                """
                UPDATE toolkit_sources
                SET last_commit_sha = ?, last_synced_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (commit_sha, now, now, source_id),
            )
            await db.commit()
            if cursor.rowcount == 0:
                return None
        return await self.get_source(source_id)

    # ------------------------------------------------------------------
    # Toolkit CRUD
    # ------------------------------------------------------------------

    async def create_toolkit(
        self,
        source_id: str,
        slug: str,
        name: str,
        type: ToolkitType,
        description: str = "",
        path: str = "",
        load_strategy: LoadStrategy = LoadStrategy.MOUNT_FILE,
        pinned_sha: str | None = None,
        content: str = "",
        config: dict | None = None,
        tags: list[str] | None = None,
        dependencies: list[str] | None = None,
        risk_level: RiskLevel = RiskLevel.SAFE,
    ) -> Toolkit:
        toolkit_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO toolkits
                    (id, source_id, slug, name, type, description, path,
                     load_strategy, version, pinned_sha, content, config,
                     tags, dependencies, risk_level, status, usage_count,
                     is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, 'available', 0, 1, ?, ?)
                """,
                (
                    toolkit_id,
                    source_id,
                    slug,
                    name,
                    type.value if isinstance(type, ToolkitType) else type,
                    description,
                    path,
                    load_strategy.value
                    if isinstance(load_strategy, LoadStrategy)
                    else load_strategy,
                    pinned_sha,
                    content,
                    json.dumps(config or {}),
                    json.dumps(tags or []),
                    json.dumps(dependencies or []),
                    risk_level.value
                    if isinstance(risk_level, RiskLevel)
                    else risk_level,
                    now,
                    now,
                ),
            )
            await db.commit()
        toolkit = await self.get_toolkit(slug)
        assert toolkit is not None
        return toolkit

    async def get_toolkit(self, slug: str) -> Toolkit | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM toolkits WHERE slug = ? AND is_active = 1",
                (slug,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_toolkit(row)

    async def list_toolkits(
        self,
        type_filter: ToolkitType | None = None,
        status_filter: ToolkitStatus | None = None,
        source_id: str | None = None,
    ) -> list[Toolkit]:
        clauses = ["is_active = 1"]
        params: list[object] = []
        if type_filter is not None:
            clauses.append("type = ?")
            params.append(
                type_filter.value
                if isinstance(type_filter, ToolkitType)
                else type_filter
            )
        if status_filter is not None:
            clauses.append("status = ?")
            params.append(
                status_filter.value
                if isinstance(status_filter, ToolkitStatus)
                else status_filter
            )
        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(source_id)

        where = " AND ".join(clauses)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"SELECT * FROM toolkits WHERE {where} ORDER BY name",
                params,
            )
            rows = await cursor.fetchall()
        return [self._row_to_toolkit(row) for row in rows]

    async def update_toolkit(self, slug: str, **kwargs: object) -> Toolkit | None:
        json_fields = {"config", "tags", "dependencies", "metadata"}
        enum_fields = {
            "type": ToolkitType,
            "load_strategy": LoadStrategy,
            "risk_level": RiskLevel,
            "status": ToolkitStatus,
        }
        sets: list[str] = []
        params: list[object] = []
        for key, value in kwargs.items():
            if key in json_fields:
                sets.append(f"{key} = ?")
                params.append(json.dumps(value))
            elif key in enum_fields and hasattr(value, "value"):
                sets.append(f"{key} = ?")
                params.append(value.value)
            else:
                sets.append(f"{key} = ?")
                params.append(value)

        if not sets:
            return await self.get_toolkit(slug)

        sets.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())
        params.append(slug)

        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                f"UPDATE toolkits SET {', '.join(sets)} WHERE slug = ? AND is_active = 1",
                params,
            )
            await db.commit()
            if cursor.rowcount == 0:
                return None
        return await self.get_toolkit(slug)

    async def delete_toolkit(self, slug: str) -> bool:
        """Soft-delete a toolkit by setting is_active = 0."""
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "UPDATE toolkits SET is_active = 0, updated_at = ? WHERE slug = ? AND is_active = 1",
                (datetime.now(timezone.utc).isoformat(), slug),
            )
            await db.commit()
            return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Import from manifest
    # ------------------------------------------------------------------

    async def import_from_manifest(
        self,
        source_id: str,
        items: list[DiscoveredItem],
        pinned_sha: str,
    ) -> list[Toolkit]:
        results: list[Toolkit] = []
        for item in items:
            slug = self._make_slug(item.name)
            existing = await self.get_toolkit(slug)
            if existing is not None:
                results.append(existing)
                continue

            toolkit = await self.create_toolkit(
                source_id=source_id,
                slug=slug,
                name=item.name,
                type=item.type,
                description=item.description,
                path=item.path,
                load_strategy=item.load_strategy,
                pinned_sha=pinned_sha,
                content=item.content,
                config=item.config,
                tags=item.tags,
                dependencies=item.dependencies,
                risk_level=item.risk_level,
            )

            # Create initial version
            await self.create_version(
                toolkit_id=toolkit.id,
                version=1,
                pinned_sha=pinned_sha,
                content=item.content,
                config=item.config,
                changelog="Initial import",
            )
            results.append(toolkit)

        return results

    # ------------------------------------------------------------------
    # Version operations
    # ------------------------------------------------------------------

    async def create_version(
        self,
        toolkit_id: str,
        version: int,
        pinned_sha: str,
        content: str = "",
        config: dict | None = None,
        changelog: str | None = None,
    ) -> ToolkitVersion:
        ver_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO toolkit_versions
                    (id, toolkit_id, version, pinned_sha, content, config,
                     changelog, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ver_id,
                    toolkit_id,
                    version,
                    pinned_sha,
                    content,
                    json.dumps(config or {}),
                    changelog,
                    now,
                ),
            )
            await db.commit()
        return ToolkitVersion(
            id=ver_id,
            toolkit_id=toolkit_id,
            version=version,
            pinned_sha=pinned_sha,
            content=content,
            config=config or {},
            changelog=changelog,
            created_at=datetime.fromisoformat(now),
        )

    async def get_versions(self, slug: str) -> list[ToolkitVersion]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT tv.* FROM toolkit_versions tv
                JOIN toolkits t ON tv.toolkit_id = t.id
                WHERE t.slug = ?
                ORDER BY tv.version DESC
                """,
                (slug,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_version(row) for row in rows]

    async def rollback(self, slug: str, target_version: int) -> Toolkit | None:
        versions = await self.get_versions(slug)
        target = None
        for v in versions:
            if v.version == target_version:
                target = v
                break
        if target is None:
            return None

        toolkit = await self.get_toolkit(slug)
        if toolkit is None:
            return None

        new_version = toolkit.version + 1

        # Create a new version record for the rollback
        await self.create_version(
            toolkit_id=toolkit.id,
            version=new_version,
            pinned_sha=target.pinned_sha,
            content=target.content,
            config=target.config,
            changelog=f"Rollback to version {target_version}",
        )

        # Update toolkit with restored content
        return await self.update_toolkit(
            slug,
            content=target.content,
            config=target.config,
            pinned_sha=target.pinned_sha,
            version=new_version,
        )

    # ------------------------------------------------------------------
    # Update detection
    # ------------------------------------------------------------------

    async def mark_update_available(self, slug: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE toolkits SET status = ?, updated_at = ? WHERE slug = ? AND is_active = 1",
                (
                    ToolkitStatus.UPDATE_AVAILABLE.value,
                    datetime.now(timezone.utc).isoformat(),
                    slug,
                ),
            )
            await db.commit()

    async def apply_update(
        self,
        slug: str,
        new_content: str,
        new_sha: str,
        changelog: str,
    ) -> Toolkit | None:
        toolkit = await self.get_toolkit(slug)
        if toolkit is None:
            return None

        new_version = toolkit.version + 1

        # Create version record
        await self.create_version(
            toolkit_id=toolkit.id,
            version=new_version,
            pinned_sha=new_sha,
            content=new_content,
            config=toolkit.config,
            changelog=changelog,
        )

        # Update toolkit
        return await self.update_toolkit(
            slug,
            content=new_content,
            pinned_sha=new_sha,
            version=new_version,
            status=ToolkitStatus.AVAILABLE,
        )

    # ------------------------------------------------------------------
    # Row conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_source(row: aiosqlite.Row) -> ToolkitSource:
        row_keys = row.keys()
        return ToolkitSource(
            id=row["id"],
            github_url=row["github_url"],
            github_owner=row["github_owner"],
            github_repo=row["github_repo"],
            branch=row["branch"],
            last_commit_sha=row["last_commit_sha"],
            last_synced_at=_parse_dt(row["last_synced_at"]),
            status=row["status"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    @staticmethod
    def _row_to_toolkit(row: aiosqlite.Row) -> Toolkit:
        return Toolkit(
            id=row["id"],
            source_id=row["source_id"],
            slug=row["slug"],
            name=row["name"],
            type=ToolkitType(row["type"]),
            description=row["description"] or "",
            path=row["path"] or "",
            load_strategy=LoadStrategy(row["load_strategy"]),
            version=row["version"],
            pinned_sha=row["pinned_sha"],
            content=row["content"] or "",
            config=json.loads(row["config"]) if row["config"] else {},
            tags=json.loads(row["tags"]) if row["tags"] else [],
            dependencies=json.loads(row["dependencies"]) if row["dependencies"] else [],
            risk_level=RiskLevel(row["risk_level"]),
            status=ToolkitStatus(row["status"]),
            usage_count=row["usage_count"],
            is_active=bool(row["is_active"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    @staticmethod
    def _row_to_version(row: aiosqlite.Row) -> ToolkitVersion:
        return ToolkitVersion(
            id=row["id"],
            toolkit_id=row["toolkit_id"],
            version=row["version"],
            pinned_sha=row["pinned_sha"],
            content=row["content"] or "",
            config=json.loads(row["config"]) if row["config"] else {},
            changelog=row["changelog"],
            created_at=_parse_dt(row["created_at"]),
        )

    @staticmethod
    def _make_slug(name: str) -> str:
        """Convert a name to a URL-safe slug."""
        slug = name.lower().strip()
        slug = re.sub(r"[^a-z0-9]+", "-", slug)
        slug = slug.strip("-")
        return slug or uuid.uuid4().hex[:8]


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO-format datetime string, returning None for missing values."""
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
