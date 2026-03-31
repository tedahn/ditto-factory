# Codebase Analysis Workflow — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a ditto-factory workflow template that analyzes any target codebase and produces a domain map, standards index, work items backlog, and executive synthesis report.

**Architecture:** Four sequential agent pods orchestrated by the existing `WorkflowEngine`. Each pod clones the target repo, reads prior phase outputs from a shared output directory, and writes structured markdown artifacts. Step outputs in the DB contain metadata only (artifact paths + quality summaries). Quality gates are prompt-based — agents self-validate and exit non-zero on failure.

**Tech Stack:** Python 3.14, FastAPI, aiosqlite, existing ditto-factory workflow engine + compiler + template CRUD

**Spec:** `docs/superpowers/specs/2026-03-30-codebase-analysis-workflow-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `controller/src/controller/workflows/prompts/__init__.py` | Create | Package init |
| `controller/src/controller/workflows/prompts/domain_expert.py` | Create | Phase 1 agent prompt constant |
| `controller/src/controller/workflows/prompts/standards_discoverer.py` | Create | Phase 2 agent prompt constant |
| `controller/src/controller/workflows/prompts/work_item_planner.py` | Create | Phase 3 agent prompt constant |
| `controller/src/controller/workflows/prompts/synthesis_report.py` | Create | Phase 4 agent prompt constant |
| `controller/src/controller/workflows/templates/codebase_analysis.py` | Create | Template definition + `register()` + `get_definition()` |
| `controller/src/controller/workflows/templates/__init__.py` | Modify | Re-export `TemplateCRUD` (already exists as module, needs package conversion) |
| `controller/src/controller/main.py` | Modify | Seed codebase-analysis template on startup |
| `controller/tests/test_codebase_analysis_workflow.py` | Create | Tests for compilation, step ordering, parameter validation, quality metadata |

---

### Task 1: Create prompt package with Phase 1 — Domain Expert prompt

**Files:**
- Create: `controller/src/controller/workflows/prompts/__init__.py`
- Create: `controller/src/controller/workflows/prompts/domain_expert.py`

- [ ] **Step 1: Create the prompts package init**

Create `controller/src/controller/workflows/prompts/__init__.py`:

```python
"""Agent prompt constants for workflow templates."""
```

- [ ] **Step 2: Create the Domain Expert prompt**

Create `controller/src/controller/workflows/prompts/domain_expert.py`:

```python
"""Phase 1 — Domain Expert agent prompt.

Scans a target codebase and produces a domain expertise map
covering architecture, bounded contexts, tech stack, dependencies,
and risk areas.
"""

