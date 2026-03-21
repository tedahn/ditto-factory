"""Task classifier that matches tasks to skills.

Phase 1: tag-based matching (language, domain).
Phase 2: embedding similarity search with tag-based fallback.
"""

from __future__ import annotations

import logging

from controller.skills.embedding import EmbeddingError, EmbeddingProvider
from controller.skills.embedding_cache import EmbeddingCache
from controller.skills.models import (
    ClassificationResult,
    Skill,
    SkillFilters,
)
from controller.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


class TaskClassifier:
    """Classify a task description to find matching skills.

    When an embedding provider is available, the classifier tries semantic
    search first and falls back to tag-based matching on failure.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        embedding_provider: EmbeddingProvider | None = None,
        settings: object | None = None,
    ) -> None:
        self._registry = registry
        self._embedder = embedding_provider
        self._settings = settings
        self._cache = EmbeddingCache(max_size=500)

    async def classify(
        self,
        task: str,
        language: list[str] | None = None,
        domain: list[str] | None = None,
    ) -> ClassificationResult:
        """Classify a task and return matching skills.

        Strategy:
        1. Try semantic search if embedding provider is configured.
        2. Fall back to tag-based search if embeddings fail or return nothing.
        3. Merge with default skills, enforce budget limits.
        """
        matched_skills: list[Skill] = []
        task_embedding: list[float] | None = None
        filters = SkillFilters(language=language, domain=domain)

        # Phase 2: Try semantic search first
        if self._embedder:
            try:
                # Check cache before calling embedding API
                task_embedding = self._cache.get(task)
                if task_embedding is None:
                    task_embedding = await self._embedder.embed(task)
                    self._cache.put(task, task_embedding)

                scored = await self._registry.search_by_embedding(
                    task_embedding=task_embedding,
                    filters=filters,
                    limit=20,
                )
                # Apply minimum similarity threshold
                min_sim = getattr(self._settings, "skill_min_similarity", 0.5)
                matched_skills = [s.skill for s in scored if s.score >= min_sim]
            except EmbeddingError:
                logger.warning("Embedding failed, falling back to tag-based search")
                task_embedding = None
                matched_skills = []

        # Phase 1 fallback: tag-based search
        if not matched_skills:
            max_per_task = getattr(self._settings, "skill_max_per_task", 5)
            matched_skills = await self._registry.search_by_tags(
                language=language,
                domain=domain,
                limit=max_per_task,
            )

        # Get default skills and merge
        defaults = await self._registry.get_defaults()
        combined = self._merge_and_deduplicate(defaults, matched_skills)

        # Enforce budget constraints
        max_chars = getattr(self._settings, "skill_max_total_chars", 16000)
        max_count = getattr(self._settings, "skill_max_per_task", 5)
        budgeted = self._enforce_budget(combined[:max_count], max_chars)

        # Resolve agent type from matched skills
        agent_type = self._resolve_agent_type_from_skills(budgeted)

        return ClassificationResult(
            skills=budgeted,
            agent_type=agent_type,
            task_embedding=task_embedding,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_and_deduplicate(
        defaults: list[Skill], matched: list[Skill]
    ) -> list[Skill]:
        """Merge default skills with matched skills, removing duplicates."""
        seen_slugs: set[str] = set()
        result: list[Skill] = []

        for skill in defaults:
            if skill.slug not in seen_slugs:
                seen_slugs.add(skill.slug)
                result.append(skill)

        for skill in matched:
            if skill.slug not in seen_slugs:
                seen_slugs.add(skill.slug)
                result.append(skill)

        return result

    @staticmethod
    def _enforce_budget(skills: list[Skill], max_chars: int) -> list[Skill]:
        """Trim skill list to stay within total character budget."""
        result: list[Skill] = []
        total_chars = 0

        for skill in skills:
            content_len = len(skill.content)
            if total_chars + content_len > max_chars and result:
                break
            result.append(skill)
            total_chars += content_len

        return result

    @staticmethod
    def _resolve_agent_type_from_skills(skills: list[Skill]) -> str:
        """Determine agent type from the domains of matched skills."""
        if not skills:
            return "general"

        domains: set[str] = set()
        for skill in skills:
            domains.update(skill.domain)

        if "frontend" in domains and "backend" not in domains:
            return "frontend"
        if "backend" in domains and "frontend" not in domains:
            return "backend"
        if "devops" in domains:
            return "devops"

        return "general"
