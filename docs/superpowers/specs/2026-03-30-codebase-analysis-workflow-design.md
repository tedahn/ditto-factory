# Codebase Analysis Workflow — Design Spec

**Date:** 2026-03-30
**Status:** Approved
**Slug:** `codebase-analysis`

## Overview

A ditto-factory workflow template that analyzes any target codebase and produces four markdown artifacts: a domain expertise map, a standards/conventions index, a prioritized work items backlog, and an executive synthesis report.

The workflow runs as 4 sequential agent pods orchestrated by the existing `WorkflowEngine`. Each pod clones the target repo, reads prior phase outputs from a shared output directory, and writes its artifact as structured markdown with file path citations.

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Inter-phase data passing | Filesystem artifacts + step metadata (Option B) | Keeps DB records lean; matches requirement for configurable output directory; large markdown outputs don't belong in SQLite |
| Target codebase input | Git clone URL + ref (Option A) | Consistent with existing `repo_owner`/`repo_name` pattern in `TaskRequest`; K8s-native (no host mounts) |
| Quality gate behavior | Fail fast (Option A) | Simplest; matches existing engine behavior; avoids wasting compute on downstream phases with bad inputs |
| Intent classification | API-only (Option A) | Infrastructure workflow, not conversational; avoids false positives in keyword matcher |

## Conceptual References

Patterns adopted from three reference repositories (not dependencies — ideas only):

- **steveyegge/beads**: Hierarchical task IDs (`CA-E01-T01-S01`), dependency graph with `blocks`/`blocked_by`/`relates_to` links, `ready` detection (tasks with no open blockers), compaction patterns for the work items backlog
- **buildermethods/agent-os**: Discover Standards (extract patterns from code), Index Standards (organize and score), Deploy Standards (recommend CLAUDE.md injections) for the standards discovery phase
- **shanraisshan/claude-code-best-practice**: Sequential phase orchestration with quality gates, single-purpose agent pods, evidence-based outputs with file path citations

## Parameters

```json
{
  "type": "object",
  "required": ["repo_owner", "repo_name", "branch", "output_dir"],
  "properties": {
    "repo_owner": {
      "type": "string",
      "description": "GitHub organization or user"
    },
    "repo_name": {
      "type": "string",
      "description": "Repository name"
    },
    "branch": {
      "type": "string",
      "description": "Branch or ref to analyze (e.g. 'main')"
    },
    "output_dir": {
      "type": "string",
      "description": "Path where agents write markdown artifacts"
    }
  }
}
```

## Workflow Steps

```
domain-expert ──┬──> standards-discoverer ──┬──> work-item-planner ──> synthesis-report
                └───────────────────────────┘         │                       ▲
                                                      └───────────────────────┘
```

| Step ID | Type | Depends On | Output Artifact |
|---|---|---|---|
| `domain-expert` | sequential | — | `domain-map.md` |
| `standards-discoverer` | sequential | `domain-expert` | `standards-index.md` |
| `work-item-planner` | sequential | `domain-expert`, `standards-discoverer` | `work-items-backlog.md` |
| `synthesis-report` | sequential | `domain-expert`, `standards-discoverer`, `work-item-planner` | `analysis-summary.md` |

> **Note:** `synthesis-report` uses `sequential` type (not `report`) because the workflow compiler's `_compile_report` does not interpolate agent prompts. All four steps are sequential agent pods.

Each step's output (stored in DB) is metadata only:
```json
{
  "artifact_path": "/path/to/output/domain-map.md",
  "summary": "Identified 5 bounded contexts across 3 languages...",
  "quality": {
    "file_citations": 47,
    "bounded_contexts": 5,
    "passed": true
  }
}
```

## Phase 1 — Domain Expert

**Input:** Clone `repo_owner/repo_name` at `branch`.

**Tasks:**
1. Map directory structure and tech stack (languages, frameworks, build tools)
2. Identify bounded contexts (groups of modules serving a single domain)
3. Classify each context as Core / Supporting / Generic (DDD classification)
4. Trace dependency graph between contexts (imports, API calls, shared types)
5. Identify risk areas (high coupling, circular dependencies, large files >500 LOC)

**Output:** `{{ output_dir }}/domain-map.md`

**Self-validation (quality gate):**
- At least 1 bounded context identified
- At least 10 file paths cited
- Tech stack section present
- Exit non-zero if any check fails

## Phase 2 — Standards Discoverer

**Input:** `{{ output_dir }}/domain-map.md` + cloned repo.

**Tasks:**
1. Extract naming conventions (files, functions, variables, classes) with examples
2. Identify architecture patterns (layering, module organization, routing)
3. Catalog quality patterns (test structure, error handling, logging, CI config)
4. Audit existing standards docs (CLAUDE.md, .editorconfig, linter configs, CONTRIBUTING.md)
5. Identify gaps — patterns in code but not documented
6. Flag anti-patterns with scored severity (1-5)
7. Recommend CLAUDE.md additions for discovered standards

