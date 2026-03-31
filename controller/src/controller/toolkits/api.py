"""REST API endpoints for the Toolkit System (hierarchical model).

  # Sources
  POST   /api/v1/toolkits/sources              - Create source from GitHub URL
  GET    /api/v1/toolkits/sources              - List all sources
  GET    /api/v1/toolkits/sources/{id}         - Get source details
  DELETE /api/v1/toolkits/sources/{id}         - Remove source
  POST   /api/v1/toolkits/sources/{id}/sync    - Check for updates

  # Discovery & Import
  POST   /api/v1/toolkits/discover             - Run discovery on a GitHub URL
  POST   /api/v1/toolkits/import               - Import components from discovery

  # Toolkit CRUD
  GET    /api/v1/toolkits/                     - List toolkits (filterable)
  GET    /api/v1/toolkits/{slug}               - Get toolkit detail with components
  PUT    /api/v1/toolkits/{slug}               - Update toolkit metadata
  DELETE /api/v1/toolkits/{slug}               - Soft-delete toolkit

  # Components
  GET    /api/v1/toolkits/{slug}/components                  - List components
  GET    /api/v1/toolkits/{slug}/components/{component_slug} - Component detail

  # Versions
  GET    /api/v1/toolkits/{slug}/versions      - Version history
  POST   /api/v1/toolkits/{slug}/rollback      - Rollback to version

  # Updates
  POST   /api/v1/toolkits/{slug}/update        - Apply pending update

  # GitHub Token
  GET    /api/v1/toolkits/github/status        - Token status
  POST   /api/v1/toolkits/github/token         - Set token
  DELETE /api/v1/toolkits/github/token         - Remove token
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from controller.toolkits.github_client import GitHubClient, GitHubError
from controller.toolkits.models import (
    ComponentType,
    DiscoveredComponent,
    DiscoveryManifest,
    LoadStrategy,
    RiskLevel,
    ToolkitCategory,
    ToolkitStatus,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic request/response schemas
# ---------------------------------------------------------------------------

# Request models

class SourceCreateRequest(BaseModel):
    github_url: str
    branch: str | None = None


class ToolkitImportRequest(BaseModel):
    source_id: str
    selected_components: list[str] | None = None  # component names to import, None = all


class DiscoverRequest(BaseModel):
    github_url: str
    branch: str | None = None


class ToolkitUpdateRequest(BaseModel):
    tags: list[str] | None = None
    status: str | None = None
    description: str | None = None


class RollbackRequest(BaseModel):
    target_version: int


# Response models — Hierarchical

class ComponentFileResponse(BaseModel):
    id: str
    path: str
    filename: str
    is_primary: bool


class ComponentResponse(BaseModel):
    id: str
    slug: str
    name: str
    type: str          # skill, plugin, profile, agent, command
    description: str
    directory: str
    primary_file: str
    load_strategy: str
    tags: list[str]
    risk_level: str
    is_active: bool
    file_count: int


class ComponentDetailResponse(ComponentResponse):
    content: str       # primary file content
    files: list[ComponentFileResponse]


class ToolkitResponse(BaseModel):
    id: str
    source_id: str
    slug: str
    name: str
    category: str      # skill_collection, plugin, profile_pack, tool, mixed
    description: str
    version: int
    pinned_sha: str | None
    source_version: str | None = None  # actual repo version (tag/release)
    status: str
    tags: list[str]
    component_count: int
    created_at: str | None
    updated_at: str | None
    # Source provenance
    source_owner: str | None = None
    source_repo: str | None = None
    source_branch: str | None = None


class ToolkitDetailResponse(ToolkitResponse):
    components: list[ComponentResponse]


class ToolkitListResponse(BaseModel):
    toolkits: list[ToolkitResponse]
    total: int


class ToolkitVersionResponse(BaseModel):
    id: str
    version: int
    pinned_sha: str
    changelog: str | None
    created_at: str | None


# Discovery response models

class DiscoveredFileResponse(BaseModel):
    path: str
    filename: str
    is_primary: bool


class DiscoveredComponentResponse(BaseModel):
    name: str
    type: str
    directory: str
    primary_file: str
    load_strategy: str
    description: str
    tags: list[str]
    risk_level: str
    files: list[DiscoveredFileResponse]


class DiscoveryResponse(BaseModel):
    source_url: str
    owner: str
    repo: str
    branch: str
    commit_sha: str
    repo_description: str
    category: str
    discovered: list[DiscoveredComponentResponse]
    source_id: str | None = None


# Source models

class SourceResponse(BaseModel):
    id: str
    github_url: str
    github_owner: str
    github_repo: str
    branch: str
    last_commit_sha: str | None
    last_synced_at: str | None
    status: str
    metadata: dict
    created_at: str | None
    updated_at: str | None
    toolkit_count: int = 0


class SourceListResponse(BaseModel):
    sources: list[SourceResponse]
    total: int


class GitHubTokenStatus(BaseModel):
    configured: bool
    rate_limit: int | None = None
    rate_remaining: int | None = None
    scopes: str | None = None


class GitHubTokenRequest(BaseModel):
    token: str


# ---------------------------------------------------------------------------
# Dependency injection helpers (overridden in main.py / tests)
# ---------------------------------------------------------------------------

def get_toolkit_registry():
    """Provide the toolkit registry -- overridden via dependency_overrides."""
    raise NotImplementedError("Must be overridden via dependency_overrides")


def get_discovery_engine():
    """Provide the discovery engine -- overridden via dependency_overrides."""
    raise NotImplementedError("Must be overridden via dependency_overrides")


def get_github_client():
    """Provide the GitHub client -- overridden via dependency_overrides."""
    raise NotImplementedError("Must be overridden via dependency_overrides")


def get_db_path():
    """Provide the database path -- overridden via dependency_overrides."""
    raise NotImplementedError("Must be overridden via dependency_overrides")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/toolkits")


# ---------------------------------------------------------------------------
# Sources CRUD
# ---------------------------------------------------------------------------

@router.post("/sources", response_model=SourceResponse, status_code=201)
async def create_source(
    body: SourceCreateRequest,
    registry=Depends(get_toolkit_registry),
):
    """Create a source from a GitHub URL."""
    try:
        parsed = GitHubClient.parse_github_url(body.github_url)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid GitHub URL: {exc}")

    owner = parsed["owner"]
    repo = parsed["repo"]
    branch = body.branch or parsed.get("branch", "main")

    source = await registry.create_source(
        github_url=body.github_url,
        owner=owner,
        repo=repo,
        branch=branch,
    )
    toolkits = await registry.list_toolkits(source_id=source.id)
    return _source_to_response(source, toolkit_count=len(toolkits))


@router.get("/sources", response_model=SourceListResponse)
async def list_sources(
    registry=Depends(get_toolkit_registry),
):
    """List all sources with toolkit counts."""
    sources = await registry.list_sources()
    results: list[SourceResponse] = []
    for source in sources:
        toolkits = await registry.list_toolkits(source_id=source.id)
        results.append(_source_to_response(source, toolkit_count=len(toolkits)))
    return SourceListResponse(sources=results, total=len(results))


@router.get("/sources/{source_id}", response_model=SourceResponse)
async def get_source(
    source_id: str,
    registry=Depends(get_toolkit_registry),
):
    """Get source details."""
    source = await registry.get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    toolkits = await registry.list_toolkits(source_id=source.id)
    return _source_to_response(source, toolkit_count=len(toolkits))


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(
    source_id: str,
    registry=Depends(get_toolkit_registry),
):
    """Remove a source and disable associated toolkits."""
    source = await registry.get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    toolkits = await registry.list_toolkits(source_id=source_id)
    for tk in toolkits:
        await registry.update_toolkit(tk.slug, status=ToolkitStatus.DISABLED)

    await registry.delete_source(source_id)
    return None


@router.post("/sources/{source_id}/sync", response_model=SourceResponse)
async def sync_source(
    source_id: str,
    registry=Depends(get_toolkit_registry),
    discovery=Depends(get_discovery_engine),
):
    """Check for updates: compare current SHA to latest, mark outdated toolkits."""
    source = await registry.get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    try:
        gh = discovery._gh
        commit = await gh.get_latest_commit(
            source.github_owner, source.github_repo, source.branch
        )
        latest_sha = commit["sha"]
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}")

    if source.last_commit_sha and latest_sha != source.last_commit_sha:
        toolkits = await registry.list_toolkits(source_id=source_id)
        for tk in toolkits:
            if tk.status == ToolkitStatus.AVAILABLE:
                await registry.mark_update_available(tk.slug)

    updated_source = await registry.update_source_sync(source_id, latest_sha)
    if updated_source is None:
        raise HTTPException(status_code=404, detail="Source not found after update")

    toolkits = await registry.list_toolkits(source_id=source_id)
    return _source_to_response(updated_source, toolkit_count=len(toolkits))


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

@router.post("/discover", response_model=DiscoveryResponse)
async def discover(
    body: DiscoverRequest,
    registry=Depends(get_toolkit_registry),
    discovery=Depends(get_discovery_engine),
):
    """Run discovery on a GitHub URL and return discovered components."""
    try:
        manifest = await discovery.discover(
            github_url=body.github_url,
            branch=body.branch,
        )
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}")
    except Exception as exc:
        logger.exception("Discovery failed for %s", body.github_url)
        raise HTTPException(status_code=500, detail=f"Discovery failed: {exc}")

    # Find or create the source
    source_id: str | None = None
    try:
        existing_sources = await registry.list_sources()
        for s in existing_sources:
            if (
                s.github_owner == manifest.owner
                and s.github_repo == manifest.repo
                and s.branch == manifest.branch
            ):
                source_id = s.id
                await registry.update_source_sync(s.id, manifest.commit_sha)
                break

        if source_id is None:
            source = await registry.create_source(
                github_url=body.github_url,
                owner=manifest.owner,
                repo=manifest.repo,
                branch=manifest.branch,
                commit_sha=manifest.commit_sha,
            )
            source_id = source.id
    except Exception:
        logger.exception("Failed to create/find source during discovery")

    return DiscoveryResponse(
        source_url=manifest.source_url,
        owner=manifest.owner,
        repo=manifest.repo,
        branch=manifest.branch,
        commit_sha=manifest.commit_sha,
        repo_description=manifest.repo_description,
        category=manifest.category.value
        if isinstance(manifest.category, ToolkitCategory)
        else manifest.category,
        discovered=[
            DiscoveredComponentResponse(
                name=comp.name,
                type=comp.type.value
                if isinstance(comp.type, ComponentType)
                else comp.type,
                directory=comp.directory,
                primary_file=comp.primary_file,
                load_strategy=comp.load_strategy.value
                if isinstance(comp.load_strategy, LoadStrategy)
                else comp.load_strategy,
                description=comp.description,
                tags=comp.tags,
                risk_level=comp.risk_level.value
                if isinstance(comp.risk_level, RiskLevel)
                else comp.risk_level,
                files=[
                    DiscoveredFileResponse(
                        path=f.path,
                        filename=f.filename,
                        is_primary=f.is_primary,
                    )
                    for f in comp.files
                ],
            )
            for comp in manifest.discovered
        ],
        source_id=source_id,
    )


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

@router.post("/import", response_model=ToolkitResponse, status_code=201)
async def import_toolkits(
    body: ToolkitImportRequest,
    registry=Depends(get_toolkit_registry),
    discovery=Depends(get_discovery_engine),
):
    """Import components from a source. Re-discovers and imports as a toolkit."""
    source = await registry.get_source(body.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    # Re-discover to get the manifest (manifests are not persisted)
    try:
        manifest = await discovery.discover(
            github_url=source.github_url,
            branch=source.branch,
        )
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}")
    except Exception as exc:
        logger.exception("Re-discovery failed for source %s", body.source_id)
        raise HTTPException(status_code=500, detail=f"Discovery failed: {exc}")

    # Import via registry
    toolkit = await registry.import_from_manifest(
        source_id=body.source_id,
        manifest=manifest,
        selected_components=body.selected_components,
    )

    # Update source sync info
    await registry.update_source_sync(body.source_id, manifest.commit_sha)

    source_obj = await registry.get_source(body.source_id)
    return _toolkit_to_response(toolkit, source_obj)


# ---------------------------------------------------------------------------
# Toolkit CRUD
# ---------------------------------------------------------------------------

@router.get("/", response_model=ToolkitListResponse)
async def list_toolkits(
    category: str | None = Query(default=None, description="Filter by category"),
    status: str | None = Query(default=None, description="Filter by status"),
    source_id: str | None = Query(default=None, description="Filter by source ID"),
    registry=Depends(get_toolkit_registry),
):
    """List toolkits with optional filtering."""
    category_filter = ToolkitCategory(category) if category else None
    status_filter = ToolkitStatus(status) if status else None

    toolkits = await registry.list_toolkits(
        category_filter=category_filter,
        status_filter=status_filter,
        source_id=source_id,
    )

    # Build source lookup for provenance
    sources = await registry.list_sources()
    source_map = {s.id: s for s in sources}

    return ToolkitListResponse(
        toolkits=[
            _toolkit_to_response(tk, source_map.get(tk.source_id))
            for tk in toolkits
        ],
        total=len(toolkits),
    )


@router.get("/{slug}", response_model=ToolkitDetailResponse)
async def get_toolkit(
    slug: str,
    registry=Depends(get_toolkit_registry),
):
    """Get toolkit details including component list."""
    toolkit = await registry.get_toolkit(slug)
    if toolkit is None:
        raise HTTPException(status_code=404, detail=f"Toolkit '{slug}' not found")

    source = await registry.get_source(toolkit.source_id)
    components = await registry.list_components(toolkit.id)

    base = _toolkit_to_response(toolkit, source)
    return ToolkitDetailResponse(
        **base.model_dump(),
        components=[_component_to_response(c) for c in components],
    )


@router.put("/{slug}", response_model=ToolkitResponse)
async def update_toolkit(
    slug: str,
    body: ToolkitUpdateRequest,
    registry=Depends(get_toolkit_registry),
):
    """Update toolkit metadata (tags, status, description)."""
    existing = await registry.get_toolkit(slug)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Toolkit '{slug}' not found")

    kwargs: dict[str, Any] = {}
    if body.tags is not None:
        kwargs["tags"] = body.tags
    if body.status is not None:
        kwargs["status"] = ToolkitStatus(body.status)
    if body.description is not None:
        kwargs["description"] = body.description

    if not kwargs:
        return _toolkit_to_response(existing)

    updated = await registry.update_toolkit(slug, **kwargs)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Toolkit '{slug}' not found")
    return _toolkit_to_response(updated)


@router.delete("/{slug}", status_code=204)
async def delete_toolkit(
    slug: str,
    registry=Depends(get_toolkit_registry),
):
    """Soft-delete a toolkit and its components."""
    existing = await registry.get_toolkit(slug)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Toolkit '{slug}' not found")

    await registry.delete_toolkit(slug)
    return None


# ---------------------------------------------------------------------------
# Activation bridge
# ---------------------------------------------------------------------------

@router.post("/{slug}/activate")
async def activate_toolkit(
    slug: str,
    registry=Depends(get_toolkit_registry),
):
    """Activate toolkit components as skills in the skill system."""
    toolkit = await registry.get_toolkit(slug)
    if not toolkit:
        raise HTTPException(status_code=404, detail=f"Toolkit '{slug}' not found")
    count = await registry.activate_toolkit(slug)
    return {"activated": count, "toolkit": slug}


@router.post("/{slug}/deactivate")
async def deactivate_toolkit(
    slug: str,
    registry=Depends(get_toolkit_registry),
):
    """Remove toolkit components from the skill system."""
    toolkit = await registry.get_toolkit(slug)
    if not toolkit:
        raise HTTPException(status_code=404, detail=f"Toolkit '{slug}' not found")
    count = await registry.deactivate_toolkit(slug)
    return {"deactivated": count, "toolkit": slug}


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------

@router.get("/{slug}/components", response_model=list[ComponentResponse])
async def list_components(
    slug: str,
    registry=Depends(get_toolkit_registry),
):
    """List all components for a toolkit."""
    toolkit = await registry.get_toolkit(slug)
    if toolkit is None:
        raise HTTPException(status_code=404, detail=f"Toolkit '{slug}' not found")

    components = await registry.list_components(toolkit.id)
    return [_component_to_response(c) for c in components]


@router.get("/{slug}/components/{component_slug}", response_model=ComponentDetailResponse)
async def get_component(
    slug: str,
    component_slug: str,
    registry=Depends(get_toolkit_registry),
):
    """Get component detail with files and primary file content."""
    toolkit = await registry.get_toolkit(slug)
    if toolkit is None:
        raise HTTPException(status_code=404, detail=f"Toolkit '{slug}' not found")

    component = await registry.get_component(toolkit.id, component_slug)
    if component is None:
        raise HTTPException(
            status_code=404,
            detail=f"Component '{component_slug}' not found in toolkit '{slug}'",
        )

    files = await registry.list_component_files(component.id)
    comp_resp = _component_to_response(component)

    return ComponentDetailResponse(
        **comp_resp.model_dump(),
        content=component.content or "",
        files=[
            ComponentFileResponse(
                id=f.id,
                path=f.path,
                filename=f.filename,
                is_primary=f.is_primary,
            )
            for f in files
        ],
    )


# ---------------------------------------------------------------------------
# Versions & Rollback
# ---------------------------------------------------------------------------

@router.get("/{slug}/versions", response_model=list[ToolkitVersionResponse])
async def list_versions(
    slug: str,
    registry=Depends(get_toolkit_registry),
):
    """List all versions of a toolkit."""
    existing = await registry.get_toolkit(slug)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Toolkit '{slug}' not found")

    versions = await registry.get_versions(slug)
    return [
        ToolkitVersionResponse(
            id=v.id,
            version=v.version,
            pinned_sha=v.pinned_sha,
            changelog=v.changelog,
            created_at=_format_dt(v.created_at),
        )
        for v in versions
    ]


@router.post("/{slug}/rollback", response_model=ToolkitResponse)
async def rollback_toolkit(
    slug: str,
    body: RollbackRequest,
    registry=Depends(get_toolkit_registry),
):
    """Rollback a toolkit to a specific version."""
    existing = await registry.get_toolkit(slug)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Toolkit '{slug}' not found")

    toolkit = await registry.rollback(slug, body.target_version)
    if toolkit is None:
        raise HTTPException(status_code=404, detail="Target version not found")

    return _toolkit_to_response(toolkit)


# ---------------------------------------------------------------------------
# Updates
# ---------------------------------------------------------------------------

@router.post("/{slug}/update", response_model=ToolkitDetailResponse)
async def apply_update(
    slug: str,
    registry=Depends(get_toolkit_registry),
    discovery=Depends(get_discovery_engine),
):
    """Apply a pending update: re-discover from source, update components."""
    toolkit = await registry.get_toolkit(slug)
    if toolkit is None:
        raise HTTPException(status_code=404, detail=f"Toolkit '{slug}' not found")

    if toolkit.status != ToolkitStatus.UPDATE_AVAILABLE:
        raise HTTPException(
            status_code=400,
            detail="No update available for this toolkit",
        )

    source = await registry.get_source(toolkit.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found for toolkit")

    try:
        # Re-discover the repo to get updated components
        manifest = await discovery.discover(
            github_url=source.github_url,
            branch=source.branch,
        )
        latest_sha = manifest.commit_sha
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}")
    except Exception as exc:
        logger.exception("Re-discovery failed during update for %s", slug)
        raise HTTPException(status_code=500, detail=f"Update failed: {exc}")

    changelog = (
        f"Updated from {toolkit.pinned_sha[:8] if toolkit.pinned_sha else 'unknown'}"
        f" to {latest_sha[:8]}"
    )

    updated = await registry.apply_update(
        slug=slug,
        new_sha=latest_sha,
        changelog=changelog,
        updated_components=manifest.discovered,
        source_version=getattr(manifest, "source_version", None),
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to apply update")

    # Update source sync info
    await registry.update_source_sync(source.id, latest_sha)

    # Return full detail response with components
    components = await registry.list_components(updated.id)
    base = _toolkit_to_response(updated, source)
    return ToolkitDetailResponse(
        **base.model_dump(),
        components=[_component_to_response(c) for c in components],
    )


# ---------------------------------------------------------------------------
# GitHub Token Management
# ---------------------------------------------------------------------------

@router.get("/github/status", response_model=GitHubTokenStatus)
async def get_github_status(
    client=Depends(get_github_client),
):
    """Check if a GitHub token is configured and its rate limit status."""
    import httpx

    has_token = bool(client.token)
    if not has_token:
        return GitHubTokenStatus(configured=False)

    try:
        async with httpx.AsyncClient() as http:
            resp = await http.get(
                "https://api.github.com/rate_limit",
                headers=dict(client._client.headers),
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                core = data.get("resources", {}).get("core", {})
                return GitHubTokenStatus(
                    configured=True,
                    rate_limit=core.get("limit"),
                    rate_remaining=core.get("remaining"),
                    scopes=resp.headers.get("x-oauth-scopes", ""),
                )
    except Exception:
        pass

    return GitHubTokenStatus(configured=True)


@router.post("/github/token", response_model=GitHubTokenStatus)
async def set_github_token(
    body: GitHubTokenRequest,
    client=Depends(get_github_client),
    db_path=Depends(get_db_path),
):
    """Set the GitHub token for toolkit discovery. Validates and persists it."""
    import httpx

    try:
        async with httpx.AsyncClient() as http:
            resp = await http.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {body.token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "ditto-factory",
                },
                timeout=10.0,
            )
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid GitHub token — authentication failed",
                )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to reach GitHub API: {exc}"
        )

    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT (datetime('now'))
            )
        """)
        await db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            ("github_token", body.token),
        )
        await db.commit()

    client.token = body.token
    client._client.headers["Authorization"] = f"Bearer {body.token}"

    return await get_github_status(client=client)


