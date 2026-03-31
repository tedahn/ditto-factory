"""Resolve agent type/image from skill requirements."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from controller.skills.models import Skill

from controller.skills.models import AgentType, ResolvedAgent

logger = logging.getLogger(__name__)


class AgentTypeResolver:
    """Picks the best agent image based on the required capabilities of selected skills."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def resolve(self, skills: list[Skill], default_image: str) -> ResolvedAgent:
        required_caps: set[str] = set()
        for skill in skills:
            required_caps.update(skill.requires or [])

        if not required_caps:
            return ResolvedAgent(
                image=default_image,
                agent_type="general",
                diagnostics={
                    "required_capabilities": [],
                    "candidates_considered": [],
                    "selected": "general",
                    "reason": "default_fallback",
                },
            )

        best, candidates = await self._find_best_match(required_caps)
        if best is None:
            logger.warning(
                "No agent type covers requirements %s, using default", required_caps
            )
            return ResolvedAgent(
                image=default_image,
                agent_type="general",
                diagnostics={
                    "required_capabilities": sorted(required_caps),
                    "candidates_considered": candidates,
                    "selected": "general",
                    "reason": "default_fallback",
                },
            )

        return ResolvedAgent(
            image=best.image,
            agent_type=best.name,
            diagnostics={
                "required_capabilities": sorted(required_caps),
                "candidates_considered": candidates,
                "selected": best.name,
                "reason": "best_match",
            },
        )

    async def _find_best_match(
        self, required_caps: set[str]
    ) -> tuple[AgentType | None, list[dict]]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM agent_types") as cur:
                rows = await cur.fetchall()

        best: AgentType | None = None
        best_extra = float("inf")
        candidates: list[dict] = []

        for row in rows:
            caps = set(json.loads(row["capabilities"] or "[]"))
            covers = required_caps.issubset(caps)
            coverage = len(required_caps & caps)
            candidates.append({
                "name": row["name"],
                "capabilities": list(caps),
                "coverage": coverage,
                "covers_all": covers,
            })
            if covers:
                extra = len(caps - required_caps)
                if extra < best_extra:
                    best = AgentType(
                        id=row["id"],
                        name=row["name"],
                        image=row["image"],
                        description=row["description"],
                        capabilities=list(caps),
                        resource_profile=json.loads(
                            row["resource_profile"] or "{}"
                        ),
                        is_default=bool(row["is_default"]),
                    )
                    best_extra = extra

        return best, candidates