PROMPT = """\
# Domain Expert — Codebase Analysis Phase 1

You are a domain analysis agent. Your job is to analyze a codebase and produce \
a structured domain map.

## Setup

1. Clone the repository:
   ```
   git clone https://github.com/{{ repo_owner }}/{{ repo_name }}.git /workspace/repo
   cd /workspace/repo
   git checkout {{ branch }}
   ```
2. Create the output directory: `mkdir -p {{ output_dir }}`
3. Your output goes to: `{{ output_dir }}/domain-map.md`

## Tasks

Analyze the repository and produce the following sections:

### 1. Tech Stack
Identify all languages, frameworks, build tools, package managers, and runtime \
dependencies. Cite the files that establish each (e.g., `package.json`, `pyproject.toml`, \
`Dockerfile`).

### 2. Directory Structure
Map the top-level directory layout and describe the purpose of each directory. \
Go one level deeper for `src/` or equivalent.

### 3. Bounded Contexts
Identify groups of modules that serve a single business domain. For each context:
- **Name**: A descriptive name (e.g., "Authentication", "Billing", "Notification")
- **Root path**: The directory or set of files that form this context
- **Classification**: Core (competitive advantage), Supporting (necessary but not differentiating), or Generic (commodity/infrastructure)
- **Key files**: The 3-5 most important files with one-line descriptions

### 4. Dependency Graph
For each bounded context, list:
- **Internal dependencies**: Which other contexts it imports from or calls
- **External dependencies**: Third-party libraries it relies on
- **Direction**: Whether the dependency is inbound, outbound, or bidirectional
Format as a list of edges: `ContextA -> ContextB (via import in src/a/client.py:12)`

### 5. Risk Areas
Identify:
- Files over 500 lines (cite path and line count)
- Circular dependencies between contexts
- High-coupling modules (imported by 5+ other modules)
- Dead code indicators (unused exports, unreachable branches)
- Missing error handling in critical paths

## Output Format

Write your findings to `{{ output_dir }}/domain-map.md` as structured markdown with \
the five sections above. Every claim must cite a specific file path. Do not speculate — \
only report what you find in the code.

## Quality Gate

Before exiting, verify your output meets ALL of these criteria:
1. At least 1 bounded context identified
2. At least 10 unique file paths cited across the document
3. Tech Stack section is present and non-empty
4. Dependency Graph section has at least 1 edge

If any criterion fails, print the failing criteria to stderr and exit with code 1.

## After Writing

Print a JSON summary to stdout:
```json
{
  "artifact_path": "{{ output_dir }}/domain-map.md",
  "summary": "<one-sentence summary of findings>",
  "quality": {
    "bounded_contexts": <count>,
    "file_citations": <count>,
    "dependency_edges": <count>,
    "risk_areas": <count>,
    "passed": true
  }
}
```
"""
```

- [ ] **Step 3: Commit**

```bash
git add controller/src/controller/workflows/prompts/__init__.py \
      controller/src/controller/workflows/prompts/domain_expert.py
git commit -m "feat(workflows): add domain expert agent prompt for codebase analysis"
```

---

### Task 2: Create Phase 2 — Standards Discoverer prompt

**Files:**
- Create: `controller/src/controller/workflows/prompts/standards_discoverer.py`

- [ ] **Step 1: Create the Standards Discoverer prompt**

Create `controller/src/controller/workflows/prompts/standards_discoverer.py`:

```python
"""Phase 2 — Standards Discoverer agent prompt.

Reads the domain map from Phase 1 and extracts naming conventions,
architecture patterns, quality patterns, existing standards docs,
gaps, and anti-patterns from the codebase.
"""

