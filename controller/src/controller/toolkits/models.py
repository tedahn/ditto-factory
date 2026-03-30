from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ToolkitType(str, Enum):
    SKILL = "skill"
    PLUGIN = "plugin"
    PROFILE = "profile"
    TOOL = "tool"


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


@dataclass
class ToolkitSource:
    id: str
    github_url: str
    github_owner: str
    github_repo: str
    branch: str = "main"
    last_commit_sha: str | None = None
    last_synced_at: datetime | None = None
    status: str = "active"
    metadata: dict = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class Toolkit:
    id: str
    source_id: str
    slug: str
    name: str
    type: ToolkitType
    description: str = ""
    path: str = ""
    load_strategy: LoadStrategy = LoadStrategy.MOUNT_FILE
    version: int = 1
    pinned_sha: str | None = None
    content: str = ""
    config: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.SAFE
    status: ToolkitStatus = ToolkitStatus.AVAILABLE
    usage_count: int = 0
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class ToolkitVersion:
    id: str
    toolkit_id: str
    version: int
    pinned_sha: str
    content: str = ""
    config: dict = field(default_factory=dict)
    changelog: str | None = None
    created_at: datetime | None = None


@dataclass
class DiscoveredItem:
    name: str
    type: ToolkitType
    path: str
    load_strategy: LoadStrategy
    description: str = ""
    tags: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.SAFE
    content: str = ""
    config: dict = field(default_factory=dict)
    frontmatter: dict = field(default_factory=dict)


@dataclass
class DiscoveryManifest:
    source_url: str
    owner: str
    repo: str
    branch: str
    commit_sha: str
    discovered: list[DiscoveredItem] = field(default_factory=list)
    recommendations: dict = field(default_factory=dict)
