"""Codebase Analysis workflow template.

Defines the template definition, parameter schema, and registration
function for the codebase-analysis workflow.
"""

from __future__ import annotations

import logging

from controller.workflows.prompts.domain_expert import PROMPT as DOMAIN_EXPERT_PROMPT
from controller.workflows.prompts.standards_discoverer import PROMPT as STANDARDS_DISCOVERER_PROMPT
from controller.workflows.prompts.work_item_planner import PROMPT as WORK_ITEM_PLANNER_PROMPT
from controller.workflows.prompts.synthesis_report import PROMPT as SYNTHESIS_REPORT_PROMPT

logger = logging.getLogger(__name__)

SLUG = "codebase-analysis"
NAME = "Codebase Analysis"
DESCRIPTION = (
    "Analyzes a target codebase in three phases (domain mapping, standards discovery, "
    "work item planning) plus a synthesis report. Produces four markdown artifacts: "
    "domain-map.md, standards-index.md, work-items-backlog.md, and analysis-summary.md."
)

PARAMETER_SCHEMA = {
    "type": "object",
    "required": ["repo_owner", "repo_name", "branch", "output_dir"],
    "properties": {
        "repo_owner": {
            "type": "string",
            "description": "GitHub organization or user",
        },
        "repo_name": {
            "type": "string",
            "description": "Repository name",
        },
        "branch": {
            "type": "string",
            "description": "Branch or ref to analyze (e.g. 'main')",
        },
        "output_dir": {
            "type": "string",
            "description": "Path where agents write markdown artifacts",
        },
    },
}

DEFINITION = {
    "steps": [
        {
            "id": "domain-expert",
            "type": "sequential",
            "agent": {
                "task_template": DOMAIN_EXPERT_PROMPT,
                "task_type": "analysis",
            },
        },
        {
            "id": "standards-discoverer",
            "type": "sequential",
            "depends_on": ["domain-expert"],
            "agent": {
                "task_template": STANDARDS_DISCOVERER_PROMPT,
                "task_type": "analysis",
            },
        },
        {
            "id": "work-item-planner",
            "type": "sequential",
            "depends_on": ["domain-expert", "standards-discoverer"],
            "agent": {
                "task_template": WORK_ITEM_PLANNER_PROMPT,
                "task_type": "analysis",
            },
        },
        {
            "id": "synthesis-report",
            "type": "sequential",
            "depends_on": ["domain-expert", "standards-discoverer", "work-item-planner"],
            "agent": {
                "task_template": SYNTHESIS_REPORT_PROMPT,
                "task_type": "analysis",
            },
        },
    ],
}


def get_definition() -> dict:
    """Return the template definition dict."""
    return DEFINITION


async def register(template_crud) -> None:
    """Register the codebase-analysis template if it doesn't exist.

    Called during controller startup. Idempotent -- skips if the slug
    already exists.
    """
    existing = await template_crud.get(SLUG)
    if existing is not None:
        logger.info("Workflow template '%s' already registered (v%d)", SLUG, existing.version)
        return

    from controller.workflows.models import WorkflowTemplateCreate

    payload = WorkflowTemplateCreate(
        slug=SLUG,
        name=NAME,
        description=DESCRIPTION,
        definition=DEFINITION,
        parameter_schema=PARAMETER_SCHEMA,
        created_by="system",
    )
    template = await template_crud.create(payload)
    logger.info("Registered workflow template '%s' (id=%s)", SLUG, template.id)