PROMPT = """\
# Standards Discoverer — Codebase Analysis Phase 2

You are a standards discovery agent. You read a prior domain analysis and extract \
the conventions, patterns, and standards from the codebase.

## Setup

1. Clone the repository:
   ```
   git clone https://github.com/{{ repo_owner }}/{{ repo_name }}.git /workspace/repo
   cd /workspace/repo
   git checkout {{ branch }}
   ```
2. Create the output directory: `mkdir -p {{ output_dir }}`
3. Read the Phase 1 domain map: `{{ output_dir }}/domain-map.md`
4. Your output goes to: `{{ output_dir }}/standards-index.md`

## Tasks

### 1. Naming Conventions
For each category below, document the dominant pattern with 3+ examples:
- **Files**: kebab-case, snake_case, PascalCase, etc.
- **Functions/methods**: camelCase, snake_case, etc.
- **Variables**: naming patterns, prefixes (`_private`, `is_`, `has_`)
- **Classes/types**: PascalCase, suffixes (`Service`, `Controller`, `Model`)
- **Constants**: UPPER_SNAKE_CASE, etc.

For each, cite the files where you found the pattern.

### 2. Architecture Patterns
Identify and document:
- **Layering**: How the codebase organizes layers (routes → services → data, etc.)
- **Module organization**: Feature-based vs layer-based vs hybrid
- **Import patterns**: Relative vs absolute, barrel exports, dependency injection
- **Configuration**: How config is loaded, env vars, defaults

### 3. Quality Patterns
Document:
- **Test structure**: Where tests live, naming convention, test runner, coverage setup
- **Error handling**: Exception types, error propagation patterns, logging
- **Logging**: Library, log levels, structured vs unstructured
- **CI/CD**: Pipeline configuration, linting, formatting, pre-commit hooks

### 4. Existing Standards Documents
Audit these files if they exist (list as "not found" if absent):
- `CLAUDE.md` — AI agent instructions
- `.editorconfig` — editor settings
- `.eslintrc` / `ruff.toml` / equivalent linter configs
- `CONTRIBUTING.md` — contributor guidelines
- `.pre-commit-config.yaml` — pre-commit hooks
- `Makefile` / `justfile` — task runner

For each found document, summarize what it covers and what it misses.

### 5. Gaps
List patterns that exist in the code but are NOT documented in any standards file. \
These are candidates for CLAUDE.md additions. Format each as:
- **Pattern**: Description of the undocumented pattern
- **Evidence**: 3+ file paths demonstrating the pattern
- **Recommended rule**: A one-line rule suitable for CLAUDE.md

### 6. Anti-Patterns
List inconsistencies where the codebase violates its own dominant patterns:
- **Pattern violated**: What the convention is
- **Violation**: What deviates from it
- **Severity**: 1 (cosmetic) to 5 (architectural risk)
- **Files**: Specific paths where the violation occurs

### 7. Consistency Scores
For each convention category (naming, architecture, quality), assign a score:
- **Score**: 0.0 to 1.0 (what percentage of the codebase follows the pattern)
- **Sample size**: How many files/instances you checked
- **Methodology**: Brief description of how you measured

## Output Format

Write findings to `{{ output_dir }}/standards-index.md` as structured markdown with \
the seven sections above. Every claim must cite specific file paths.

## Quality Gate

Before exiting, verify:
1. At least 3 convention categories documented (naming, architecture, quality)
2. At least 15 unique file paths cited
3. Consistency scores present for at least 3 categories
4. Gaps section has at least 1 entry

If any criterion fails, print failing criteria to stderr and exit with code 1.

## After Writing

Print a JSON summary to stdout:
```json
{
  "artifact_path": "{{ output_dir }}/standards-index.md",
  "summary": "<one-sentence summary>",
  "quality": {
    "convention_categories": <count>,
    "file_citations": <count>,
    "gaps_found": <count>,
    "anti_patterns_found": <count>,
    "avg_consistency_score": <float>,
    "passed": true
  }
}
```
"""
```

- [ ] **Step 2: Commit**

```bash
git add controller/src/controller/workflows/prompts/standards_discoverer.py
git commit -m "feat(workflows): add standards discoverer agent prompt for codebase analysis"
```

---

### Task 3: Create Phase 3 — Work Item Planner prompt

**Files:**
- Create: `controller/src/controller/workflows/prompts/work_item_planner.py`

- [ ] **Step 1: Create the Work Item Planner prompt**

Create `controller/src/controller/workflows/prompts/work_item_planner.py`:

```python
"""Phase 3 — Work Item Planner agent prompt.

Reads the domain map and standards index, discovers actionable work items,
scores priority, and builds a Beads-style dependency graph with
hierarchical IDs.
"""

