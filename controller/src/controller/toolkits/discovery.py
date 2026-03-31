"""Discovery engine that analyzes a GitHub repo and produces a structured manifest.

Groups files by directory into components instead of treating each file
as a standalone toolkit entry.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import defaultdict
from pathlib import PurePosixPath

import yaml

from controller.toolkits.github_client import GitHubClient
from controller.toolkits.models import (
    ComponentType,
    DiscoveredComponent,
    DiscoveredFile,
    DiscoveryManifest,
    LoadStrategy,
    RiskLevel,
    ToolkitCategory,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exclusion sets
# ---------------------------------------------------------------------------

EXCLUDED_DIRS: set[str] = {
    ".github",
    ".git",
    "node_modules",
    "__pycache__",
    "docs",
    "examples",
    "tests",
    "test",
    ".devcontainer",
    ".githooks",
    ".codex",
    ".cursor-plugin",
    ".opencode",
    "preview",
    "screenshots",
    "cli",
    "hooks",
    ".vscode",
    ".idea",
    "dist",
    "build",
    "assets",
    "images",
    "static",
    "public",
}

EXCLUDED_FILES: set[str] = {
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    ".gitignore",
    ".gitattributes",
    "package.json",
    "package-lock.json",
    "CONTRIBUTING.md",
    "CLAUDE.md",
    "GEMINI.md",
    "AGENTS.md",
}

# Files that are usable component content.
_COMPONENT_EXTENSIONS: set[str] = {".md", ".json", ".yaml", ".yml", ".toml"}

# Known top-level directories that map to a ComponentType.
_TYPE_BY_DIR: dict[str, ComponentType] = {
    "skills": ComponentType.SKILL,
    ".claude/skills": ComponentType.SKILL,
    "agents": ComponentType.AGENT,
    "commands": ComponentType.COMMAND,
    "profiles": ComponentType.PROFILE,
    ".claude/rules": ComponentType.PROFILE,
    ".claude-plugin": ComponentType.PLUGIN,
}

# Files that indicate "primary" status, in priority order.
_PRIMARY_FILENAMES: tuple[str, ...] = (
    "SKILL.md",
    "plugin.json",
    "marketplace.json",
)


class DiscoveryEngine:
    """Scans a GitHub repository and returns a :class:`DiscoveryManifest`
    describing every component found, grouped by directory.

    Args:
        github_client: An initialised :class:`GitHubClient` instance.
    """

    def __init__(self, github_client: GitHubClient) -> None:
        self._gh = github_client

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def discover(
        self,
        github_url: str,
        branch: str | None = None,
    ) -> DiscoveryManifest:
        """Analyse a GitHub repository and return a discovery manifest.

        Args:
            github_url: Full GitHub URL (may include tree/blob path).
            branch: Override branch (uses URL branch or repo default).

        Returns:
            A :class:`DiscoveryManifest` with all discovered components.
        """

        # 1. Parse URL ------------------------------------------------
        parsed = GitHubClient.parse_github_url(github_url)
        owner = parsed["owner"]
        repo = parsed["repo"]
        url_branch = parsed["branch"]
        subpath = parsed["path"]

        # 2. Repo metadata -------------------------------------------
        repo_info = await self._gh.get_repo_info(owner, repo)
        effective_branch = branch or url_branch
        if effective_branch == "main" and repo_info.get("default_branch"):
            effective_branch = repo_info["default_branch"]

        # 3. Latest commit SHA ----------------------------------------
        commit = await self._gh.get_latest_commit(owner, repo, effective_branch)
        commit_sha = commit["sha"]

        # 4. Recursive file tree --------------------------------------
        tree = await self._gh.get_tree(owner, repo, effective_branch)

        # 5. Filter to subdirectory if URL pointed at one
        if subpath:
            prefix = subpath.rstrip("/") + "/"
            tree = [
                {**item, "path": item["path"][len(prefix):]}
                for item in tree
                if item["path"].startswith(prefix)
                or item["path"] == subpath.rstrip("/")
            ]

        blob_paths = {item["path"] for item in tree if item["type"] == "blob"}

        # 6. Classify the repo ----------------------------------------
        category = self._classify_repo(blob_paths)

        # 7. Group files into components ------------------------------
        raw_groups = self._group_files_into_components(blob_paths)

        # 8. Build DiscoveredComponent list ---------------------------
        discovered: list[DiscoveredComponent] = []
        for comp_dir, files in sorted(raw_groups.items()):
            comp_type = self._determine_component_type(comp_dir, blob_paths)
            primary_path = self._pick_primary_file(files)
            comp_files: list[DiscoveredFile] = []

            for fpath in sorted(files):
                is_primary = fpath == primary_path
                comp_files.append(
                    DiscoveredFile(
                        path=fpath,
                        filename=PurePosixPath(fpath).name,
                        is_primary=is_primary,
                    )
                )

            # Fetch content for primary file
            full_primary = f"{subpath}/{primary_path}" if subpath else primary_path
            try:
                content = await self._gh.get_file_content(
                    owner, repo, full_primary, ref=effective_branch
                )
            except Exception:
                logger.warning("Failed to fetch %s, skipping component", full_primary)
                continue

            # Update the primary DiscoveredFile with content
            for df in comp_files:
                if df.is_primary:
                    df.content = content
                    break

            # Extract metadata from primary content
            frontmatter, body = self._parse_frontmatter(content)
            name = (
                frontmatter.get("name")
                or self._extract_first_heading(body)
                or self._name_from_dir(comp_dir)
            )
            description = (
                frontmatter.get("description", "")
                or self._extract_first_paragraph(body)
            )
            tags = self._generate_tags(comp_dir, content, frontmatter)
            risk = self._classify_risk(comp_type, primary_path, content)
            load_strategy = self._determine_load_strategy(comp_type)

            # For single-file components keyed by full path, the
            # directory is the parent folder.
            is_single_file = comp_dir == primary_path
            actual_directory = (
                str(PurePosixPath(comp_dir).parent)
                if is_single_file
                else comp_dir
            )

            discovered.append(
                DiscoveredComponent(
                    name=name,
                    type=comp_type,
                    directory=actual_directory,
                    primary_file=primary_path,
                    load_strategy=load_strategy,
                    description=description,
                    tags=tags,
                    risk_level=risk,
                    files=comp_files,
                    frontmatter=frontmatter,
                )
            )

        return DiscoveryManifest(
            source_url=github_url,
            owner=owner,
            repo=repo,
            branch=effective_branch,
            commit_sha=commit_sha,
            repo_description=repo_info.get("description") or "",
            category=category,
            discovered=discovered,
        )

    # ------------------------------------------------------------------ #
    # Repo classification
    # ------------------------------------------------------------------ #

    @staticmethod
    def _classify_repo(blob_paths: set[str]) -> ToolkitCategory:
        """Classify the overall repo based on directory structure."""
        has_skills = any(
            p.startswith("skills/") or p.startswith(".claude/skills/")
            for p in blob_paths
        )
        has_plugin = any(p.startswith(".claude-plugin/") for p in blob_paths)
        has_profiles = any(
            p.startswith("profiles/") or p.startswith(".claude/rules/")
            for p in blob_paths
        )

        found = sum([has_skills, has_plugin, has_profiles])
        if found > 1:
            return ToolkitCategory.MIXED
        if has_skills:
            return ToolkitCategory.SKILL_COLLECTION
        if has_plugin:
            return ToolkitCategory.PLUGIN
        if has_profiles:
            return ToolkitCategory.PROFILE_PACK
        return ToolkitCategory.MIXED

    # ------------------------------------------------------------------ #
    # File grouping
    # ------------------------------------------------------------------ #

    def _group_files_into_components(
        self, blob_paths: set[str]
    ) -> dict[str, list[str]]:
        """Group files by their component directory.

        Returns a dict mapping component_directory -> list of file paths.
        """
        groups: dict[str, list[str]] = defaultdict(list)

        for path in blob_paths:
            if not self._is_component_file(path):
                continue

            comp_dir = self._resolve_component_directory(path)
            if comp_dir is not None:
                groups[comp_dir].append(path)

        return dict(groups)

    def _is_component_file(self, path: str) -> bool:
        """Check if a file should be included as a component file."""
        pp = PurePosixPath(path)

        # Excluded filenames
        if pp.name in EXCLUDED_FILES:
            return False

        # Must have a component-relevant extension
        if pp.suffix not in _COMPONENT_EXTENSIONS:
            return False

        # Check excluded directories — any segment in the path
        parts = pp.parts
        for part in parts:
            if part in EXCLUDED_DIRS:
                return False

        return True

    def _resolve_component_directory(self, path: str) -> str | None:
        """Determine which component directory a file belongs to.

        For known type dirs (skills/, agents/, commands/, etc.), files are
        grouped by the subdirectory one level below the type prefix.
        For category-style repos, each file is its own single-file component.

        Returns the component directory string, or None to skip.
        """
        pp = PurePosixPath(path)
        parts = pp.parts

        if len(parts) < 2:
            # Root-level file — not a component
            return None

        # .claude-plugin/ — entire directory is one component
        if parts[0] == ".claude-plugin":
            return ".claude-plugin"

        # Check for known type directories (skills/, agents/, etc.)
        # Try two-segment prefixes first (.claude/skills, .claude/rules)
        for prefix in sorted(_TYPE_BY_DIR.keys(), key=len, reverse=True):
            prefix_parts = PurePosixPath(prefix).parts
            if parts[: len(prefix_parts)] == prefix_parts:
                remaining = parts[len(prefix_parts) :]
                if not remaining:
                    return None

                # If file is in a subdirectory below the type dir,
                # group by that subdirectory.
                # e.g., skills/systematic-debugging/SKILL.md →
                #        component = skills/systematic-debugging
                # e.g., skills/systematic-debugging/examples/foo.md →
                #        component = skills/systematic-debugging (not examples/)
                if len(remaining) >= 2:
                    return str(PurePosixPath(*parts[: len(prefix_parts) + 1]))

                # Single file directly in the type dir
                # e.g., agents/code-reviewer.md
                # Each file = its own component, keyed by full path
                return path

        # Category-style directory (e.g., engineering/eng-frontend-dev.md)
        # Each file is its own single-file component, keyed by full path
        return path

    # ------------------------------------------------------------------ #
    # Component type & primary file
    # ------------------------------------------------------------------ #

    @staticmethod
    def _determine_component_type(
        comp_dir: str, blob_paths: set[str]
    ) -> ComponentType:
        """Determine the ComponentType for a component directory."""
        pp = PurePosixPath(comp_dir)
        parts = pp.parts

        for prefix, ctype in sorted(
            _TYPE_BY_DIR.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            prefix_parts = PurePosixPath(prefix).parts
            if parts[: len(prefix_parts)] == prefix_parts:
                return ctype

        if parts[0] == ".claude-plugin":
            return ComponentType.PLUGIN

        # Default: treat as agent (category-style repos)
        return ComponentType.AGENT

    @staticmethod
    def _pick_primary_file(files: list[str]) -> str:
        """Pick the primary file from a list of component files."""
        # Check for known primary filenames
        for primary_name in _PRIMARY_FILENAMES:
            for f in files:
                if PurePosixPath(f).name == primary_name:
                    return f

        # Pick the alphabetically first .md file
        md_files = sorted(f for f in files if f.endswith(".md"))
        if md_files:
            return md_files[0]

        # Fallback: alphabetically first file
        return sorted(files)[0]

    @staticmethod
    def _determine_load_strategy(comp_type: ComponentType) -> LoadStrategy:
        """Determine the load strategy based on component type."""
        if comp_type == ComponentType.PLUGIN:
            return LoadStrategy.INSTALL_PLUGIN
        if comp_type == ComponentType.PROFILE:
            return LoadStrategy.INJECT_RULES
        if comp_type == ComponentType.TOOL:
            return LoadStrategy.INSTALL_PACKAGE
        return LoadStrategy.MOUNT_FILE

    # ------------------------------------------------------------------ #
    # Helpers: frontmatter, headings, naming
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_frontmatter(content: str) -> tuple[dict, str]:
        """Parse YAML frontmatter delimited by ``---``.

        Returns:
            A tuple of (frontmatter_dict, remaining_content).
        """
        if not content.startswith("---"):
            return {}, content

        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}, content

        try:
            fm = yaml.safe_load(parts[1])
            if not isinstance(fm, dict):
                return {}, content
            return fm, parts[2].strip()
        except yaml.YAMLError:
            return {}, content

    @staticmethod
    def _extract_first_heading(content: str) -> str | None:
        """Return text of the first ``# Heading`` in *content*."""
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
        return None

    @staticmethod
    def _extract_first_paragraph(content: str) -> str:
        """Return the first non-empty paragraph after any heading."""
        lines = content.splitlines()
        collecting = False
        paragraph_lines: list[str] = []

        for line in lines:
            stripped = line.strip()

            if stripped.startswith("#"):
                if paragraph_lines:
                    break
                collecting = True
                continue

            if not collecting:
                if stripped:
                    collecting = True

            if collecting:
                if stripped:
                    paragraph_lines.append(stripped)
                elif paragraph_lines:
                    break

        return " ".join(paragraph_lines)[:500] if paragraph_lines else ""

    @staticmethod
    def _name_from_dir(comp_dir: str) -> str:
        """Derive a human-readable name from a component directory or file path."""
        last = PurePosixPath(comp_dir).name
        # Strip file extension if present
        if "." in last:
            last = last.rsplit(".", 1)[0]
        name = last.replace("-", " ").replace("_", " ")
        return name.title()

    @staticmethod
    def _slugify(name: str) -> str:
        """Convert *name* to a URL-safe slug."""
        slug = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
        slug = slug.lower()
        slug = re.sub(r"[^a-z0-9]+", "-", slug)
        slug = slug.strip("-")
        return slug

    # ------------------------------------------------------------------ #
    # Risk classification
    # ------------------------------------------------------------------ #

    @staticmethod
    def _classify_risk(
        comp_type: ComponentType, path: str, content: str
    ) -> RiskLevel:
        """Classify the risk level of a discovered component."""
        if comp_type in (ComponentType.TOOL, ComponentType.PLUGIN):
            return RiskLevel.HIGH

        high_risk_patterns = [
            "npm install", "pip install", "brew install", "apt-get install",
            "cargo install", "chmod +x", "sudo ", "curl ", "wget ",
        ]
        for pattern in high_risk_patterns:
            if pattern in content:
                return RiskLevel.HIGH

        moderate_indicators = [
            "```bash", "```shell", "```sh", "#!/",
            "pre-commit", "post-commit", "hooks/", ".sh",
        ]
        config_extensions = (".json", ".yaml", ".yml", ".toml", ".ini", ".cfg")

        if path.endswith(config_extensions):
            return RiskLevel.MODERATE

        for indicator in moderate_indicators:
            if indicator in content:
                return RiskLevel.MODERATE

        if path.endswith(".md"):
            return RiskLevel.SAFE

        return RiskLevel.MODERATE

    # ------------------------------------------------------------------ #
    # Tag generation
    # ------------------------------------------------------------------ #

    @staticmethod
    def _generate_tags(
        path: str, content: str, frontmatter: dict
    ) -> list[str]:
        """Auto-generate tags from path segments, frontmatter, and content."""
        tags: list[str] = []

        # Tags from frontmatter
        fm_tags = frontmatter.get("tags", [])
        if isinstance(fm_tags, list):
            tags.extend(str(t).lower() for t in fm_tags)
        elif isinstance(fm_tags, str):
            tags.extend(t.strip().lower() for t in fm_tags.split(","))

        # Tags from path segments (excluding filenames and common dirs)
        path_parts = path.split("/")
        skip_segments = {"src", "lib", "dist", "build", "index", "skills",
                         "agents", "commands", "profiles", ".claude"}
        for part in path_parts[:-1]:
            clean = part.strip(".").lower()
            if clean and clean not in skip_segments and clean not in tags:
                tags.append(clean)

        # Tags from content keywords
        keyword_patterns = {
            "typescript": r"\btypescript\b|\bTypeScript\b|\.ts\b",
            "python": r"\bpython\b|\bPython\b|\.py\b",
            "javascript": r"\bjavascript\b|\bJavaScript\b",
            "react": r"\breact\b|\bReact\b",
            "mcp": r"\bMCP\b|\bmodel.?context.?protocol\b",
            "api": r"\bAPI\b|\bapi\b",
            "testing": r"\btest(?:ing)?\b|\bTDD\b|\bjest\b|\bpytest\b",
            "security": r"\bsecurity\b|\bauth(?:entication)?\b|\bOAuth\b",
        }
        for tag, pattern in keyword_patterns.items():
            if tag not in tags and re.search(pattern, content):
                tags.append(tag)

        # De-duplicate while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for t in tags:
            if t not in seen:
                seen.add(t)
                deduped.append(t)

        return deduped
