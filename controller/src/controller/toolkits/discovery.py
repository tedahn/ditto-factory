"""Discovery engine that analyzes a GitHub repo and produces a structured manifest."""

from __future__ import annotations

import logging
import re
import unicodedata
from fnmatch import fnmatch

import yaml

from controller.toolkits.github_client import GitHubClient
from controller.toolkits.models import (
    DiscoveredItem,
    DiscoveryManifest,
    LoadStrategy,
    RiskLevel,
    ToolkitType,
)

logger = logging.getLogger(__name__)

# Directories that should never be treated as category directories.
_NON_CATEGORY_DIRS: set[str] = {
    ".github",
    ".git",
    "docs",
    "examples",
    "tests",
    "test",
    "scripts",
    "node_modules",
    "__pycache__",
    ".vscode",
    ".idea",
    "dist",
    "build",
    "lib",
    "src",
    "vendor",
    "assets",
    "images",
    "static",
    "public",
}


class DiscoveryEngine:
    """Scans a GitHub repository and returns a :class:`DiscoveryManifest`
    describing every toolkit item found.

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
            A :class:`DiscoveryManifest` with all discovered items.
        """

        # 1. Parse URL
        parsed = GitHubClient.parse_github_url(github_url)
        owner = parsed["owner"]
        repo = parsed["repo"]
        url_branch = parsed["branch"]
        subpath = parsed["path"]

        # 2. Repo metadata (to learn default branch)
        repo_info = await self._gh.get_repo_info(owner, repo)

        # Determine effective branch
        effective_branch = branch or url_branch
        if effective_branch == "main" and repo_info.get("default_branch"):
            effective_branch = repo_info["default_branch"]

        # 3. Latest commit SHA
        commit = await self._gh.get_latest_commit(owner, repo, effective_branch)
        commit_sha = commit["sha"]

        # 4. Recursive file tree
        tree = await self._gh.get_tree(owner, repo, effective_branch)

        # 5. Filter to subdirectory if URL pointed at one
        if subpath:
            prefix = subpath.rstrip("/") + "/"
            tree = [
                {**item, "path": item["path"][len(prefix):]}
                for item in tree
                if item["path"].startswith(prefix) or item["path"] == subpath.rstrip("/")
            ]

        # 6 + 7. Run detection heuristics and fetch content
        blob_paths = {item["path"] for item in tree if item["type"] == "blob"}
        tree_paths = {item["path"] for item in tree if item["type"] == "tree"}

        discovered: list[DiscoveredItem] = []

        # Track paths already claimed so higher-priority rules win.
        claimed: set[str] = set()

        # --- Heuristic 1: .claude-plugin/ directory ---
        if ".claude-plugin" in tree_paths or any(
            p.startswith(".claude-plugin/") for p in blob_paths
        ):
            plugin_files = sorted(
                p for p in blob_paths if p.startswith(".claude-plugin/")
            )
            for path in plugin_files:
                claimed.add(path)
                full_path = f"{subpath}/{path}" if subpath else path
                content = await self._gh.get_file_content(
                    owner, repo, full_path, ref=effective_branch
                )
                frontmatter, body = self._parse_frontmatter(content)
                name = (
                    frontmatter.get("name")
                    or self._extract_first_heading(body)
                    or self._name_from_path(path)
                )
                discovered.append(
                    DiscoveredItem(
                        name=name,
                        type=ToolkitType.PLUGIN,
                        path=path,
                        load_strategy=LoadStrategy.INSTALL_PLUGIN,
                        description=frontmatter.get("description", "")
                        or self._extract_first_paragraph(body),
                        tags=self._generate_tags(path, content, frontmatter),
                        risk_level=self._classify_risk(
                            ToolkitType.PLUGIN, path, content
                        ),
                        content=content,
                        frontmatter=frontmatter,
                    )
                )

        # --- Heuristic 2: .claude/skills/**/*.md ---
        claude_skill_files = sorted(
            p
            for p in blob_paths
            if p.startswith(".claude/skills/") and p.endswith(".md")
        )
        for path in claude_skill_files:
            if path in claimed:
                continue
            claimed.add(path)
            discovered.append(
                await self._build_skill_item(
                    owner, repo, effective_branch, subpath, path
                )
            )

        # --- Heuristic 3: skills/**/*.md ---
        skills_files = sorted(
            p
            for p in blob_paths
            if p.startswith("skills/") and p.endswith(".md")
        )
        for path in skills_files:
            if path in claimed:
                continue
            claimed.add(path)
            discovered.append(
                await self._build_skill_item(
                    owner, repo, effective_branch, subpath, path
                )
            )

        # --- Heuristic 4: agents/**/*.md ---
        agent_files = sorted(
            p
            for p in blob_paths
            if p.startswith("agents/") and p.endswith(".md")
        )
        for path in agent_files:
            if path in claimed:
                continue
            claimed.add(path)
            discovered.append(
                await self._build_skill_item(
                    owner, repo, effective_branch, subpath, path
                )
            )

        # --- Heuristic 5: Category directories with .md files ---
        top_level_dirs: dict[str, list[str]] = {}
        for p in blob_paths:
            if p in claimed:
                continue
            parts = p.split("/")
            if len(parts) >= 2 and parts[0] not in _NON_CATEGORY_DIRS:
                dir_name = parts[0]
                if p.endswith(".md"):
                    top_level_dirs.setdefault(dir_name, []).append(p)

        # Only treat as category layout if multiple dirs qualify
        if len(top_level_dirs) >= 2:
            for category, paths in sorted(top_level_dirs.items()):
                for path in sorted(paths):
                    if path in claimed:
                        continue
                    claimed.add(path)
                    item = await self._build_skill_item(
                        owner, repo, effective_branch, subpath, path
                    )
                    # Add category tag
                    if category not in item.tags:
                        item.tags.insert(0, category)
                    discovered.append(item)

        # --- Heuristic 6: profiles/ or .claude/rules/ ---
        profile_files = sorted(
            p
            for p in blob_paths
            if (p.startswith("profiles/") or p.startswith(".claude/rules/"))
            and p not in claimed
        )
        for path in profile_files:
            claimed.add(path)
            full_path = f"{subpath}/{path}" if subpath else path
            content = await self._gh.get_file_content(
                owner, repo, full_path, ref=effective_branch
            )
            frontmatter, body = self._parse_frontmatter(content)
            name = (
                frontmatter.get("name")
                or self._extract_first_heading(body)
                or self._name_from_path(path)
            )
            discovered.append(
                DiscoveredItem(
                    name=name,
                    type=ToolkitType.PROFILE,
                    path=path,
                    load_strategy=LoadStrategy.INJECT_RULES,
                    description=frontmatter.get("description", "")
                    or self._extract_first_paragraph(body),
                    tags=self._generate_tags(path, content, frontmatter),
                    risk_level=self._classify_risk(
                        ToolkitType.PROFILE, path, content
                    ),
                    content=content,
                    frontmatter=frontmatter,
                )
            )

        # --- Heuristic 7: mcp.json / MCP server files ---
        mcp_files = sorted(
            p
            for p in blob_paths
            if (
                p == "mcp.json"
                or p.endswith("/mcp.json")
                or fnmatch(p, "**/mcp-server.*")
                or fnmatch(p, "mcp-server.*")
                or (p.endswith("server.js") or p.endswith("/server.js"))
            )
            and p not in claimed
        )
        for path in mcp_files:
            full_path = f"{subpath}/{path}" if subpath else path
            content = await self._gh.get_file_content(
                owner, repo, full_path, ref=effective_branch
            )
            # For server.js, only include if it imports MCP
            if path.endswith("server.js") and "modelcontextprotocol" not in content and "mcp" not in content.lower():
                continue
            claimed.add(path)
            frontmatter, body = self._parse_frontmatter(content)
            name = (
                frontmatter.get("name")
                or self._extract_first_heading(body)
                or self._name_from_path(path)
            )
            discovered.append(
                DiscoveredItem(
                    name=name,
                    type=ToolkitType.TOOL,
                    path=path,
                    load_strategy=LoadStrategy.INSTALL_PACKAGE,
                    description=frontmatter.get("description", "")
                    or self._extract_first_paragraph(body),
                    tags=self._generate_tags(path, content, frontmatter),
                    risk_level=RiskLevel.HIGH,
                    content=content,
                    frontmatter=frontmatter,
                )
            )

        # --- Heuristic 8: package.json with MCP deps ---
        pkg_jsons = sorted(
            p for p in blob_paths if p.endswith("package.json") and p not in claimed
        )
        for path in pkg_jsons:
            full_path = f"{subpath}/{path}" if subpath else path
            content = await self._gh.get_file_content(
                owner, repo, full_path, ref=effective_branch
            )
            if "@modelcontextprotocol/sdk" in content:
                claimed.add(path)
                frontmatter, body = self._parse_frontmatter(content)
                discovered.append(
                    DiscoveredItem(
                        name=self._name_from_path(path),
                        type=ToolkitType.TOOL,
                        path=path,
                        load_strategy=LoadStrategy.INSTALL_PACKAGE,
                        description="MCP tool package",
                        tags=self._generate_tags(path, content, frontmatter),
                        risk_level=RiskLevel.HIGH,
                        content=content,
                        frontmatter=frontmatter,
                    )
                )

        # --- Heuristic 9: pyproject.toml with CLI entry points ---
        pyproject_files = sorted(
            p for p in blob_paths if p.endswith("pyproject.toml") and p not in claimed
        )
        for path in pyproject_files:
            full_path = f"{subpath}/{path}" if subpath else path
            content = await self._gh.get_file_content(
                owner, repo, full_path, ref=effective_branch
            )
            if "[project.scripts]" in content or "[tool.poetry.scripts]" in content:
                claimed.add(path)
                frontmatter, body = self._parse_frontmatter(content)
                discovered.append(
                    DiscoveredItem(
                        name=self._name_from_path(path),
                        type=ToolkitType.TOOL,
                        path=path,
                        load_strategy=LoadStrategy.INSTALL_PACKAGE,
                        description="Python CLI tool package",
                        tags=self._generate_tags(path, content, frontmatter),
                        risk_level=RiskLevel.HIGH,
                        content=content,
                        frontmatter=frontmatter,
                    )
                )

        # 8. Build recommendations
        recommendations: dict = {}
        if not discovered:
            recommendations["note"] = (
                "No toolkits detected. Consider adding a skills/ directory "
                "with .md files or a .claude-plugin/ manifest."
            )

        return DiscoveryManifest(
            source_url=github_url,
            owner=owner,
            repo=repo,
            branch=effective_branch,
            commit_sha=commit_sha,
            discovered=discovered,
            recommendations=recommendations,
        )

    # ------------------------------------------------------------------ #
    # Helper: build a skill DiscoveredItem
    # ------------------------------------------------------------------ #

    async def _build_skill_item(
        self,
        owner: str,
        repo: str,
        branch: str,
        subpath: str,
        path: str,
    ) -> DiscoveredItem:
        """Fetch content for a skill .md file and return a DiscoveredItem."""
        full_path = f"{subpath}/{path}" if subpath else path
        content = await self._gh.get_file_content(
            owner, repo, full_path, ref=branch
        )
        frontmatter, body = self._parse_frontmatter(content)
        name = (
            frontmatter.get("name")
            or self._extract_first_heading(body)
            or self._name_from_path(path)
        )
        return DiscoveredItem(
            name=name,
            type=ToolkitType.SKILL,
            path=path,
            load_strategy=LoadStrategy.MOUNT_FILE,
            description=frontmatter.get("description", "")
            or self._extract_first_paragraph(body),
            tags=self._generate_tags(path, content, frontmatter),
            risk_level=self._classify_risk(ToolkitType.SKILL, path, content),
            content=content,
            frontmatter=frontmatter,
        )

    # ------------------------------------------------------------------ #
    # Helper methods
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
        # parts[0] is empty string before first ---
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

            # Skip headings
            if stripped.startswith("#"):
                # If we already collected text, we're done
                if paragraph_lines:
                    break
                collecting = True
                continue

            if not collecting:
                # Before we hit a heading, start collecting from first
                # non-empty line
                if stripped:
                    collecting = True

            if collecting:
                if stripped:
                    paragraph_lines.append(stripped)
                elif paragraph_lines:
                    # Empty line after paragraph content means end
                    break

        return " ".join(paragraph_lines)[:500] if paragraph_lines else ""

    @staticmethod
    def _classify_risk(
        item_type: ToolkitType, path: str, content: str
    ) -> RiskLevel:
        """Classify the risk level of a discovered item."""

        # Anything that installs packages or runs binaries is high risk
        if item_type == ToolkitType.TOOL:
            return RiskLevel.HIGH

        if item_type == ToolkitType.PLUGIN:
            return RiskLevel.HIGH

        # Check for executable code patterns in content
        high_risk_patterns = [
            "npm install",
            "pip install",
            "brew install",
            "apt-get install",
            "cargo install",
            "chmod +x",
            "sudo ",
            "curl ",
            "wget ",
        ]
        for pattern in high_risk_patterns:
            if pattern in content:
                return RiskLevel.HIGH

        # Moderate: has hooks, scripts, or config files
        moderate_indicators = [
            "```bash",
            "```shell",
            "```sh",
            "#!/",
            "pre-commit",
            "post-commit",
            "hooks/",
            ".sh",
        ]
        config_extensions = (".json", ".yaml", ".yml", ".toml", ".ini", ".cfg")

        if path.endswith(config_extensions):
            return RiskLevel.MODERATE

        for indicator in moderate_indicators:
            if indicator in content:
                return RiskLevel.MODERATE

        # Pure markdown with no risky code blocks
        if path.endswith(".md"):
            return RiskLevel.SAFE

        return RiskLevel.MODERATE

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
        skip_segments = {"src", "lib", "dist", "build", "index"}
        for part in path_parts[:-1]:  # skip the filename
            clean = part.strip(".").lower()
            if clean and clean not in skip_segments and clean not in tags:
                tags.append(clean)

        # Tags from content keywords (lightweight extraction)
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

    @staticmethod
    def _slugify(name: str) -> str:
        """Convert *name* to a URL-safe slug."""
        # Normalise unicode and lowercase
        slug = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
        slug = slug.lower()
        # Replace non-alphanumeric with hyphens
        slug = re.sub(r"[^a-z0-9]+", "-", slug)
        # Strip leading/trailing hyphens
        slug = slug.strip("-")
        return slug

    @staticmethod
    def _name_from_path(path: str) -> str:
        """Derive a human-readable name from a file path."""
        filename = path.rsplit("/", 1)[-1]
        # Remove extension
        name = filename.rsplit(".", 1)[0] if "." in filename else filename
        # Convert kebab/snake case to title case
        name = name.replace("-", " ").replace("_", " ")
        return name.title()