PROMPT = """\
# Work Item Planner — Codebase Analysis Phase 3

You are a work item planning agent. You read prior analysis outputs and produce \
a prioritized, dependency-aware backlog of work items.

## Setup

1. Clone the repository:
   ```
   git clone https://github.com/{{ repo_owner }}/{{ repo_name }}.git /workspace/repo
   cd /workspace/repo
   git checkout {{ branch }}
   ```
2. Create the output directory: `mkdir -p {{ output_dir }}`
3. Read Phase 1 output: `{{ output_dir }}/domain-map.md`
4. Read Phase 2 output: `{{ output_dir }}/standards-index.md`
5. Your output goes to: `{{ output_dir }}/work-items-backlog.md`

## Tasks

### 1. Discovery
Scan the codebase for actionable items in these categories:

**Bugs:**
- Error handling gaps (catch blocks that swallow errors, missing try/catch)
- TODO/FIXME/HACK/XXX comments
- Dead code (unreachable branches, unused imports, orphaned files)
- Type safety issues (any casts, missing null checks)

**Tech Debt:**
- Outdated dependencies (check lockfiles for major version gaps)
- Missing tests (modules with no corresponding test file)
- Inconsistent patterns flagged in Phase 2's anti-patterns section
- Large files (>500 LOC) identified in Phase 1's risk areas

**Enhancements:**
- Documentation gaps from Phase 2 (missing CLAUDE.md rules, etc.)
- Missing CI/CD steps (no lint, no type check, no coverage gate)
- Accessibility or security improvements
- Performance opportunities (N+1 queries, missing indexes, unbounded lists)

### 2. Scoring
For each item, assign three scores on a 1-5 scale:
- **Impact**: How much does fixing this improve the codebase? (1=cosmetic, 5=critical)
- **Risk**: How likely is this to cause a production issue if left unfixed? (1=unlikely, 5=imminent)
- **Effort**: How much work to fix? (1=trivial, 5=major refactor)
- **Priority**: Computed as (Impact × Risk) ÷ Effort, rounded to 1 decimal

### 3. Hierarchical IDs (Beads-style)
Assign IDs following this scheme:
- **Epic**: `CA-E01`, `CA-E02`, ... (group related items)
- **Task**: `CA-E01-T01`, `CA-E01-T02`, ...
- **Subtask**: `CA-E01-T01-S01`, `CA-E01-T01-S02`, ...

Group items into epics by theme (e.g., "Test Coverage", "Error Handling", \
"Documentation", "Dependency Updates").

### 4. Dependency Links
For each item, specify:
- `blocks: [list of IDs this item blocks]`
- `blocked_by: [list of IDs blocking this item]`
- `relates_to: [list of related IDs]`

Common dependencies:
- A linter config change blocks lint-fix items
- A dependency update may block security fixes
- Documentation items are usually independent (no blockers)

### 5. Ready List
Compute the "ready" list: items with no `blocked_by` entries (or all blockers \
are already resolved). Mark these with `status: ready` in the output.

### 6. Limit
Output the top 30 items by priority score. If you find more than 30, include \
a note at the bottom: "X additional items discovered but excluded. Re-run with \
a higher limit to see all."

## Output Format

Write to `{{ output_dir }}/work-items-backlog.md`:

```markdown
# Work Items Backlog

**Repository:** {{ repo_owner }}/{{ repo_name }}
**Branch:** {{ branch }}
**Generated:** <timestamp>
**Total items:** <count>
**Ready items:** <count>

## Ready List

Items with no open blockers, sorted by priority:

| ID | Title | Priority | Category |
|---|---|---|---|

## All Items

### CA-E01: <Epic Title>

#### CA-E01-T01: <Task Title>
- **Category:** bug | tech-debt | enhancement
- **Priority:** <score> (Impact: X, Risk: Y, Effort: Z)
- **File(s):** `path/to/file.py:42`
- **Description:** <what and why>
- **Acceptance criteria:**
  - [ ] <specific, testable criterion>
  - [ ] <another criterion>
- **Blocks:** CA-E01-T02
- **Blocked by:** (none)
- **Relates to:** CA-E02-T01
- **Status:** ready

...

## Dependency Graph

CA-E01-T01 -> CA-E01-T02
CA-E01-T03 -> CA-E02-T01
...
```

## Quality Gate

Before exiting, verify:
1. At least 5 work items produced
2. Every item has: ID, priority score, at least 1 file path, at least 1 acceptance criterion
3. At least 1 item has `status: ready`
4. No orphaned dependency references (all IDs in blocks/blocked_by exist in the backlog)

If any criterion fails, print failing criteria to stderr and exit with code 1.

## After Writing

Print a JSON summary to stdout:
```json
{
  "artifact_path": "{{ output_dir }}/work-items-backlog.md",
  "summary": "<one-sentence summary>",
  "quality": {
    "total_items": <count>,
    "ready_items": <count>,
    "epics": <count>,
    "categories": {"bug": <n>, "tech-debt": <n>, "enhancement": <n>},
    "avg_priority": <float>,
    "passed": true
  }
}
```
"""
```

- [ ] **Step 2: Commit**

```bash
git add controller/src/controller/workflows/prompts/work_item_planner.py
git commit -m "feat(workflows): add work item planner agent prompt for codebase analysis"
```

---

### Task 4: Create Phase 4 — Synthesis Report prompt

**Files:**
- Create: `controller/src/controller/workflows/prompts/synthesis_report.py`

- [ ] **Step 1: Create the Synthesis Report prompt**

Create `controller/src/controller/workflows/prompts/synthesis_report.py`:

```python
"""Phase 4 — Synthesis Report agent prompt.

Reads all three prior phase artifacts and produces an executive
summary with key findings, risk matrix, and recommended next actions.
"""

