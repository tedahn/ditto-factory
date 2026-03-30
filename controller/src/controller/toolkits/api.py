"""REST API endpoints for the Toolkit System.

  # Sources
  POST   /api/v1/toolkits/sources              - Create source from GitHub URL
  GET    /api/v1/toolkits/sources              - List all sources
  GET    /api/v1/toolkits/sources/{id}         - Get source details
  DELETE /api/v1/toolkits/sources/{id}         - Remove source
  POST   /api/v1/toolkits/sources/{id}/sync    - Check for updates

  # Discovery & Import
  POST   /api/v1/toolkits/discover             - Run discovery on a GitHub URL
  POST   /api/v1/toolkits/import               - Import items from discovery manifest

  # Toolkit CRUD
  GET    /api/v1/toolkits/                     - List toolkits (filterable)
  GET    /api/v1/toolkits/{slug}               - Get toolkit by slug
  PUT    /api/v1/toolkits/{slug}               - Update toolkit metadata
  DELETE /api/v1/toolkits/{slug}               - Soft-delete toolkit

  # Versions
  GET    /api/v1/toolkits/{slug}/versions      - Version history
  POST   /api/v1/toolkits/{slug}/rollback      - Rollback to version

  # Updates
  POST   /api/v1/toolkits/{slug}/update        - Apply pending update
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from controller.toolkits.github_client import GitHubClient, GitHubError
from controller.toolkits.models import (
    DiscoveredItem,
    LoadStrategy,
    RiskLevel,
    ToolkitStatus,
    ToolkitType,
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
    items: list[dict]  # list of { path, name?, type?, tags?, config? }


class DiscoverRequest(BaseModel):
    github_url: str
    branch: str | None = None


class ToolkitUpdateRequest(BaseModel):
    tags: list[str] | None = None
    status: str | None = None
    description: str | None = None


class RollbackRequest(BaseModel):
    target_version: int


# Response models

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


class ToolkitResponse(BaseModel):
    id: str
    source_id: str
    slug: str
    name: str
    type: str
    description: str
    path: str
    load_strategy: str
    version: int
    pinned_sha: str | None
    content: str
    config: dict
    tags: list[str]
    dependencies: list[str]
    risk_level: str
    status: str
    usage_count: int
    created_at: str | None
    updated_at: str | None


class ToolkitVersionResponse(BaseModel):
    id: str
    version: int
    pinned_sha: str
    changelog: str | None
    created_at: str | None


class DiscoveredItemResponse(BaseModel):
    name: str
    type: str
    path: str
    load_strategy: str
    description: str
    tags: list[str]
    dependencies: list[str]
    risk_level: str


class DiscoveryResponse(BaseModel):
    source_url: str
    owner: str
    repo: str
    branch: str
    commit_sha: str
    discovered: list[DiscoveredItemResponse]
    source_id: str | None = None  # set if source was created/found


class ToolkitListResponse(BaseModel):
    toolkits: list[ToolkitResponse]
    total: int


class SourceListResponse(BaseModel):
    sources: list[SourceResponse]
    total: int


# ---------------------------------------------------------------------------
# Dependency injection helpers (overridden in main.py / tests)
# ---------------------------------------------------------------------------

def get_toolkit_registry():
    """Provide the toolkit registry -- overridden via dependency_overrides."""
    raise NotImplementedError("Must be overridden via dependency_overrides")


def get_discovery_engine():
    """Provide the discovery engine -- overridden via dependency_overrides."""
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
    toolkit_count = len(await registry.list_toolkits(source_id=source.id))
    return _source_to_response(source, toolkit_count=toolkit_count)


@router.get("/sources", response_model=SourceListResponse)
async def list_sources(
    registry=Depends(get_toolkit_registry),
):
    """List all sources with toolkit counts."""
    sources = await registry.list_sources()
    results: list[SourceResponse] = []
    for source in sources:
        count = len(await registry.list_toolkits(source_id=source.id))
        results.append(_source_to_response(source, toolkit_count=count))
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
    count = len(await registry.list_toolkits(source_id=source.id))
    return _source_to_response(source, toolkit_count=count)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(
    source_id: str,
    registry=Depends(get_toolkit_registry),
):
    """Remove a source and disable associated toolkits."""
    source = await registry.get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    # Disable all associated toolkits
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
        # Mark all toolkits from this source as update_available
        toolkits = await registry.list_toolkits(source_id=source_id)
        for tk in toolkits:
            if tk.status == ToolkitStatus.AVAILABLE:
                await registry.mark_update_available(tk.slug)

    # Update source sync info
    updated_source = await registry.update_source_sync(source_id, latest_sha)
    if updated_source is None:
        raise HTTPException(status_code=404, detail="Source not found after update")

    count = len(await registry.list_toolkits(source_id=source_id))
    return _source_to_response(updated_source, toolkit_count=count)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

@router.post("/discover", response_model=DiscoveryResponse)
async def discover(
    body: DiscoverRequest,
    registry=Depends(get_toolkit_registry),
    discovery=Depends(get_discovery_engine),
):
    """Run discovery on a GitHub URL and return discovered items."""
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

    # Try to find or create the source
    source_id: str | None = None
    try:
        parsed = GitHubClient.parse_github_url(body.github_url)
        # Check if source already exists
        existing_sources = await registry.list_sources()
        for s in existing_sources:
            if (
                s.github_owner == manifest.owner
                and s.github_repo == manifest.repo
                and s.branch == manifest.branch
            ):
                source_id = s.id
                # Update sync info
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
        discovered=[
            DiscoveredItemResponse(
                name=item.name,
                type=item.type.value if isinstance(item.type, ToolkitType) else item.type,
                path=item.path,
                load_strategy=item.load_strategy.value
                if isinstance(item.load_strategy, LoadStrategy)
                else item.load_strategy,
                description=item.description,
                tags=item.tags,
                dependencies=item.dependencies,
                risk_level=item.risk_level.value
                if isinstance(item.risk_level, RiskLevel)
                else item.risk_level,
            )
            for item in manifest.discovered
        ],
        source_id=source_id,
    )


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

@router.post("/import", response_model=ToolkitListResponse, status_code=201)
async def import_toolkits(
    body: ToolkitImportRequest,
    registry=Depends(get_toolkit_registry),
):
    """Import selected items from a discovery manifest."""
    source = await registry.get_source(body.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    # Convert dict items to DiscoveredItem objects
    items: list[DiscoveredItem] = []
    for item_dict in body.items:
        try:
            item = DiscoveredItem(
                name=item_dict.get("name", ""),
                type=ToolkitType(item_dict.get("type", "skill")),
                path=item_dict.get("path", ""),
                load_strategy=LoadStrategy(
                    item_dict.get("load_strategy", "mount_file")
                ),
                description=item_dict.get("description", ""),
                tags=item_dict.get("tags", []),
                dependencies=item_dict.get("dependencies", []),
                risk_level=RiskLevel(item_dict.get("risk_level", "safe")),
                content=item_dict.get("content", ""),
                config=item_dict.get("config", {}),
            )
            items.append(item)
        except (ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid item: {exc}",
            )

    pinned_sha = source.last_commit_sha or "unknown"
    toolkits = await registry.import_from_manifest(
        source_id=body.source_id,
        items=items,
        pinned_sha=pinned_sha,
    )

    return ToolkitListResponse(
        toolkits=[_toolkit_to_response(tk) for tk in toolkits],
        total=len(toolkits),
    )


# ---------------------------------------------------------------------------
# Toolkit CRUD
# ---------------------------------------------------------------------------

@router.get("/", response_model=ToolkitListResponse)
async def list_toolkits(
    type: str | None = Query(default=None, description="Filter by toolkit type"),
    status: str | None = Query(default=None, description="Filter by status"),
    source_id: str | None = Query(default=None, description="Filter by source ID"),
    registry=Depends(get_toolkit_registry),
):
    """List toolkits with optional filtering."""
    type_filter = ToolkitType(type) if type else None
    status_filter = ToolkitStatus(status) if status else None

    toolkits = await registry.list_toolkits(
        type_filter=type_filter,
        status_filter=status_filter,
        source_id=source_id,
    )

    return ToolkitListResponse(
        toolkits=[_toolkit_to_response(tk) for tk in toolkits],
        total=len(toolkits),
    )


@router.get("/{slug}", response_model=ToolkitResponse)
async def get_toolkit(
    slug: str,
    registry=Depends(get_toolkit_registry),
):
    """Get toolkit details including content."""
    toolkit = await registry.get_toolkit(slug)
    if toolkit is None:
        raise HTTPException(status_code=404, detail=f"Toolkit '{slug}' not found")
    return _toolkit_to_response(toolkit)


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
    """Soft-delete a toolkit."""
    existing = await registry.get_toolkit(slug)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Toolkit '{slug}' not found")

    await registry.delete_toolkit(slug)
    return None


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

@router.post("/{slug}/update", response_model=ToolkitResponse)
async def apply_update(
    slug: str,
    registry=Depends(get_toolkit_registry),
    discovery=Depends(get_discovery_engine),
):
    """Apply a pending update: fetch latest content from source, create new version."""
    toolkit = await registry.get_toolkit(slug)
    if toolkit is None:
        raise HTTPException(status_code=404, detail=f"Toolkit '{slug}' not found")

    if toolkit.status != ToolkitStatus.UPDATE_AVAILABLE:
        raise HTTPException(
            status_code=400,
            detail="No update available for this toolkit",
        )

    # Fetch source info
    source = await registry.get_source(toolkit.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found for toolkit")

    try:
        gh = discovery._gh
        # Get latest commit
        commit = await gh.get_latest_commit(
            source.github_owner, source.github_repo, source.branch
        )
        latest_sha = commit["sha"]

        # Fetch latest content for the toolkit path
        new_content = await gh.get_file_content(
            source.github_owner,
            source.github_repo,
            toolkit.path,
            ref=source.branch,
        )
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}")

    updated = await registry.apply_update(
        slug=slug,
        new_content=new_content,
        new_sha=latest_sha,
        changelog=f"Updated from {toolkit.pinned_sha[:8] if toolkit.pinned_sha else 'unknown'} to {latest_sha[:8]}",
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to apply update")

    return _toolkit_to_response(updated)


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


def _toolkit_to_response(toolkit: Any) -> ToolkitResponse:
    """Convert an internal toolkit object to ToolkitResponse."""
    return ToolkitResponse(
        id=toolkit.id,
        source_id=toolkit.source_id,
        slug=toolkit.slug,
        name=toolkit.name,
        type=toolkit.type.value if isinstance(toolkit.type, ToolkitType) else toolkit.type,
        description=toolkit.description or "",
        path=toolkit.path or "",
        load_strategy=toolkit.load_strategy.value
        if isinstance(toolkit.load_strategy, LoadStrategy)
        else toolkit.load_strategy,
        version=toolkit.version,
        pinned_sha=toolkit.pinned_sha,
        content=toolkit.content or "",
        config=toolkit.config or {},
        tags=toolkit.tags or [],
        dependencies=toolkit.dependencies or [],
        risk_level=toolkit.risk_level.value
        if isinstance(toolkit.risk_level, RiskLevel)
        else toolkit.risk_level,
        status=toolkit.status.value
        if isinstance(toolkit.status, ToolkitStatus)
        else toolkit.status,
        usage_count=toolkit.usage_count or 0,
        created_at=_format_dt(toolkit.created_at),
        updated_at=_format_dt(toolkit.updated_at),
    )
