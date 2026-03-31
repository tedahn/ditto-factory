"""Data models for the Toolkit Discovery & Registration system.

Hierarchy:
  Toolkit (1 per GitHub repo)
    └── Component (a usable unit: skill, plugin, profile, agent)
          └── ComponentFile (individual files within a component)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ToolkitCategory(str, Enum):
    """What kind of repo/toolkit is this overall."""
    SKILL_COLLECTION = "skill_collection"
    PLUGIN = "plugin"
    PROFILE_PACK = "profile_pack"
    TOOL = "tool"
    MIXED = "mixed"  # repos that contain multiple types


class ComponentType(str, Enum):
    """Type of an individual component within a toolkit."""
    SKILL = "skill"
    PLUGIN = "plugin"
    PROFILE = "profile"
    TOOL = "tool"
    AGENT = "agent"
    COMMAND = "command"


class LoadStrategy(str, Enum):
    MOUNT_FILE = "mount_file"
    INSTALL_PLUGIN = "install_plugin"
    INJECT_RULES = "inject_rules"
    INSTALL_PACKAGE = "install_package"


class RiskLevel(str, Enum):
    SAFE = "safe"
    MODERATE = "moderate"
    HIGH = "high"


class ToolkitStatus(str, Enum):
    AVAILABLE = "available"
    DISABLED = "disabled"
    UPDATE_AVAILABLE = "update_available"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Core Models (DB-backed)
# ---------------------------------------------------------------------------

@dataclass
class ToolkitSource:
    """A GitHub repository connection (source of toolkits)."""
    id: str
    github_url: str
    github_owner: str
    github_repo: str
    branch: str
    last_commit_sha: str | None = None
    last_synced_at: datetime | None = None
    status: str = "active"
    metadata: dict = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None

@dataclass
class Toolkit:
    """A toolkit = one GitHub repository imported as a unit."""
    id: str
    source_id: str
    slug: str                          # e.g. "superpowers"
    name: str                          # e.g. "Superpowers"
    category: ToolkitCategory          # what kind of repo
    description: str = ""
    version: int = 1
    pinned_sha: str | None = None
    source_version: str | None = None  # e.g. "v5.0.6", "3.0.0", or "main@abc1234"
    status: ToolkitStatus = ToolkitStatus.AVAILABLE
    tags: list[str] = field(default_factory=list)
    component_count: int = 0
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class ToolkitVersion:
    """Version history for a toolkit (repo-level)."""
    id: str
    toolkit_id: str
    version: int
    pinned_sha: str
    changelog: str | None = None
    created_at: datetime | None = None


@dataclass
class ToolkitComponent:
    """A usable unit within a toolkit (a skill, plugin, agent, etc.)."""
    id: str
    toolkit_id: str
    slug: str                          # e.g. "systematic-debugging"
    name: str                          # e.g. "Systematic Debugging"
    type: ComponentType                # skill, plugin, profile, etc.
    description: str = ""
    directory: str = ""                # e.g. "skills/systematic-debugging"
    primary_file: str = ""             # e.g. "skills/systematic-debugging/SKILL.md"
    load_strategy: LoadStrategy = LoadStrategy.MOUNT_FILE
    content: str = ""                  # primary file content cached
    tags: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.SAFE
    is_active: bool = True
    file_count: int = 0
    created_at: datetime | None = None


@dataclass
class ComponentFile:
    """An individual file within a component."""
    id: str
    component_id: str
    path: str                          # relative path within repo
    filename: str                      # just the filename
    content: str = ""                  # cached content
    is_primary: bool = False           # True for SKILL.md / main entry point
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Discovery Models (not DB-backed, used during import flow)
# ---------------------------------------------------------------------------

@dataclass
class DiscoveredFile:
    """A file found during discovery, before import."""
    path: str
    filename: str
    content: str = ""
    is_primary: bool = False


@dataclass
class DiscoveredComponent:
    """A component found during discovery — a directory-level unit."""
    name: str
    type: ComponentType
    directory: str                     # e.g. "skills/systematic-debugging"
    primary_file: str                  # e.g. "skills/systematic-debugging/SKILL.md"
    load_strategy: LoadStrategy = LoadStrategy.MOUNT_FILE
    description: str = ""
    tags: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.SAFE
    files: list[DiscoveredFile] = field(default_factory=list)
    frontmatter: dict = field(default_factory=dict)


@dataclass
class DiscoveryManifest:
    """Result of analyzing a GitHub repo."""
    source_url: str
    owner: str
    repo: str
    branch: str
    commit_sha: str
    repo_description: str = ""
    category: ToolkitCategory = ToolkitCategory.MIXED
    source_version: str | None = None  # detected version from releases/tags/config
    discovered: list[DiscoveredComponent] = field(default_factory=list)