PROMPT = """\
# Synthesis Report — Codebase Analysis Phase 4

You are a synthesis agent. You read the outputs of three prior analysis phases \
and produce a concise executive summary.

## Setup

1. Create the output directory: `mkdir -p {{ output_dir }}`
2. Read Phase 1 output: `{{ output_dir }}/domain-map.md`
3. Read Phase 2 output: `{{ output_dir }}/standards-index.md`
4. Read Phase 3 output: `{{ output_dir }}/work-items-backlog.md`
5. Your output goes to: `{{ output_dir }}/analysis-summary.md`

## Output Format

Write to `{{ output_dir }}/analysis-summary.md`:

```markdown
# Codebase Analysis Summary

**Repository:** {{ repo_owner }}/{{ repo_name }}
**Branch:** {{ branch }}
**Generated:** <timestamp>

## Executive Summary

<One paragraph (3-5 sentences) summarizing the overall health, architecture, \
and key opportunities for the codebase. Be specific — cite numbers from the \
prior phases.>

## Key Findings

### Architecture (from Domain Map)

| # | Finding | Impact | Source |
|---|---|---|---|
| 1 | <finding> | <high/medium/low> | `<file path>` |
| ... | ... | ... | ... |

Top 5 findings from Phase 1.

### Standards (from Standards Index)

| # | Finding | Consistency | Source |
|---|---|---|---|
| 1 | <finding> | <score> | `<file path>` |
| ... | ... | ... | ... |

Top 5 findings from Phase 2.

### Work Items (from Backlog)

| # | ID | Title | Priority | Category |
|---|---|---|---|---|
| 1 | <id> | <title> | <score> | <category> |
| ... | ... | ... | ... | ... |

Top 5 highest-priority items from Phase 3.

## Risk Matrix

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| <risk> | <high/med/low> | <high/med/low> | <action> |

Synthesize risks from all three phases. Include 3-5 risks.

## Recommended Next Actions

1. **<Action title>** — <description> (addresses work item <ID>)
2. **<Action title>** — <description>
3. **<Action title>** — <description>
4. **<Action title>** — <description>
5. **<Action title>** — <description>

The top 5 ready work items from Phase 3, described as actionable next steps.

## Appendix: Phase Outputs

- Domain Map: `{{ output_dir }}/domain-map.md`
- Standards Index: `{{ output_dir }}/standards-index.md`
- Work Items Backlog: `{{ output_dir }}/work-items-backlog.md`
```

## Quality Gate

Before exiting, verify:
1. Executive Summary section is present and non-empty
2. At least 3 key findings listed per phase (9 total minimum)
3. At least 3 risks in the Risk Matrix
4. At least 3 recommended next actions

If any criterion fails, print failing criteria to stderr and exit with code 1.

## After Writing

Print a JSON summary to stdout:
```json
{
  "artifact_path": "{{ output_dir }}/analysis-summary.md",
  "summary": "<one-sentence summary>",
  "quality": {
    "findings_count": <count>,
    "risks_count": <count>,
    "actions_count": <count>,
    "passed": true
  }
}
```
"""
```

- [ ] **Step 2: Commit**

```bash
git add controller/src/controller/workflows/prompts/synthesis_report.py
git commit -m "feat(workflows): add synthesis report agent prompt for codebase analysis"
```

---

### Task 5: Create the workflow template definition + registration

**Files:**
- Create: `controller/src/controller/workflows/templates/codebase_analysis.py`
- Modify: `controller/src/controller/workflows/templates/__init__.py`

Currently `controller/src/controller/workflows/templates.py` is a module file containing `TemplateCRUD`. It needs to become a package (`templates/`) with `__init__.py` re-exporting `TemplateCRUD` and a new `codebase_analysis.py` module.

- [ ] **Step 1: Convert templates module to package**

Rename the existing file to become the package's `__init__.py`. Use `git mv` to preserve history:

```bash
cd /Users/tedahn/Documents/codebase/ditto-factory/controller/src/controller/workflows
mkdir -p templates_pkg
git mv templates.py templates_pkg/__init__.py
mv templates_pkg templates
```

Verify the existing import `from controller.workflows.templates import TemplateCRUD` still works — since `__init__.py` contains the `TemplateCRUD` class, all 7 import sites are unaffected. Run a quick grep to confirm:

```bash
grep -r "from controller.workflows.templates import" /Users/tedahn/Documents/codebase/ditto-factory/controller/ --include="*.py" | grep -v __pycache__
```

- [ ] **Step 2: Create the codebase analysis template module**

Create `controller/src/controller/workflows/templates/codebase_analysis.py`:

```python
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

    Called during controller startup. Idempotent — skips if the slug
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
```

- [ ] **Step 3: Commit**

```bash
git add controller/src/controller/workflows/templates/codebase_analysis.py
git commit -m "feat(workflows): add codebase-analysis template definition and registration"
```

---

### Task 6: Wire up template registration in main.py

**Files:**
- Modify: `controller/src/controller/main.py`

- [ ] **Step 1: Add template seeding after workflow engine init**

In `controller/src/controller/main.py`, find the block that ends with `logger.info("Workflow engine initialized")` (around line 270). After that line, add the template seeding:

```python
            # Seed built-in workflow templates
            try:
                from controller.workflows.templates.codebase_analysis import register as register_codebase_analysis
                await register_codebase_analysis(template_crud)
            except Exception:
                logger.exception("Failed to seed codebase-analysis workflow template")
