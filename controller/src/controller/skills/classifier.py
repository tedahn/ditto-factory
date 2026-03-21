"""Tag-based task classifier (Phase 1 MVP -- no embeddings)."""

from __future__ import annotations

import logging

from controller.skills.models import ClassificationResult, Skill
from controller.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


class TaskClassifier:
    """Selects skills for a task using tag-based matching and budget enforcement."""

    def __init__(self, registry: SkillRegistry, settings) -> None:
        self._registry = registry
        self._settings = settings

    async def classify(
        self,
        task: str,
        language: list[str] | None = None,
        domain: list[str] | None = None,
    ) -> ClassificationResult:
        # Phase 1: tag-based matching only
        matched = await self._registry.search_by_tags(
            language=language,
            domain=domain,
            limit=getattr(self._settings, "skill_max_per_task", 5),
        )

        defaults = await self._registry.get_defaults()

        combined = self._merge_and_deduplicate(defaults, matched)

        max_chars = getattr(self._settings, "skill_max_total_chars", 16000)
        budgeted = self._enforce_budget(combined, max_chars)

        agent_type = self._resolve_agent_type_from_skills(budgeted)

        return ClassificationResult(
            skills=budgeted,
            agent_type=agent_type,
        )

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_and_deduplicate(
        defaults: list[Skill], matched: list[Skill]
    ) -> list[Skill]:
        seen: set[str] = set()
        result: list[Skill] = []
        for skill in defaults + matched:
            if skill.slug not in seen:
                seen.add(skill.slug)
                result.append(skill)
        return result

    @staticmethod
    def _enforce_budget(skills: list[Skill], max_chars: int) -> list[Skill]:
        total = 0
        accepted: list[Skill] = []
        for skill in skills:
            if total + len(skill.content) > max_chars:
                logger.warning("Skill budget exceeded, dropping %s", skill.slug)
                continue
            total += len(skill.content)
            accepted.append(skill)
        return accepted

    @staticmethod
    def _resolve_agent_type_from_skills(skills: list[Skill]) -> str:
        all_requires: set[str] = set()
        for skill in skills:
            all_requires.update(skill.requires or [])
        if "browser" in all_requires:
            return "frontend"
        return "general"
