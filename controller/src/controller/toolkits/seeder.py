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
        """Seed curated sources, retrying any that exist but lack a toolkit."""
        sources = await self._registry.list_sources()

        # Build a set of already-successfully-imported source IDs
        existing_toolkits = await self._registry.list_toolkits()
        imported_source_ids = {t.source_id for t in existing_toolkits}

        results: dict = {"seeded": [], "skipped": [], "failed": []}

        for source_info in self.CURATED_SOURCES:
            # Check if this URL already has a source record
            existing_source = None
            for s in sources:
                if s.github_url == source_info["url"]:
                    existing_source = s
                    break

            # If source exists AND has a toolkit, skip entirely
            if existing_source and existing_source.id in imported_source_ids:
                results["skipped"].append(source_info["url"])
                continue

            try:
                # Run discovery
                manifest = await self._discovery.discover(source_info["url"])

                # Create source if it doesn't exist yet
                if not existing_source:
                    parsed = GitHubClient.parse_github_url(source_info["url"])
                    existing_source = await self._registry.create_source(
                        github_url=source_info["url"],
                        owner=parsed["owner"],
                        repo=parsed["repo"],
                        branch=parsed.get("branch", "main"),
                        commit_sha=manifest.commit_sha,
                        metadata={"description": source_info["description"], "seeded": True},
                    )

                # Import as a single toolkit with components
                if manifest.discovered:
                    toolkit = await self._registry.import_from_manifest(
                        source_id=existing_source.id,
                        manifest=manifest,
                    )
                    results["seeded"].append({
                        "repo": f"{manifest.owner}/{manifest.repo}",
                        "components_imported": toolkit.component_count,
                    })
                else:
                    results["seeded"].append({
                        "repo": source_info["url"],
                        "components_imported": 0,
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