```

This goes inside the `if settings.workflow_enabled:` block, after the `workflow_engine` and `template_crud` are initialized, but before the `except Exception` that catches workflow init failures.

- [ ] **Step 2: Verify the placement**

The modified section should look like:

```python
            workflow_engine = WorkflowEngine(
                db_path=wf_db_path,
                settings=settings,
                compiler=wf_compiler,
                spawner=spawner,
                redis_state=app.state.redis_state,
            )
            logger.info("Workflow engine initialized")

            # Seed built-in workflow templates
            try:
                from controller.workflows.templates.codebase_analysis import register as register_codebase_analysis
                await register_codebase_analysis(template_crud)
            except Exception:
                logger.exception("Failed to seed codebase-analysis workflow template")

        except Exception:
            logger.exception("Failed to initialize workflow engine")
```

- [ ] **Step 3: Commit**

```bash
git add controller/src/controller/main.py
git commit -m "feat(workflows): seed codebase-analysis template on startup"
```

---

### Task 7: Write tests for template compilation and step ordering

**Files:**
- Create: `controller/tests/test_codebase_analysis_workflow.py`

- [ ] **Step 1: Write the test file**

Create `controller/tests/test_codebase_analysis_workflow.py`:

```python
"""Tests for the codebase-analysis workflow template.

Verifies template compilation, step ordering, parameter validation,
and quality metadata schema.
"""

from __future__ import annotations

import pytest

from controller.workflows.compiler import CompilationError, WorkflowCompiler
from controller.workflows.models import StepType
from controller.workflows.templates.codebase_analysis import (
    DEFINITION,
    PARAMETER_SCHEMA,
    SLUG,
    get_definition,
)


@pytest.fixture
def compiler():
    return WorkflowCompiler(max_agents_per_execution=20)


@pytest.fixture
def valid_params():
    return {
        "repo_owner": "acme",
        "repo_name": "my-service",
        "branch": "main",
        "output_dir": "/workspace/output",
    }