**Output:** `{{ output_dir }}/standards-index.md`

**Self-validation (quality gate):**
- At least 3 convention categories documented
- At least 15 file paths cited
- Consistency scores present per category
- Exit non-zero if any check fails

## Phase 3 — Work Item Planner

**Input:** `{{ output_dir }}/domain-map.md` + `{{ output_dir }}/standards-index.md` + cloned repo.

**Tasks:**
1. Scan for bugs (error handling gaps, TODOs, FIXMEs, dead code)
2. Identify tech debt (outdated deps, missing tests, inconsistent patterns)
3. Discover enhancements (gaps from Phase 2, missing docs, missing CI)
4. Score each item: Priority = (Impact x Risk) / Effort, each on 1-5 scale
5. Assign hierarchical Beads-style IDs: `CA-E01` (epic), `CA-E01-T01` (task), `CA-E01-T01-S01` (subtask)
6. Build dependency links: `blocks`, `blocked_by`, `relates_to`
7. Compute `ready` list (items with no open blockers)
8. Limit to top 30 items

**Output:** `{{ output_dir }}/work-items-backlog.md`

**Self-validation (quality gate):**
- At least 5 work items produced
- Each item has: ID, priority score, file path citation, acceptance criteria
- At least 1 `ready` item in the list
- Exit non-zero if any check fails

## Phase 4 — Synthesis Report

**Input:** All 3 prior artifacts.

**Tasks:**
1. Executive summary (1 paragraph)
2. Key findings table (top 5 from each phase)
3. Risk matrix
4. Recommended next actions (top 5 ready work items)

**Output:** `{{ output_dir }}/analysis-summary.md`

**Self-validation (quality gate):**
- Executive Summary section present and non-empty
- At least 3 key findings per phase
- At least 3 risks in the risk matrix
- At least 3 recommended next actions
- Exit non-zero if any check fails

> **Note:** All prompts include `mkdir -p {{ output_dir }}` in their setup step to ensure the output directory exists.

## Implementation Components

### New files

| File | Purpose |
|---|---|
| `controller/src/controller/workflows/templates/__init__.py` | Package init |
| `controller/src/controller/workflows/templates/codebase_analysis.py` | Template definition dict + parameter schema + `register()` function |
| `controller/src/controller/workflows/prompts/__init__.py` | Package init |
| `controller/src/controller/workflows/prompts/domain_expert.py` | Phase 1 agent prompt constant |
| `controller/src/controller/workflows/prompts/standards_discoverer.py` | Phase 2 agent prompt constant |
| `controller/src/controller/workflows/prompts/work_item_planner.py` | Phase 3 agent prompt constant |
| `controller/src/controller/workflows/prompts/synthesis_report.py` | Phase 4 report prompt constant |
| `controller/tests/test_codebase_analysis_workflow.py` | Tests for template compilation, step ordering, parameter validation |

### Modified files

| File | Change |
|---|---|
| `controller/src/controller/main.py` | Call `codebase_analysis.register()` on startup to seed the template if not already present |

### Not modified

- `intent.py` — no intent classification rules
- `quality.py` — quality gates are prompt-based, not the deterministic checker
- `swarm/` — sequential pipeline uses engine step chaining, not swarm messaging

## Data Flow

```
POST /api/v1/workflows/executions
  { template_slug: "codebase-analysis",
    parameters: { repo_owner, repo_name, branch, output_dir } }

  -> WorkflowEngine.start()
     -> WorkflowCompiler.compile() -> 4 WorkflowSteps
     -> Step 1 (domain-expert): spawn pod, clone repo, write domain-map.md
        -> step output: { artifact_path, summary, quality }
     -> Step 2 (standards-discoverer): spawn pod, read domain-map.md, write standards-index.md
        -> step output: { artifact_path, summary, quality }
     -> Step 3 (work-item-planner): spawn pod, read both, write work-items-backlog.md
        -> step output: { artifact_path, summary, quality }
     -> Step 4 (synthesis-report): spawn pod, read all 3, write analysis-summary.md
        -> step output: { artifact_path, summary, quality }
  -> Execution status: completed
     -> result: step 4 output
```

## API Usage

```bash
# Start analysis
POST /api/v1/workflows/executions
{
  "template_slug": "codebase-analysis",
  "parameters": {
    "repo_owner": "acme",
    "repo_name": "my-service",
    "branch": "main",
    "output_dir": "/workspace/analysis-output"
  }
}

# Check progress
GET /api/v1/workflows/executions/{id}

# Returns execution with step statuses
```
