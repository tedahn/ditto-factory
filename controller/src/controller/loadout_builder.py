"""Builds an AgentLoadout from task context + toolkit components."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from controller.loadout import AgentLoadout

if TYPE_CHECKING:
    from controller.config import Settings
    from controller.skills.models import ClassificationResult
    from controller.toolkits.registry import ToolkitRegistry

logger = logging.getLogger(__name__)


class LoadoutBuilder:
    """Builds an AgentLoadout from task context and toolkit selections."""

    def __init__(
        self,
        toolkit_registry: ToolkitRegistry | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._toolkit_registry = toolkit_registry
        self._settings = settings

    async def build(
        self,
        thread_id: str,
        task_description: str = "",
        toolkit_slugs: list[str] | None = None,
        component_slugs: list[str] | None = None,
        classification_result: ClassificationResult | None = None,
        max_skill_chars: int = 16000,
    ) -> AgentLoadout:
        """Build a complete loadout for an agent.

        Sources of skills/tools (merged in order):
        1. Skills from classification result (existing skill system)
        2. Explicitly requested toolkit components (by toolkit slug)
        3. Explicitly requested individual components (by component slug)

        For each toolkit component, the mount strategy determines handling:
        - SKILL/AGENT/COMMAND -> added to skills list + mounted as files
        - PROFILE -> added to claude_md_additions
        - PLUGIN -> added to mcp_config
        - TOOL -> added to env_vars + mcp_config
        """
        loadout = AgentLoadout(thread_id=thread_id)

        # 1. Add classified skills (from existing skill system)
        if classification_result and classification_result.skills:
            for skill in classification_result.skills:
                if loadout.total_skill_chars + len(skill.content) > max_skill_chars:
                    logger.warning("Skill budget exceeded, stopping at %s", skill.slug)
                    break
                loadout.skills.append({
                    "name": skill.slug,
                    "content": skill.content,
                })

        # 2. Add toolkit components
        if self._toolkit_registry and toolkit_slugs:
            for tk_slug in toolkit_slugs:
                await self._add_toolkit_components(loadout, tk_slug, max_skill_chars)

        # 3. Add individual components
        if self._toolkit_registry and component_slugs:
            for comp_slug in component_slugs:
                await self._add_component_by_slug(loadout, comp_slug, max_skill_chars)

        logger.info(
            "Built loadout for %s: %d skills (%d chars), %d mounted files, %d env vars",
            thread_id,
            len(loadout.skills),
            loadout.total_skill_chars,
            loadout.total_mounted_files,
            len(loadout.env_vars),
        )

        return loadout

    async def _add_toolkit_components(
        self, loadout: AgentLoadout, toolkit_slug: str, max_chars: int
    ) -> None:
        """Add all components from a toolkit to the loadout."""
        toolkit = await self._toolkit_registry.get_toolkit(toolkit_slug)
        if not toolkit:
            logger.warning("Toolkit '%s' not found, skipping", toolkit_slug)
            return

        components = await self._toolkit_registry.list_components(toolkit.id)
        for comp in components:
            if not comp.is_active:
                continue
            self._add_component_to_loadout(loadout, comp, toolkit_slug, max_chars)

    async def _add_component_by_slug(
        self, loadout: AgentLoadout, component_slug: str, max_chars: int
    ) -> None:
        """Add a single component by its slug (format: toolkit_slug--component_slug)."""
        if "--" not in component_slug:
            logger.warning("Invalid component slug '%s', expected 'toolkit--component'", component_slug)
            return

        tk_slug, comp_slug = component_slug.split("--", 1)
        comp = await self._toolkit_registry.get_component_by_slug(tk_slug, comp_slug)
        if not comp:
            logger.warning("Component '%s' not found, skipping", component_slug)
            return

        self._add_component_to_loadout(loadout, comp, tk_slug, max_chars)

    def _add_component_to_loadout(
        self, loadout: AgentLoadout, comp, toolkit_slug: str, max_chars: int
    ) -> None:
        """Route a component to the right loadout section based on its type."""
        from controller.toolkits.models import ComponentType

        if comp.type in (ComponentType.SKILL, ComponentType.AGENT, ComponentType.COMMAND):
            # Add to skills (prompt injection path)
            if loadout.total_skill_chars + len(comp.content) <= max_chars:
                # Avoid duplicates
                existing_names = {s["name"] for s in loadout.skills}
                skill_name = f"{toolkit_slug}--{comp.slug}"
                if skill_name not in existing_names:
                    loadout.skills.append({
                        "name": skill_name,
                        "content": comp.content,
                    })

            # Also mount the primary file
            if comp.primary_file:
                mount_path = f".claude/skills/{toolkit_slug}/{comp.slug}/SKILL.md"
                loadout.mounted_files[mount_path] = comp.content

        elif comp.type == ComponentType.PROFILE:
            # Inject into CLAUDE.md additions
            if comp.content:
                loadout.claude_md_additions.append(comp.content)

        elif comp.type == ComponentType.PLUGIN:
            # Add to MCP config (future: parse plugin.json)
            logger.info("Plugin component '%s' noted (MCP integration pending)", comp.slug)

        elif comp.type == ComponentType.TOOL:
            # Add to env_vars or MCP config (future)
            logger.info("Tool component '%s' noted (package install pending)", comp.slug)
