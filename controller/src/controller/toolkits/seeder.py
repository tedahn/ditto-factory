"""Toolkit seeder -- pre-populates the registry with curated GitHub repos on first startup."""

from __future__ import annotations

import logging

from controller.toolkits.discovery import DiscoveryEngine
from controller.toolkits.github_client import GitHubClient
from controller.toolkits.registry import ToolkitRegistry

logger = logging.getLogger(__name__)


class ToolkitSeeder:
    """Seeds the toolkit registry with curated repos on first startup."""

    CURATED_SOURCES = [
        {
            "url": "https://github.com/obra/superpowers",
            "description": "Agentic skills framework -- TDD, debugging, planning, brainstorming, code review workflows",
        },
        {
            "url": "https://github.com/nextlevelbuilder/ui-ux-pro-max-skill",
            "description": "Design intelligence for UI/UX -- 50+ styles, 161 color palettes, 57 font pairings, component patterns",
        },
        {
            "url": "https://github.com/msitarzewski/agency-agents",
            "description": "Specialized agent personas -- frontend, backend, DevOps, security, marketing, design, and more",
        },
        {
            "url": "https://github.com/Panniantong/Agent-Reach",
            "description": "Internet access tools -- search Twitter, Reddit, YouTube, GitHub via MCP",
        },
        {
            "url": "https://github.com/shanraisshan/claude-code-best-practice",
            "description": "Claude Code best practices -- CLAUDE.md patterns, agent teams, development workflows",
        },
        {
            "url": "https://github.com/steveyegge/beads",
            "description": "Distributed graph issue tracker -- persistent memory for coding agents via Dolt",
        },
        {
            "url": "https://github.com/buildermethods/agent-os",
            "description": "Codebase standards injection -- profiles and spec-driven development",
        },
    ]

    def __init__(self, registry: ToolkitRegistry, discovery_engine: DiscoveryEngine):
        self._registry = registry
        self._discovery = discovery_engine

    async def seed_if_empty(self) -> dict:
        """Run seeding only if no sources exist yet. Returns summary."""
        sources = await self._registry.list_sources()
        if sources:
            return {"skipped": True, "reason": "sources already exist"}

        results: dict = {"seeded": [], "failed": []}
        for source_info in self.CURATED_SOURCES:
            try:
                # Run discovery
                manifest = await self._discovery.discover(source_info["url"])

                # Create source
                parsed = GitHubClient.parse_github_url(source_info["url"])
                source = await self._registry.create_source(
                    github_url=source_info["url"],
                    owner=parsed["owner"],
                    repo=parsed["repo"],
                    branch=parsed.get("branch", "main"),
                    commit_sha=manifest.commit_sha,
                    metadata={"description": source_info["description"], "seeded": True},
                )

                # Import all discovered items
                if manifest.discovered:
                    imported = await self._registry.import_from_manifest(
                        source_id=source.id,
                        items=manifest.discovered,
                        pinned_sha=manifest.commit_sha,
                    )
                    results["seeded"].append({
                        "repo": f"{parsed['owner']}/{parsed['repo']}",
                        "toolkits_imported": len(imported),
                    })
                else:
                    results["seeded"].append({
                        "repo": f"{parsed['owner']}/{parsed['repo']}",
                        "toolkits_imported": 0,
                    })
            except Exception as e:
                logger.warning(
                    "Failed to seed from %s: %s", source_info["url"], str(e)
                )
                results["failed"].append({
                    "url": source_info["url"],
                    "error": str(e),
                })

        return results
