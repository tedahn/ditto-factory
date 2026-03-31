# Phase 2: Agent-Driven Toolkit Onboarding via Workflow Engine

**Goal:** Toolkit onboarding runs as a ditto-factory workflow — dogfooding the platform. An agent clones the repo, analyzes it with LLM reasoning, and produces a structured manifest that the controller imports.

## Architecture

```
User clicks "Import from GitHub" in dashboard
  → Controller creates a workflow execution using "toolkit-onboarding" template
  → Workflow engine spawns agent pod
  → Agent pod:
      1. Clones the repo (git clone)
      2. Reads README, configs, directory structure
      3. Uses LLM reasoning to classify and map components
      4. Writes structured manifest JSON to a result artifact
  → Controller receives structured output via handle_agent_result
  → Controller parses manifest → imports into toolkit registry
  → User reviews imported toolkit in dashboard
```

## Task Breakdown

### Task 1: Add STRUCTURED_OUTPUT result type

**Files to modify:**
- `controller/src/controller/models.py` — Add `STRUCTURED_OUTPUT = "structured_output"` to `ResultType` enum
- `controller/src/controller/state/sqlite.py` — No change needed (stores as text already)
- `controller/src/controller/workflows/engine.py` — In `handle_agent_result`, when result_type is STRUCTURED_OUTPUT, parse the JSON output and store in step.output

### Task 2: Create the toolkit-onboarding workflow template

**What:** A built-in workflow template definition that ships with ditto-factory. Not stored in DB — compiled at startup.

**File to create:** `controller/src/controller/toolkits/onboarding_template.py`

The template has 3 steps:
1. **clone-and-analyze** (agent step) — Agent clones repo, reads files, produces structured manifest JSON
2. **validate-manifest** (transform step) — Validate the manifest JSON schema
3. **import-toolkit** (sequential step) — Controller imports the manifest into the registry

```python
ONBOARDING_TEMPLATE = {
    "slug": "toolkit-onboarding",
    "name": "Toolkit Onboarding",
    "description": "Analyze a GitHub repo and import as a toolkit",
    "definition": {
        "steps": [
            {
                "id": "analyze",
                "type": "sequential",
                "agent": {
                    "task_template": "Clone and analyze the GitHub repository at {{ github_url }} (branch: {{ branch }}). Read the README, directory structure, config files, and key source files. Produce a structured JSON manifest classifying the repository and its components.\n\n{{ analysis_skill }}",
                    "task_type": "analysis",
                    "skills": ["toolkit-analysis"],
                    "output_schema": {
                        "type": "object",
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
                                    "properties": {
                                        "name": {"type": "string"},
                                        "slug": {"type": "string"},
                                        "type": {"type": "string"},
                                        "description": {"type": "string"},
                                        "directory": {"type": "string"},
                                        "primary_file": {"type": "string"},
                                        "tags": {"type": "array"},
                                        "risk_level": {"type": "string"},
                                        "relationship_group": {"type": "string"},
                                        "files": {"type": "array"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        ]
    },
    "parameter_schema": {
        "type": "object",
        "properties": {
            "github_url": {"type": "string"},
            "branch": {"type": "string", "default": "main"}
        },
        "required": ["github_url"]
    }
}
```

### Task 3: Create the toolkit-analysis skill

**What:** A skill that teaches the agent HOW to analyze a repo. Gets mounted into the agent's loadout.

**File to create:** `controller/src/controller/toolkits/skills/toolkit-analysis.md`

This skill contains:
- Instructions for cloning and reading the repo
- How to classify repos (with examples of correct classifications)
- How to identify real components vs internal files
- How to detect relationships between components
- The exact JSON schema to output
- Examples of correctly analyzed repos (superpowers, agency-agents, beads)

### Task 4: Onboarding Workflow Handler

**What:** A handler that processes the workflow result — takes the agent's structured manifest output and imports it into the toolkit registry.

**File to create:** `controller/src/controller/toolkits/onboarding_handler.py`

```python
class OnboardingHandler:
    """Handles completed toolkit-onboarding workflow executions."""
    
    async def handle_result(self, execution_id: str, step_output: dict) -> Toolkit:
        """Parse agent's manifest output and import into toolkit registry.
        
        1. Validate the manifest JSON structure
        2. Convert to DiscoveryManifest model
        3. Create source record
        4. Call registry.import_from_manifest
        5. Return the imported toolkit
        """
```

Wire this into the workflow engine's `handle_agent_result` — when a `toolkit-onboarding` workflow step completes, call the handler.

### Task 5: Register built-in template + wire into API

**Files to modify:**
- `controller/src/controller/main.py` — Register the toolkit-onboarding template in the DB on startup (idempotent)
- `controller/src/controller/toolkits/api.py` — Update `POST /discover` endpoint:
  - Instead of running pattern-matching discovery, start a toolkit-onboarding workflow execution
  - Return execution_id so the frontend can poll for results
  - Add `GET /discover/{execution_id}` to check status and get results

**New flow:**
```
POST /discover {github_url} → starts workflow → returns {execution_id, status: "running"}
GET /discover/{execution_id} → returns {status: "running|completed|failed", manifest?: {...}}
```

The frontend polls the status endpoint and shows progress.

### Task 6: Update Frontend for Async Discovery

**Files to modify:**
- `web/src/components/toolkits/import-url-input.tsx` — After clicking "Discover", show workflow execution progress instead of waiting for sync response
- `web/src/components/toolkits/discovery-results.tsx` — Show LLM-analyzed manifest with:
  - Category with AI reasoning ("This is an agent persona library because...")
  - Component relationship groups
  - Confidence indicators
- `web/src/lib/hooks.ts` — Add `useDiscoveryStatus(executionId)` polling hook

### Task 7: Install git in controller container

**File to modify:** `images/controller/Dockerfile` — Add `git` to the container so the agent can clone repos.

Actually — the agent pod clones the repo, not the controller. The agent image already has git. The controller just needs to start the workflow.

## Dependencies

- `anthropic` Python package in the AGENT image (already there — agents run Claude Code)
- The agent image has git (already there)
- The workflow engine needs to be enabled: `DF_WORKFLOW_ENABLED=true` (already set)

## Execution Order

```
Task 1 → STRUCTURED_OUTPUT result type (small, foundational)
Task 2 → Onboarding workflow template definition
Task 3 → Toolkit analysis skill (the prompt engineering)
Task 4 → Onboarding handler (manifest → registry import)
Task 5 → Wire into API + register template on startup
Task 6 → Frontend async discovery UX
Task 7 → Container deps (verify, likely no-op)
```

Tasks 1-4 are backend core. Task 5 integrates. Task 6 is frontend. Task 7 is ops.