class TestDefinitionStructure:
    """Verify the template definition is well-formed."""

    def test_definition_has_four_steps(self):
        defn = get_definition()
        assert len(defn["steps"]) == 4

    def test_step_ids(self):
        defn = get_definition()
        step_ids = [s["id"] for s in defn["steps"]]
        assert step_ids == [
            "domain-expert",
            "standards-discoverer",
            "work-item-planner",
            "synthesis-report",
        ]

    def test_step_types(self):
        defn = get_definition()
        types = [s["type"] for s in defn["steps"]]
        assert types == ["sequential", "sequential", "sequential", "sequential"]

    def test_dependency_chain(self):
        defn = get_definition()
        steps = {s["id"]: s for s in defn["steps"]}
        # domain-expert has no dependencies
        assert steps["domain-expert"].get("depends_on", []) == []
        # standards-discoverer depends on domain-expert
        assert steps["standards-discoverer"]["depends_on"] == ["domain-expert"]
        # work-item-planner depends on both
        assert set(steps["work-item-planner"]["depends_on"]) == {
            "domain-expert",
            "standards-discoverer",
        }
        # synthesis-report depends on all three prior steps
        assert set(steps["synthesis-report"]["depends_on"]) == {
            "domain-expert",
            "standards-discoverer",
            "work-item-planner",
        }

    def test_all_steps_have_agent_spec(self):
        defn = get_definition()
        for step in defn["steps"]:
            assert "agent" in step, f"Step {step['id']} missing agent spec"
            assert "task_template" in step["agent"]
            assert step["agent"]["task_type"] == "analysis"

    def test_slug_is_correct(self):
        assert SLUG == "codebase-analysis"


class TestCompilation:
    """Verify the template compiles correctly with the WorkflowCompiler."""

    def test_compiles_to_four_steps(self, compiler, valid_params):
        steps = compiler.compile(
            DEFINITION, valid_params, PARAMETER_SCHEMA
        )
        assert len(steps) == 4

    def test_compiled_step_types(self, compiler, valid_params):
        steps = compiler.compile(
            DEFINITION, valid_params, PARAMETER_SCHEMA
        )
        types = [s.step_type for s in steps]
        assert types == [
            StepType.SEQUENTIAL,
            StepType.SEQUENTIAL,
            StepType.SEQUENTIAL,
            StepType.SEQUENTIAL,
        ]

    def test_compiled_step_ids_match(self, compiler, valid_params):
        steps = compiler.compile(
            DEFINITION, valid_params, PARAMETER_SCHEMA
        )
        step_ids = [s.step_id for s in steps]
        assert step_ids == [
            "domain-expert",
            "standards-discoverer",
            "work-item-planner",
            "synthesis-report",
        ]

    def test_parameters_interpolated_in_task(self, compiler, valid_params):
        steps = compiler.compile(
            DEFINITION, valid_params, PARAMETER_SCHEMA
        )
        # The domain-expert step task should have the repo info interpolated
        task = steps[0].input["task"]
        assert "acme" in task
        assert "my-service" in task
        assert "main" in task
        assert "/workspace/output" in task

    def test_agent_count_within_limit(self, compiler, valid_params):
        # 3 sequential + 1 report = 4 agents, well under 20 limit
        steps = compiler.compile(
            DEFINITION, valid_params, PARAMETER_SCHEMA
        )
        assert len(steps) <= 20


class TestParameterValidation:
    """Verify parameter schema enforcement."""

    def test_missing_required_repo_owner(self, compiler):
        params = {"repo_name": "x", "output_dir": "/out"}
        with pytest.raises(CompilationError, match="repo_owner"):
            compiler.compile(DEFINITION, params, PARAMETER_SCHEMA)

    def test_missing_required_repo_name(self, compiler):
        params = {"repo_owner": "x", "output_dir": "/out"}
        with pytest.raises(CompilationError, match="repo_name"):
            compiler.compile(DEFINITION, params, PARAMETER_SCHEMA)

    def test_missing_required_output_dir(self, compiler):
        params = {"repo_owner": "x", "repo_name": "y", "branch": "main"}
        with pytest.raises(CompilationError, match="output_dir"):
            compiler.compile(DEFINITION, params, PARAMETER_SCHEMA)

    def test_missing_required_branch(self, compiler):
        params = {"repo_owner": "x", "repo_name": "y", "output_dir": "/out"}
        with pytest.raises(CompilationError, match="branch"):
            compiler.compile(DEFINITION, params, PARAMETER_SCHEMA)

    def test_wrong_type_repo_owner(self, compiler):
        params = {"repo_owner": 123, "repo_name": "x", "branch": "main", "output_dir": "/out"}
        with pytest.raises(CompilationError, match="repo_owner"):
            compiler.compile(DEFINITION, params, PARAMETER_SCHEMA)


