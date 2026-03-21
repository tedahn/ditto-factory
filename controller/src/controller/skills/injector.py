"""Format selected skills for injection into agent sessions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from controller.skills.models import Skill

logger = logging.getLogger(__name__)


class SkillInjector:
    """Formats skills for Redis storage and enforces character budgets."""

    def format_for_redis(self, skills: list[Skill]) -> list[dict]:
        """Return list of dicts suitable for JSON-encoding into Redis."""
        return [
            {"name": skill.slug, "content": skill.content}
            for skill in skills
        ]

    def validate_budget(
        self, skills: list[Skill], max_chars: int = 16000
    ) -> list[Skill]:
        """Drop skills that would exceed the character budget."""
        total = 0
        accepted: list[Skill] = []
        for skill in skills:
            if total + len(skill.content) > max_chars:
                logger.warning(
                    "Skill budget exceeded, dropping %s (%d chars)",
                    skill.slug,
                    len(skill.content),
                )
                continue
            total += len(skill.content)
            accepted.append(skill)
        return accepted
