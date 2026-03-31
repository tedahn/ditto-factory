"""Built-in toolkit onboarding workflow template.

This template defines a 2-step workflow:
1. An agent clones a GitHub repo, analyzes its structure using LLM reasoning,
   and produces a structured manifest JSON.
2. A transform step validates the manifest and imports it into the toolkit registry.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# The analysis prompt that teaches the agent how to analyze a repo
ANALYSIS_PROMPT = '''You are analyzing a GitHub repository to onboard it as a toolkit into the Ditto Factory platform.

## Your Task

1. Clone the repository: `git clone --depth 1 {{ github_url }}`
2. Read and understand the repository by examining:
   - README.md (what the project is, how it works)
   - Directory structure (`find . -type f | head -200`)
   - CLAUDE.md, .claude/ directory (if present)
   - package.json, pyproject.toml (if present)
   - Key source files in skills/, agents/, commands/, profiles/ directories
3. Classify the repository and identify its components
4. Output a structured JSON manifest

## Classification Guidelines

Classify the repo into ONE category:
- **agent_persona_library** -- Collection of agent personas/roles (e.g., frontend dev, code reviewer, DevOps engineer). Each persona defines behavior, expertise, and process.
- **development_methodology** -- Development workflow skills (TDD, debugging, planning, code review). Components are methodologies that guide agent behavior.
- **capability_extension** -- Tools that give agents new capabilities (search, API access, file operations). Usually MCP servers or CLI tools.
- **design_intelligence** -- UI/UX knowledge, design systems, style guides, component patterns.
- **persistent_memory** -- Systems for agent memory persistence across sessions (databases, graph stores, knowledge bases).
- **codebase_standards** -- Code standards, profiles, rules that get injected into agent context.
- **multi_agent_pipeline** -- Orchestrated multi-agent workflows where components work together in sequence.

## Component Identification

For each real, usable component identify:
- **name**: Human-readable name
- **type**: skill | agent | command | plugin | profile | tool
- **description**: What it does (1-2 sentences)
- **directory**: Path in the repo
- **primary_file**: The main entry point file
- **tags**: Relevant tags
- **risk_level**: safe (just markdown) | moderate (has scripts/hooks) | high (installs packages)
- **relationship_group**: If this component works with others as part of a pipeline/group, name the group

## What to EXCLUDE
- README files, LICENSE, CHANGELOG, CONTRIBUTING
- Test files, example files, documentation-only files
- Build scripts, CI configs, editor configs
- Internal implementation details that aren't user-facing

## Output Format

Output ONLY valid JSON (no markdown, no explanation), matching this schema:

```json
{
  "repo_name": "superpowers",
  "category": "development_methodology",
  "category_reason": "This is a development methodology framework providing structured workflows for TDD, debugging, planning, and code review",
  "description": "Agentic skills framework for structured development workflows",
  "version": "v5.0.6",
  "components": [
    {
      "name": "Test-Driven Development",
      "slug": "test-driven-development",
      "type": "skill",
      "description": "RED-GREEN-REFACTOR cycle with testing anti-patterns reference",
      "directory": "skills/test-driven-development",
      "primary_file": "skills/test-driven-development/SKILL.md",
      "tags": ["testing", "tdd", "methodology"],
      "risk_level": "safe",
      "relationship_group": "core-development",
      "files": [
        {"path": "skills/test-driven-development/SKILL.md", "filename": "SKILL.md", "is_primary": true},
        {"path": "skills/test-driven-development/testing-anti-patterns.md", "filename": "testing-anti-patterns.md", "is_primary": false}
      ]
    }
  ]
}
```
'''

# The workflow template definition
ONBOARDING_TEMPLATE = {
    "slug": "toolkit-onboarding",
    "name": "Toolkit Onboarding",
    "description": "Analyze a GitHub repository and import it as a toolkit using AI reasoning",
    "definition": {
        "steps": [
            {
                "id": "analyze",
                "type": "sequential",
                "agent": {
                    "task_template": ANALYSIS_PROMPT,
                    "task_type": "analysis",
                    "output_schema": {
                        "type": "object",
                        "required": ["repo_name", "category", "components"],
                        "properties": {
                            "repo_name": {"type": "string"},
                            "category": {"type": "string"},
                            "category_reason": {"type": "string"},
                            "description": {"type": "string"},
                            "version": {"type": "string"},
                            "components": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["name", "type", "directory"],
                                    "properties": {
                                        "name": {"type": "string"},
                                        "slug": {"type": "string"},
                                        "type": {"type": "string"},
                                        "description": {"type": "string"},
                                        "directory": {"type": "string"},
                                        "primary_file": {"type": "string"},
                                        "tags": {"type": "array", "items": {"type": "string"}},
                                        "risk_level": {"type": "string"},
                                        "relationship_group": {"type": "string"},
                                        "files": {"type": "array"},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        ],
    },
    "parameter_schema": {
        "type": "object",
        "required": ["github_url"],
        "properties": {
            "github_url": {"type": "string", "description": "GitHub repository URL"},
            "branch": {"type": "string", "default": "main", "description": "Branch to analyze"},
        },
    },
}


async def validate_and_import_manifest(
    manifest_json: dict,
    source_url: str,
    toolkit_registry,
    github_client=None,
) -> dict:
    """Validate an LLM-produced manifest and import into the toolkit registry.

    Parameters
    ----------
    manifest_json:
        The JSON manifest produced by the analysis agent step.
    source_url:
        The GitHub URL of the repository being onboarded.
    toolkit_registry:
        A ``ToolkitRegistry`` instance for persistence.
    github_client:
        Optional ``GitHubClient`` for fetching commit SHAs.

    Returns
    -------
    dict
        ``{"status": "imported", "toolkit_slug": "...", "component_count": N}``
    """
    from controller.toolkits.models import (
        ToolkitCategory,
        ComponentType,
        LoadStrategy,
        RiskLevel,
        DiscoveredComponent,
        DiscoveredFile,
        DiscoveryManifest,
    )
    from controller.toolkits.github_client import GitHubClient

    # Parse the URL
    parsed = GitHubClient.parse_github_url(source_url)

    # Map category string to enum
    category_map = {
        "agent_persona_library": ToolkitCategory.SKILL_COLLECTION,
        "development_methodology": ToolkitCategory.SKILL_COLLECTION,
        "capability_extension": ToolkitCategory.TOOL,
        "design_intelligence": ToolkitCategory.SKILL_COLLECTION,
        "persistent_memory": ToolkitCategory.PLUGIN,
        "codebase_standards": ToolkitCategory.PROFILE_PACK,
        "multi_agent_pipeline": ToolkitCategory.MIXED,
    }
    raw_category = manifest_json.get("category", "mixed")
    category = category_map.get(raw_category, ToolkitCategory.MIXED)

    # Map component type strings to enums
    type_map = {
        "skill": ComponentType.SKILL,
        "agent": ComponentType.AGENT,
        "command": ComponentType.COMMAND,
        "plugin": ComponentType.PLUGIN,
        "profile": ComponentType.PROFILE,
        "tool": ComponentType.TOOL,
    }

    risk_map = {
        "safe": RiskLevel.SAFE,
        "moderate": RiskLevel.MODERATE,
        "high": RiskLevel.HIGH,
    }

    # Build discovered components
    components = []
    for comp in manifest_json.get("components", []):
        files = []
        for f in comp.get("files", []):
            files.append(
                DiscoveredFile(
                    path=f.get("path", ""),
                    filename=f.get("filename", ""),
                    content="",  # Content fetched separately if needed
                    is_primary=f.get("is_primary", False),
                )
            )

        components.append(
            DiscoveredComponent(
                name=comp.get("name", ""),
                type=type_map.get(comp.get("type", "skill"), ComponentType.SKILL),
                directory=comp.get("directory", ""),
                primary_file=comp.get("primary_file", ""),
                load_strategy=LoadStrategy.MOUNT_FILE,
                description=comp.get("description", ""),
                tags=comp.get("tags", []),
                risk_level=risk_map.get(
                    comp.get("risk_level", "safe"), RiskLevel.SAFE
                ),
                files=files,
            )
        )

    # Build manifest
    manifest = DiscoveryManifest(
        source_url=source_url,
        owner=parsed["owner"],
        repo=parsed["repo"],
        branch=parsed.get("branch", "main"),
        commit_sha="",  # Will be populated during import
        repo_description=manifest_json.get("description", ""),
        category=category,
        discovered=components,
        source_version=manifest_json.get("version"),
    )

    # Create source if needed
    sources = await toolkit_registry.list_sources()
    source = None
    for s in sources:
        if s.github_url == source_url:
            source = s
            break

    if not source:
        # Get commit SHA from GitHub if client available
        commit_sha = ""
        if github_client:
            try:
                commit = await github_client.get_latest_commit(
                    parsed["owner"], parsed["repo"], parsed.get("branch", "main")
                )
                commit_sha = commit["sha"]
            except Exception:
                pass

        source = await toolkit_registry.create_source(
            github_url=source_url,
            owner=parsed["owner"],
            repo=parsed["repo"],
            branch=parsed.get("branch", "main"),
            commit_sha=commit_sha,
            metadata={
                "category": raw_category,
                "category_reason": manifest_json.get("category_reason", ""),
                "onboarded_by": "llm",
            },
        )

    manifest.commit_sha = source.last_commit_sha or ""

    # Import
    toolkit = await toolkit_registry.import_from_manifest(
        source_id=source.id,
        manifest=manifest,
    )

    return {
        "status": "imported",
        "toolkit_slug": toolkit.slug,
        "component_count": toolkit.component_count,
        "category": raw_category,
        "category_reason": manifest_json.get("category_reason", ""),
    }