class TestPromptContent:
    """Verify prompt templates contain required sections."""

    def test_domain_expert_prompt_has_sections(self):
        from controller.workflows.prompts.domain_expert import PROMPT
        assert "## Setup" in PROMPT
        assert "## Tasks" in PROMPT
        assert "## Quality Gate" in PROMPT
        assert "Bounded Contexts" in PROMPT
        assert "Dependency Graph" in PROMPT
        assert "Risk Areas" in PROMPT

    def test_standards_discoverer_prompt_has_sections(self):
        from controller.workflows.prompts.standards_discoverer import PROMPT
        assert "## Setup" in PROMPT
        assert "Naming Conventions" in PROMPT
        assert "Architecture Patterns" in PROMPT
        assert "Anti-Patterns" in PROMPT
        assert "Consistency Scores" in PROMPT
        assert "domain-map.md" in PROMPT

    def test_work_item_planner_prompt_has_sections(self):
        from controller.workflows.prompts.work_item_planner import PROMPT
        assert "## Setup" in PROMPT
        assert "CA-E01" in PROMPT
        assert "blocks" in PROMPT
        assert "blocked_by" in PROMPT
        assert "Ready List" in PROMPT
        assert "domain-map.md" in PROMPT
        assert "standards-index.md" in PROMPT

    def test_synthesis_report_prompt_has_sections(self):
        from controller.workflows.prompts.synthesis_report import PROMPT
        assert "Executive Summary" in PROMPT
        assert "Risk Matrix" in PROMPT
        assert "Recommended Next Actions" in PROMPT
        assert "Quality Gate" in PROMPT
        assert "domain-map.md" in PROMPT
        assert "standards-index.md" in PROMPT
        assert "work-items-backlog.md" in PROMPT

    def test_all_prompts_have_template_variables(self):
        from controller.workflows.prompts.domain_expert import PROMPT as p1
        from controller.workflows.prompts.standards_discoverer import PROMPT as p2
        from controller.workflows.prompts.work_item_planner import PROMPT as p3
        from controller.workflows.prompts.synthesis_report import PROMPT as p4

        for prompt in [p1, p2, p3]:
            assert "{{ repo_owner }}" in prompt
            assert "{{ repo_name }}" in prompt
            assert "{{ branch }}" in prompt
            assert "{{ output_dir }}" in prompt

        # Synthesis report uses repo_owner/repo_name/branch in output template
        # and output_dir for file paths
        assert "{{ output_dir }}" in p4
        assert "{{ repo_owner }}" in p4
```

- [ ] **Step 2: Run tests to verify they pass**

Run:
```bash
cd /Users/tedahn/Documents/codebase/ditto-factory/controller
python -m pytest tests/test_codebase_analysis_workflow.py -v
```

Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add controller/tests/test_codebase_analysis_workflow.py
git commit -m "test(workflows): add tests for codebase-analysis template compilation and prompts"
```

---

## Summary

| Task | What | Files |
|---|---|---|
| 1 | Domain Expert prompt | `prompts/__init__.py`, `prompts/domain_expert.py` |
| 2 | Standards Discoverer prompt | `prompts/standards_discoverer.py` |
| 3 | Work Item Planner prompt | `prompts/work_item_planner.py` |
| 4 | Synthesis Report prompt (with quality gate) | `prompts/synthesis_report.py` |
| 5 | Template definition + registration | `templates/codebase_analysis.py`, `templates/__init__.py` |
| 6 | Wire up in main.py | `main.py` |
| 7 | Tests | `tests/test_codebase_analysis_workflow.py` |
