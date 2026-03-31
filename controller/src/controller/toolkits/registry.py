"""Toolkit Registry -- CRUD operations for the hierarchical toolkit model.

Hierarchy:
  ToolkitSource (GitHub repo connection)
    └── Toolkit (one per repo import)
          └── ToolkitComponent (skill, plugin, profile, etc.)
                └── ComponentFile (individual files)

Uses aiosqlite for async database access with JSON serialization for
structured fields (tags, metadata).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone

import aiosqlite

from controller.toolkits.models import (
    ComponentFile,
    ComponentType,
    DiscoveredComponent,
    DiscoveryManifest,
    LoadStrategy,
    RiskLevel,
    Toolkit,
    ToolkitCategory,
    ToolkitComponent,
    ToolkitSource,
    ToolkitStatus,
    ToolkitVersion,
)

logger = logging.getLogger(__name__)


class ToolkitRegistry:
    """Manages toolkit source, toolkit, component, and file CRUD against SQLite."""

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
    # Toolkit CRUD (repo-level)
    # ------------------------------------------------------------------

    async def create_toolkit(
        self,
        source_id: str,
        slug: str,
        name: str,
        category: ToolkitCategory,
        description: str = "",
        pinned_sha: str | None = None,
        source_version: str | None = None,
        tags: list[str] | None = None,
    ) -> Toolkit:
        toolkit_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO toolkits
                    (id, source_id, slug, name, category, description,
                     version, pinned_sha, source_version, status, tags,
                     component_count, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, 'available', ?, 0, 1, ?, ?)
                """,
                (
                    toolkit_id,
                    source_id,
                    slug,
                    name,
                    category.value if isinstance(category, ToolkitCategory) else category,
                    description,
                    pinned_sha,
                    source_version,
                    json.dumps(tags or []),
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
        category_filter: ToolkitCategory | None = None,
        status_filter: ToolkitStatus | None = None,
        source_id: str | None = None,
    ) -> list[Toolkit]:
        clauses = ["is_active = 1"]
        params: list[object] = []
        if category_filter is not None:
            clauses.append("category = ?")
            params.append(
                category_filter.value
                if isinstance(category_filter, ToolkitCategory)
                else category_filter
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
        json_fields = {"tags", "metadata"}
        enum_fields = {
            "category": ToolkitCategory,
            "status": ToolkitStatus,
        }
        # Map model field names to DB column names
        field_to_column: dict[str, str] = {}  # no remapping needed — DB columns match model fields

        sets: list[str] = []
        params: list[object] = []
        for key, value in kwargs.items():
            col = field_to_column.get(key, key)
            if key in json_fields:
                sets.append(f"{col} = ?")
                params.append(json.dumps(value))
            elif key in enum_fields and hasattr(value, "value"):
                sets.append(f"{col} = ?")
                params.append(value.value)
            else:
                sets.append(f"{col} = ?")
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
        """Hard-delete a toolkit, its components, component files, and versions."""
        async with aiosqlite.connect(self._db_path) as db:
            # Get toolkit ID first
            cursor = await db.execute("SELECT id FROM toolkits WHERE slug = ?", (slug,))
            row = await cursor.fetchone()
            if not row:
                return False
            toolkit_id = row[0]

            # Delete component files
            await db.execute(
                """DELETE FROM toolkit_component_files WHERE component_id IN (
                    SELECT id FROM toolkit_components WHERE toolkit_id = ?
                )""",
                (toolkit_id,),
            )
            # Delete components
            await db.execute("DELETE FROM toolkit_components WHERE toolkit_id = ?", (toolkit_id,))
            # Delete versions
            await db.execute("DELETE FROM toolkit_versions WHERE toolkit_id = ?", (toolkit_id,))
            # Delete toolkit
            await db.execute("DELETE FROM toolkits WHERE id = ?", (toolkit_id,))
            await db.commit()
            return True

    # ------------------------------------------------------------------
    # Component CRUD
    # ------------------------------------------------------------------

    async def create_component(
        self,
        toolkit_id: str,
        slug: str,
        name: str,
        type: ComponentType,
        description: str = "",
        directory: str = "",
        primary_file: str = "",
        load_strategy: LoadStrategy = LoadStrategy.MOUNT_FILE,
        content: str = "",
        tags: list[str] | None = None,
        risk_level: RiskLevel = RiskLevel.SAFE,
    ) -> ToolkitComponent:
        comp_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO toolkit_components
                    (id, toolkit_id, slug, name, type, description,
                     directory, primary_file, load_strategy, content,
                     tags, risk_level, is_active, file_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?)
                """,
                (
                    comp_id,
                    toolkit_id,
                    slug,
                    name,
                    type.value if isinstance(type, ComponentType) else type,
                    description,
                    directory,
                    primary_file,
                    load_strategy.value if isinstance(load_strategy, LoadStrategy) else load_strategy,
                    content,
                    json.dumps(tags or []),
                    risk_level.value if isinstance(risk_level, RiskLevel) else risk_level,
                    now,
                ),
            )
            await db.commit()

        return ToolkitComponent(
            id=comp_id,
            toolkit_id=toolkit_id,
            slug=slug,
            name=name,
            type=type if isinstance(type, ComponentType) else ComponentType(type),
            description=description,
            directory=directory,
            primary_file=primary_file,
            load_strategy=load_strategy if isinstance(load_strategy, LoadStrategy) else LoadStrategy(load_strategy),
            content=content,
            tags=tags or [],
            risk_level=risk_level if isinstance(risk_level, RiskLevel) else RiskLevel(risk_level),
            is_active=True,
            file_count=0,
            created_at=datetime.fromisoformat(now),
        )

    async def list_components(self, toolkit_id: str) -> list[ToolkitComponent]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM toolkit_components WHERE toolkit_id = ? AND is_active = 1 ORDER BY name",
                (toolkit_id,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_component(row) for row in rows]

    async def get_component(
        self, toolkit_id: str, component_slug: str
    ) -> ToolkitComponent | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM toolkit_components WHERE toolkit_id = ? AND slug = ? AND is_active = 1",
                (toolkit_id, component_slug),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_component(row)

    async def get_component_by_slug(
        self, toolkit_slug: str, component_slug: str
    ) -> ToolkitComponent | None:
        """Look up a component by toolkit slug + component slug."""
        toolkit = await self.get_toolkit(toolkit_slug)
        if toolkit is None:
            return None
        return await self.get_component(toolkit.id, component_slug)

    # ------------------------------------------------------------------
    # Component File CRUD
    # ------------------------------------------------------------------

    async def create_component_file(
        self,
        component_id: str,
        path: str,
        filename: str,
        content: str = "",
        is_primary: bool = False,
    ) -> ComponentFile:
        file_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO toolkit_component_files
                    (id, component_id, path, filename, content, is_primary, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_id,
                    component_id,
                    path,
                    filename,
                    content,
                    1 if is_primary else 0,
                    now,
                ),
            )
            await db.commit()

        return ComponentFile(
            id=file_id,
            component_id=component_id,
            path=path,
            filename=filename,
            content=content,
            is_primary=is_primary,
            created_at=datetime.fromisoformat(now),
        )

    async def list_component_files(self, component_id: str) -> list[ComponentFile]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM toolkit_component_files WHERE component_id = ? ORDER BY is_primary DESC, path",
                (component_id,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_file(row) for row in rows]

    # ------------------------------------------------------------------
    # Import from manifest
    # ------------------------------------------------------------------

    async def import_from_manifest(
        self,
        source_id: str,
        manifest: DiscoveryManifest,
        selected_components: list[str] | None = None,
    ) -> Toolkit:
        """Import a discovered manifest as a single toolkit with components and files.

        Uses a SINGLE database connection so the entire import is atomic.
        If any insert fails, the connection closes without commit and
        nothing is persisted -- preventing partial/broken toolkit rows.

        Creates:
        1. One Toolkit row (repo-level)
        2. One ToolkitComponent per discovered component
        3. One ComponentFile per file within each component
        4. One ToolkitVersion (initial v1)

        Returns the created Toolkit with component_count set.
        """
        slug = self._make_slug(manifest.repo)

        # Check if toolkit already exists (separate read-only connection)
        existing = await self.get_toolkit(slug)
        if existing is not None:
            return existing

        toolkit_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        category_val = manifest.category.value if isinstance(manifest.category, ToolkitCategory) else manifest.category

        async with aiosqlite.connect(self._db_path) as db:
            # Double-check inside the transaction to avoid races
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM toolkits WHERE slug = ? AND is_active = 1",
                (slug,),
            )
            if await cursor.fetchone() is not None:
                # Another caller inserted between our check and here
                pass  # fall through -- will be fetched after the block
            else:
                # 1. Insert toolkit
                await db.execute(
                    """
                    INSERT INTO toolkits
                        (id, source_id, slug, name, category, description,
                         version, pinned_sha, source_version, status, tags,
                         component_count, is_active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, 'available', ?, 0, 1, ?, ?)
                    """,
                    (
                        toolkit_id,
                        source_id,
                        slug,
                        manifest.repo.replace("-", " ").title(),
                        category_val,
                        manifest.repo_description,
                        manifest.commit_sha,
                        manifest.source_version,
                        json.dumps([]),
                        now,
                        now,
                    ),
                )

                # 2. Insert components and files
                component_count = 0
                components_to_import = manifest.discovered
                if selected_components is not None:
                    components_to_import = [
                        c for c in components_to_import
                        if c.name in selected_components
                    ]

                seen_slugs: set[str] = set()
                for disc_comp in components_to_import:
                    comp_id = uuid.uuid4().hex
                    comp_slug = self._make_slug(disc_comp.name)
                    # Deduplicate slugs — append directory hash if collision
                    if comp_slug in seen_slugs:
                        dir_suffix = self._make_slug(disc_comp.directory.split("/")[-1] if disc_comp.directory else comp_id[:6])
                        comp_slug = f"{comp_slug}-{dir_suffix}" if dir_suffix != comp_slug else f"{comp_slug}-{comp_id[:6]}"
                    seen_slugs.add(comp_slug)

                    # Find primary file content
                    primary_content = ""
                    for f in disc_comp.files:
                        if f.is_primary:
                            primary_content = f.content
                            break

                    comp_type = disc_comp.type.value if isinstance(disc_comp.type, ComponentType) else disc_comp.type
                    load_strat = disc_comp.load_strategy.value if isinstance(disc_comp.load_strategy, LoadStrategy) else disc_comp.load_strategy
                    risk_val = disc_comp.risk_level.value if isinstance(disc_comp.risk_level, RiskLevel) else disc_comp.risk_level

                    await db.execute(
                        """
                        INSERT INTO toolkit_components
                            (id, toolkit_id, slug, name, type, description,
                             directory, primary_file, load_strategy, content,
                             tags, risk_level, is_active, file_count, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?)
                        """,
                        (
                            comp_id,
                            toolkit_id,
                            comp_slug,
                            disc_comp.name,
                            comp_type,
                            disc_comp.description,
                            disc_comp.directory,
                            disc_comp.primary_file,
                            load_strat,
                            primary_content,
                            json.dumps(disc_comp.tags or []),
                            risk_val,
                            now,
                        ),
                    )

                    # Insert component files
                    file_count = 0
                    for disc_file in disc_comp.files:
                        file_id = uuid.uuid4().hex
                        await db.execute(
                            """
                            INSERT INTO toolkit_component_files
                                (id, component_id, path, filename, content, is_primary, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                file_id,
                                comp_id,
                                disc_file.path,
                                disc_file.filename,
                                disc_file.content,
                                1 if disc_file.is_primary else 0,
                                now,
                            ),
                        )
                        file_count += 1

                    # Update component file_count
                    await db.execute(
                        "UPDATE toolkit_components SET file_count = ? WHERE id = ?",
                        (file_count, comp_id),
                    )

                    component_count += 1

                # 3. Update toolkit component_count
                await db.execute(
                    "UPDATE toolkits SET component_count = ? WHERE id = ?",
                    (component_count, toolkit_id),
                )

                # 4. Create initial version
                ver_id = uuid.uuid4().hex
                await db.execute(
                    """
                    INSERT INTO toolkit_versions
                        (id, toolkit_id, version, pinned_sha, changelog, created_at)
                    VALUES (?, ?, 1, ?, ?, ?)
                    """,
                    (ver_id, toolkit_id, manifest.commit_sha, "Initial import", now),
                )

                # ONLY commit if everything succeeded
                await db.commit()

        # Auto-activate skills from imported toolkit components
        try:
            activated = await self.activate_toolkit(slug)
            logger.info("Auto-activated %d skills from toolkit %s", activated, slug)
        except Exception:
            logger.warning("Failed to auto-activate toolkit %s", slug, exc_info=True)

        # Return the fully persisted toolkit
        result = await self.get_toolkit(slug)
        assert result is not None
        return result

    # ------------------------------------------------------------------
    # Version operations
    # ------------------------------------------------------------------

    async def create_version(
        self,
        toolkit_id: str,
        version: int,
        pinned_sha: str,
        changelog: str | None = None,
    ) -> ToolkitVersion:
        ver_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO toolkit_versions
                    (id, toolkit_id, version, pinned_sha, changelog, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ver_id, toolkit_id, version, pinned_sha, changelog, now),
            )
            await db.commit()
        return ToolkitVersion(
            id=ver_id,
            toolkit_id=toolkit_id,
            version=version,
            pinned_sha=pinned_sha,
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
            changelog=f"Rollback to version {target_version}",
        )

        # Update toolkit with restored SHA
        return await self.update_toolkit(
            slug,
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
        new_sha: str,
        changelog: str,
        updated_components: list[DiscoveredComponent],
        source_version: str | None = None,
    ) -> Toolkit | None:
        """Apply an update: create new version, update component content."""
        toolkit = await self.get_toolkit(slug)
        if toolkit is None:
            return None

        new_version = toolkit.version + 1

        # Create version record
        await self.create_version(
            toolkit_id=toolkit.id,
            version=new_version,
            pinned_sha=new_sha,
            changelog=changelog,
        )

        # Update existing components or create new ones
        for disc_comp in updated_components:
            comp_slug = self._make_slug(disc_comp.name)
            existing_comp = await self.get_component(toolkit.id, comp_slug)

            primary_content = ""
            for f in disc_comp.files:
                if f.is_primary:
                    primary_content = f.content
                    break

            if existing_comp is not None:
                # Update existing component content
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute(
                        """
                        UPDATE toolkit_components
                        SET content = ?, description = ?, primary_file = ?,
                            tags = ?, risk_level = ?
                        WHERE id = ?
                        """,
                        (
                            primary_content,
                            disc_comp.description,
                            disc_comp.primary_file,
                            json.dumps(disc_comp.tags),
                            disc_comp.risk_level.value
                            if isinstance(disc_comp.risk_level, RiskLevel)
                            else disc_comp.risk_level,
                            existing_comp.id,
                        ),
                    )
                    # Replace files: delete old, insert new
                    await db.execute(
                        "DELETE FROM toolkit_component_files WHERE component_id = ?",
                        (existing_comp.id,),
                    )
                    for disc_file in disc_comp.files:
                        await db.execute(
                            """
                            INSERT INTO toolkit_component_files
                                (id, component_id, path, filename, content, is_primary, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                uuid.uuid4().hex,
                                existing_comp.id,
                                disc_file.path,
                                disc_file.filename,
                                disc_file.content,
                                1 if disc_file.is_primary else 0,
                                datetime.now(timezone.utc).isoformat(),
                            ),
                        )
                    await db.commit()
            else:
                # New component
                component = await self.create_component(
                    toolkit_id=toolkit.id,
                    slug=comp_slug,
                    name=disc_comp.name,
                    type=disc_comp.type,
                    description=disc_comp.description,
                    directory=disc_comp.directory,
                    primary_file=disc_comp.primary_file,
                    load_strategy=disc_comp.load_strategy,
                    content=primary_content,
                    tags=disc_comp.tags,
                    risk_level=disc_comp.risk_level,
                )
                for disc_file in disc_comp.files:
                    await self.create_component_file(
                        component_id=component.id,
                        path=disc_file.path,
                        filename=disc_file.filename,
                        content=disc_file.content,
                        is_primary=disc_file.is_primary,
                    )

        # Recount components
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM toolkit_components WHERE toolkit_id = ? AND is_active = 1",
                (toolkit.id,),
            )
            row = await cursor.fetchone()
            comp_count = row["cnt"] if row else 0

        # Update toolkit
        update_kwargs: dict[str, object] = dict(
            pinned_sha=new_sha,
            version=new_version,
            status=ToolkitStatus.AVAILABLE,
            component_count=comp_count,
        )
        if source_version is not None:
            update_kwargs["source_version"] = source_version
        return await self.update_toolkit(slug, **update_kwargs)

    # ------------------------------------------------------------------
    # Activation bridge (toolkit -> skill system)
    # ------------------------------------------------------------------

    async def activate_toolkit(self, toolkit_slug: str, skill_db_path: str | None = None) -> int:
        """Create Skill records from toolkit SKILL/AGENT/COMMAND components.

        For each qualifying component, inserts a row into the skills table
        (namespaced slug: ``{toolkit_slug}--{component_slug}``) so the
        existing classifier/injector can discover and use them.

        Returns the number of newly activated skills.
        """
        db_path = skill_db_path or self._db_path
        toolkit = await self.get_toolkit(toolkit_slug)
        if not toolkit:
            return 0

        components = await self.list_components(toolkit.id)
        activated = 0

        async with aiosqlite.connect(db_path) as db:
            for comp in components:
                if comp.type.value not in ("skill", "agent", "command"):
                    continue

                skill_slug = f"{toolkit.slug}--{comp.slug}"

                # Check if already activated
                cursor = await db.execute(
                    "SELECT id FROM skills WHERE source_component_id = ?",
                    (comp.id,),
                )
                if await cursor.fetchone():
                    continue

                skill_id = uuid.uuid4().hex
                await db.execute(
                    """INSERT INTO skills
                       (id, slug, name, description, content, language, domain,
                        requires, tags, is_default, is_active, created_by,
                        source_toolkit_id, source_component_id)
                       VALUES (?, ?, ?, ?, ?, '[]', '[]', '[]', ?, 0, 1, 'toolkit-activation', ?, ?)""",
                    (
                        skill_id,
                        skill_slug,
                        comp.name,
                        comp.description,
                        comp.content,
                        json.dumps(comp.tags),
                        toolkit.id,
                        comp.id,
                    ),
                )
                activated += 1

            await db.commit()

        return activated

    async def deactivate_toolkit(self, toolkit_slug: str, skill_db_path: str | None = None) -> int:
        """Remove Skill records that were created from this toolkit's components."""
        db_path = skill_db_path or self._db_path
        toolkit = await self.get_toolkit(toolkit_slug)
        if not toolkit:
            return 0

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "DELETE FROM skills WHERE source_toolkit_id = ?",
                (toolkit.id,),
            )
            await db.commit()
            return cursor.rowcount

    # ------------------------------------------------------------------
    # Row conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_source(row: aiosqlite.Row) -> ToolkitSource:
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
            category=ToolkitCategory(row["category"]),
            description=row["description"] or "",
            version=row["version"],
            pinned_sha=row["pinned_sha"],
            source_version=row["source_version"] if "source_version" in row.keys() else None,
            status=ToolkitStatus(row["status"]),
            tags=json.loads(row["tags"]) if row["tags"] else [],
            component_count=row["component_count"] or 0,
            is_active=bool(row["is_active"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    @staticmethod
    def _row_to_component(row: aiosqlite.Row) -> ToolkitComponent:
        return ToolkitComponent(
            id=row["id"],
            toolkit_id=row["toolkit_id"],
            slug=row["slug"],
            name=row["name"],
            type=ComponentType(row["type"]),
            description=row["description"] or "",
            directory=row["directory"] or "",
            primary_file=row["primary_file"] or "",
            load_strategy=LoadStrategy(row["load_strategy"]),
            content=row["content"] or "",
            tags=json.loads(row["tags"]) if row["tags"] else [],
            risk_level=RiskLevel(row["risk_level"]),
            is_active=bool(row["is_active"]),
            file_count=row["file_count"] if "file_count" in row.keys() else 0,
            created_at=_parse_dt(row["created_at"]),
        )

    @staticmethod
    def _row_to_file(row: aiosqlite.Row) -> ComponentFile:
        return ComponentFile(
            id=row["id"],
            component_id=row["component_id"],
            path=row["path"],
            filename=row["filename"],
            content=row["content"] or "",
            is_primary=bool(row["is_primary"]),
            created_at=_parse_dt(row["created_at"]),
        )

    @staticmethod
    def _row_to_version(row: aiosqlite.Row) -> ToolkitVersion:
        return ToolkitVersion(
            id=row["id"],
            toolkit_id=row["toolkit_id"],
            version=row["version"],
            pinned_sha=row["pinned_sha"],
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