@router.delete("/github/token")
async def remove_github_token(
    client=Depends(get_github_client),
    db_path=Depends(get_db_path),
):
    """Remove the stored GitHub token."""
    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM app_settings WHERE key = ?", ("github_token",))
        await db.commit()

    client.token = None
    if "Authorization" in client._client.headers:
        del client._client.headers["Authorization"]

    return {"status": "removed"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_dt(dt: Any) -> str | None:
    """Format a datetime to ISO string, handling None."""
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.isoformat()


def _source_to_response(
    source: Any, toolkit_count: int = 0
) -> SourceResponse:
    """Convert an internal source object to SourceResponse."""
    return SourceResponse(
        id=source.id,
        github_url=source.github_url,
        github_owner=source.github_owner,
        github_repo=source.github_repo,
        branch=source.branch,
        last_commit_sha=source.last_commit_sha,
        last_synced_at=_format_dt(source.last_synced_at),
        status=source.status,
        metadata=source.metadata or {},
        created_at=_format_dt(source.created_at),
        updated_at=_format_dt(source.updated_at),
        toolkit_count=toolkit_count,
    )


def _toolkit_to_response(toolkit: Any, source: Any = None) -> ToolkitResponse:
    """Convert an internal toolkit object to ToolkitResponse."""
    return ToolkitResponse(
        id=toolkit.id,
        source_id=toolkit.source_id,
        slug=toolkit.slug,
        name=toolkit.name,
        category=toolkit.category.value
        if isinstance(toolkit.category, ToolkitCategory)
        else toolkit.category,
        description=toolkit.description or "",
        version=toolkit.version,
        pinned_sha=toolkit.pinned_sha,
        source_version=getattr(toolkit, "source_version", None),
        status=toolkit.status.value
        if isinstance(toolkit.status, ToolkitStatus)
        else toolkit.status,
        tags=toolkit.tags or [],
        component_count=toolkit.component_count or 0,
        created_at=_format_dt(toolkit.created_at),
        updated_at=_format_dt(toolkit.updated_at),
        source_owner=getattr(source, "github_owner", None) if source else None,
        source_repo=getattr(source, "github_repo", None) if source else None,
        source_branch=getattr(source, "branch", None) if source else None,
    )


def _component_to_response(component: Any) -> ComponentResponse:
    """Convert an internal component object to ComponentResponse."""
    return ComponentResponse(
        id=component.id,
        slug=component.slug,
        name=component.name,
        type=component.type.value
        if isinstance(component.type, ComponentType)
        else component.type,
        description=component.description or "",
        directory=component.directory or "",
        primary_file=component.primary_file or "",
        load_strategy=component.load_strategy.value
        if isinstance(component.load_strategy, LoadStrategy)
        else component.load_strategy,
        tags=component.tags or [],
        risk_level=component.risk_level.value
        if isinstance(component.risk_level, RiskLevel)
        else component.risk_level,
        is_active=component.is_active,
        file_count=component.file_count or 0,
    )
