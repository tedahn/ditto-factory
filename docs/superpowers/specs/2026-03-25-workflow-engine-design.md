# Two-State Workflow Engine -- Design Specification

**Date:** 2026-03-25
**Status:** Proposed
**Author:** Software Architect Agent

---

## 1. Executive Summary

**What:** A two-state workflow engine that replaces single-agent dispatch with deterministic, multi-step, fan-out-capable execution plans. Agents remain stateless workers; all orchestration logic lives in the engine.

**Why:** The current architecture handles one task = one agent. For multi-source data gathering (e.g., "find events in 5 cities across 3 platforms"), we need to fan out 15 agents, merge results, deduplicate, and deliver -- all deterministically. Individual agents cannot coordinate this; the engine must.

**Key decision:** Orchestration is deterministic code (Python, no LLM). Agent reasoning is LLM-powered but scoped to a single task. These two states never mix.

**Backwards compatible:** The existing single-agent path is preserved as the `single-task` workflow template and remains the default when no template matches.

---

## 2. Architecture

### 2.1 System Overview

```
                                    TWO STATES
                     +-----------------------------------------+
                     |                                         |
    STATE 1: DETERMINISTIC              STATE 2: AGENT REASONING
    (Python, no LLM)                    (Claude Code, LLM)
    +---------------------+            +---------------------+
    |                     |            |                     |
    | Webhook             |            | Agent Pod           |
    |   |                 |            |   - Receives task   |
    |   v                 |            |   - Has skills      |
    | Intent Classifier   |            |   - Has output      |
    | (async worker)      |            |     schema          |
    |   |                 |            |   - Does the work   |
    |   v                 |            |   - Returns result  |
    | Workflow Compiler   |            |   - No workflow     |
    |   |                 |            |     awareness       |
    |   v                 |            |                     |
    | Workflow Engine     |  spawns    |                     |
    |   |  |  |  |   ----+----------->|  N agent pods       |
    |   |  (fan-out)      |            |  (parallel)         |
    |   |                 |  results   |                     |
    |   |<----------------+-----------+|                     |
    |   v                 |            +---------------------+
    | Aggregate + Merge   |
    |   |                 |
    |   v                 |
    | Transform           |
    | (dedup, filter,     |
    |  sort)              |
    |   |                 |
    |   v                 |
    | Deliver             |
    | (Slack, Linear,     |
    |  GitHub)            |
    +---------------------+
```

### 2.2 Request Flow (Detailed)

```
Webhook (Slack/GitHub/Linear)
    |
    v
Controller API (fast ack, <200ms)
    |
    +---> Redis Stream: "intent_classify"
              |
              v
    Intent Classification Worker (async)
        - LLM call: "which template + what parameters?"
        - Fallback: rule-based matching
        - Result -> Redis: "intent_result:{thread_id}"
              |
              v
    Orchestrator picks up intent result
        |
        +---> Template matched?
        |         |
        |    Yes: WorkflowEngine.start(template_slug, params, thread_id)
        |         |
        |         v
        |    Compiler: template + params -> execution plan
        |         |
        |         v
        |    Engine: execute steps per DAG order
        |         |
        |         +---> Sequential step: spawn 1 agent, wait
        |         +---> Fan-out step: spawn N agents, wait all
        |         +---> Aggregate step: merge results (no agent)
        |         +---> Transform step: dedup/filter/sort (no agent)
        |         +---> Conditional step: branch on expression
        |         +---> Report step: deliver via integration
        |
        +---> No template matched:
              Current single-agent path (backwards compatible)
```

### 2.3 Integration with Existing Orchestrator

The workflow engine extends the current `Orchestrator` class, not replaces it. Two integration points:

**Entry point -- `Orchestrator.handle_task()`:**
```python
# After lock acquisition, before _spawn_job:
if self._settings.workflow_engine_enabled:
    template = await self._workflow_engine.match_template(task_request)
    if template:
        await self._workflow_engine.start(
            template_slug=template.slug,
            parameters=template.extracted_params,
            thread_id=thread_id,
            task_request=task_request,
        )
        return  # workflow engine takes over

# Fall through to existing single-agent path
await self._spawn_job(thread, task_request)
```

**Completion point -- `Orchestrator.handle_job_completion()`:**
```python
# After getting result from monitor:
active_job = await self._state.get_active_job_for_thread(thread_id)
if active_job and active_job.workflow_execution_id:
    # This agent is part of a workflow
    await self._workflow_engine.handle_agent_result(
        execution_id=active_job.workflow_execution_id,
        step_id=active_job.workflow_step_id,
        result=result,
    )
    return  # workflow engine decides what's next

# Fall through to existing safety pipeline
pipeline = SafetyPipeline(...)
await pipeline.process(thread, result)
```

---

## 3. Data Model

All tables live in the same Postgres instance (per Q2 expert recommendation). SQLite compatibility preserved via the existing `StateBackend` protocol pattern.

**SQLite compatibility notes:**
- `JSONB` columns: use `TEXT` (store JSON as strings, parse in application code)
- `TIMESTAMPTZ`: use `TEXT` with ISO 8601 format (`datetime('now')` instead of `now()`)
- `TEXT[]` arrays: use `TEXT` with JSON-encoded arrays (e.g., `'["step1","step2"]'`)
- Partial indexes (`WHERE` clause on `CREATE INDEX`): supported since SQLite 3.8.0
- `SELECT ... FOR UPDATE`: not supported; use `BEGIN EXCLUSIVE` transaction instead
- `UNIQUE` constraints: identical syntax, fully supported

### 3.1 Schema

```sql
-- ============================================================
-- Workflow Templates: versioned, CRUD-managed definitions
-- ============================================================
CREATE TABLE workflow_templates (
    id              TEXT PRIMARY KEY,           -- UUID
    slug            TEXT UNIQUE NOT NULL,       -- human-readable identifier
    name            TEXT NOT NULL,              -- display name
    description     TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    definition      JSONB NOT NULL,            -- the template JSON (see section 4)
    parameter_schema JSONB,                     -- JSON Schema for input validation
    is_active       BOOLEAN DEFAULT true,
    created_by      TEXT NOT NULL,              -- "system" | user email | API key ID
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_wf_tmpl_slug ON workflow_templates(slug) WHERE is_active = true;

-- ============================================================
-- Workflow Executions: one per workflow invocation
-- ============================================================
CREATE TABLE workflow_executions (
    id                TEXT PRIMARY KEY,          -- UUID
    template_id       TEXT NOT NULL REFERENCES workflow_templates(id),
    template_version  INTEGER NOT NULL,          -- snapshot of version at execution time
    thread_id         TEXT NOT NULL,             -- links to existing threads table
    parameters        JSONB NOT NULL,            -- resolved parameter values
    status            TEXT NOT NULL DEFAULT 'pending',
                      -- pending | running | completed | failed | cancelled
    started_at        TIMESTAMPTZ,
    completed_at      TIMESTAMPTZ,
    result            JSONB,                     -- final aggregated result
    error             TEXT,
    created_at        TIMESTAMPTZ DEFAULT now()
);

-- Only index active executions (the ones we poll)
CREATE INDEX idx_wf_exec_active
    ON workflow_executions(status)
    WHERE status IN ('pending', 'running');

CREATE INDEX idx_wf_exec_thread
    ON workflow_executions(thread_id);

-- ============================================================
-- Workflow Steps: individual units of work within an execution
-- ============================================================
CREATE TABLE workflow_steps (
    id              TEXT PRIMARY KEY,            -- UUID
    execution_id    TEXT NOT NULL REFERENCES workflow_executions(id) ON DELETE CASCADE,
    step_id         TEXT NOT NULL,               -- from template definition (e.g., "search")
    step_type       TEXT NOT NULL,               -- fan_out | sequential | aggregate |
                                                 -- transform | report | conditional
    depends_on      TEXT[] DEFAULT '{}',         -- step_ids this step waits for
    status          TEXT NOT NULL DEFAULT 'pending',
                    -- pending | running | completed | failed | skipped
    input           JSONB,                       -- input data for this step
    output          JSONB,                       -- output data from this step
    agent_jobs      TEXT[] DEFAULT '{}',          -- K8s job names (for agent steps)
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    error           TEXT,
    retry_count     INTEGER DEFAULT 0,
    max_retries     INTEGER DEFAULT 2,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Prevent duplicate step_ids within the same execution
-- SQLite: same syntax supported
CREATE UNIQUE INDEX idx_wf_steps_exec_step
    ON workflow_steps(execution_id, step_id);

CREATE INDEX idx_wf_steps_exec
    ON workflow_steps(execution_id);

CREATE INDEX idx_wf_steps_active
    ON workflow_steps(execution_id, status)
    WHERE status IN ('pending', 'running');
    -- SQLite: partial indexes supported since 3.8.0

-- ============================================================
-- Workflow Step Agents: individual agent results within a fan-out
-- ============================================================
CREATE TABLE workflow_step_agents (
    id              TEXT PRIMARY KEY,            -- UUID
    step_id         TEXT NOT NULL REFERENCES workflow_steps(id) ON DELETE CASCADE,
    agent_index     INTEGER NOT NULL,            -- 0-based index within fan-out
    k8s_job_name    TEXT,
    thread_id       TEXT NOT NULL,               -- agent's own thread_id
    status          TEXT NOT NULL DEFAULT 'pending',
    input           JSONB,                       -- individual agent input
    output          JSONB,                       -- individual agent result
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    error           TEXT
);

-- Prevent duplicate agent_index within the same step
CREATE UNIQUE INDEX idx_wf_step_agents_unique
    ON workflow_step_agents(step_id, agent_index);

CREATE INDEX idx_wf_step_agents_step
    ON workflow_step_agents(step_id);

-- ============================================================
-- Workflow Template Versions: immutable version history
-- (same pattern as skill_versions)
-- ============================================================
CREATE TABLE workflow_template_versions (
    id              TEXT PRIMARY KEY,
    template_id     TEXT NOT NULL REFERENCES workflow_templates(id),
    version         INTEGER NOT NULL,
    definition      JSONB NOT NULL,             -- SQLite: use JSON type or TEXT
    parameter_schema JSONB,                      -- SQLite: use JSON type or TEXT
    changelog       TEXT,
    created_by      TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now(),   -- SQLite: use TEXT with ISO 8601
    UNIQUE (template_id, version)
);
```

### 3.2 Relationship to Existing Tables

```
threads (existing)           workflow_executions
    |                              |
    +--- thread_id <----- thread_id
    |                              |
jobs (existing)              workflow_steps
    |                              |
    +--- workflow_execution_id     +--- execution_id
    +--- workflow_step_id          |
                              workflow_step_agents
                                   |
                                   +--- step_id
```

The existing `jobs` table gets two new nullable columns:
```sql
ALTER TABLE jobs ADD COLUMN workflow_execution_id TEXT REFERENCES workflow_executions(id);
ALTER TABLE jobs ADD COLUMN workflow_step_id TEXT REFERENCES workflow_steps(id);

CREATE INDEX idx_jobs_wf_exec ON jobs(workflow_execution_id) WHERE workflow_execution_id IS NOT NULL;
CREATE INDEX idx_jobs_wf_step ON jobs(workflow_step_id) WHERE workflow_step_id IS NOT NULL;
-- SQLite: partial indexes supported since 3.8.0
```

### 3.3 State Machine

**Workflow execution states:**
```
pending --> running --> completed
                  |---> failed
                  |---> cancelled
```

**Step states:**
```
pending --> running --> completed
                  |---> failed --> (retry?) --> running
                  |---> skipped (conditional branch not taken)
```

**Transition rules:**
- Execution moves to `running` when first step starts.
- Execution moves to `completed` when all terminal steps complete.
- Execution moves to `failed` if any step fails after exhausting retries (unless step has `on_failure: continue`).
- Step moves to `running` when all `depends_on` steps are `completed`.
- Step moves to `failed` only after `max_retries` exhausted.

---

## 4. Workflow Template Schema

### 4.1 TypeScript Type Definitions

```typescript
// Root template definition (stored in workflow_templates.definition)
type WorkflowTemplate = {
  slug: string;                                  // unique identifier
  name: string;                                  // display name
  description?: string;
  parameters: Record<string, ParameterDef>;      // input parameters
  steps: Step[];                                 // ordered by dependency
};

type ParameterDef = {
  type: "string" | "number" | "boolean" | "array" | "object";
  description?: string;
  required?: boolean;                            // default: true
  default?: any;
  items?: string;                                // for array type: item type
};

// ----- Step Types -----

type Step =
  | FanOutStep
  | SequentialStep
  | AggregateStep
  | TransformStep
  | ReportStep
  | ConditionalStep;

type FanOutStep = {
  id: string;
  type: "fan_out";
  depends_on?: string[];                         // step IDs this waits for
  over: string;                                  // cartesian expression: "regions x sources"
  agent: AgentSpec;
  max_parallel: number;                          // cost control (e.g., 10)
  timeout_seconds: number;                       // per-agent timeout
  on_failure: "fail_workflow" | "continue";      // partial failure handling
};

type SequentialStep = {
  id: string;
  type: "sequential";
  depends_on?: string[];
  agent: AgentSpec;
  timeout_seconds: number;
};

type AggregateStep = {
  id: string;
  type: "aggregate";
  depends_on?: string[];
  input: string;                                 // glob: "search.*" = all outputs from step "search"
  strategy: "merge_arrays" | "merge_objects" | "concat";
  // No user-defined code runs in the workflow engine. All operations are predefined.
};

type TransformStep = {
  id: string;
  type: "transform";
  depends_on?: string[];
  input: string;                                 // step_id whose output is input
  operations: TransformOp[];
};

type TransformOp =
  | { op: "deduplicate"; key: string }           // "name+date+location"
  | { op: "filter"; condition: string }          // JSONPath expression
  | { op: "sort"; field: string; order: "asc" | "desc" }
  | { op: "limit"; count: number };

type ReportStep = {
  id: string;
  type: "report";
  depends_on?: string[];
  input: string;
  format: "json" | "markdown" | "csv";
  delivery: "thread_reply" | "file_upload" | "both";
};

type ConditionalStep = {
  id: string;
  type: "conditional";
  depends_on?: string[];
  condition: string;                             // JSONPath expression evaluated against prior outputs
  then_step: string;                             // step_id to execute if true
  else_step?: string;                            // step_id to execute if false
};

// ----- Agent Spec -----

type AgentSpec = {
  task_template: string;                         // "Search {{ source }} for {{ query }} in {{ region }}"
  task_type: "code_change" | "analysis" | "file_output" | "api_action";
  skills?: string[];                             // skill slugs to inject
  output_schema?: OutputSchema;                  // JSON Schema for structured output
  agent_type?: string;                           // optional agent type override
  timeout_seconds?: number;                      // override step-level timeout
};

type OutputSchema = {
  type: string;
  properties?: Record<string, any>;
  items?: any;
  required?: string[];
};
```

### 4.2 Template Interpolation

> **Security constraint:** Template interpolation MUST NOT use Jinja2 or any engine that supports code execution. Use simple string substitution: `template.replace('{{ region }}', params['region'])`. All `{{ var }}` markers are resolved via `str.replace()` -- no expressions, no filters, no code execution.

Templates use `{{ variable }}` placeholder syntax resolved via simple string substitution:

| Expression | Resolves To |
|:-----------|:------------|
| `{{ query }}` | Value of parameter `query` |
| `{{ region }}` | Current iteration value in fan-out |
| `{{ source }}` | Current iteration value in fan-out |
| `{{ steps.search.output }}` | Output of step with id "search" (resolved by engine lookup, not expression evaluation) |

### 4.3 Fan-Out Expansion

The `over` field in `FanOutStep` defines a cartesian product:

```
over: "regions x sources"
```

Given `regions = ["Dallas", "Austin"]` and `sources = ["eventbrite", "meetup"]`, the compiler expands to 4 agents:

| Agent Index | region | source |
|:------------|:-------|:-------|
| 0 | Dallas | eventbrite |
| 1 | Dallas | meetup |
| 2 | Austin | eventbrite |
| 3 | Austin | meetup |

Single-dimension fan-out uses just the parameter name:
```
over: "regions"
```

---

## 5. Workflow Engine (Python Module)

### 5.1 File Structure

```
controller/src/controller/workflows/
    __init__.py
    engine.py           # WorkflowEngine: top-level orchestration
    compiler.py          # Compiles template + params -> execution plan
    executor.py          # Step executors: fan_out, aggregate, transform, etc.
    models.py            # Pydantic models for templates, steps, results
    templates.py         # TemplateCRUD (same pattern as SkillRegistry)
    intent.py            # IntentClassifier (async worker)
    state.py             # Workflow state persistence (extends StateBackend)
    api.py               # REST API routes for templates + executions
```

### 5.2 WorkflowEngine -- Core Class

```python
class WorkflowEngine:
    """Top-level orchestration. Compiles templates, executes steps, handles results."""

    def __init__(
        self,
        settings: Settings,
        state: StateBackend,          # existing state backend
        workflow_state: WorkflowState, # new workflow state backend
        redis_state: RedisState,
        spawner: JobSpawner,
        registry: IntegrationRegistry,
        template_store: TemplateStore,
    ):
        self._compiler = WorkflowCompiler(template_store)
        self._executor = StepExecutor(settings, state, redis_state, spawner)
        ...

    async def match_template(self, task_request: TaskRequest) -> MatchResult | None:
        """Check if a task matches a workflow template.

        Uses pre-classified intent if available, otherwise falls back
        to the single-task template for non-workflow tasks.
        """

    async def start(
        self,
        template_slug: str,
        parameters: dict,
        thread_id: str,
        task_request: TaskRequest,
    ) -> str:
        """Compile template into execution plan, persist, start first step(s).

        Returns: execution_id
        """
        # 1. Load template
        template = await self._template_store.get_by_slug(template_slug)

        # 2. Validate parameters against template.parameter_schema
        self._compiler.validate_params(template, parameters)

        # 3. Compile: expand fan-outs, resolve dependencies, create step records
        execution = self._compiler.compile(template, parameters, thread_id)

        # 3a. Enforce agent limits
        total_agents = sum(
            len(self._compiler.expand_over(s, parameters))
            for s in template.definition["steps"]
            if s["type"] == "fan_out"
        )
        if total_agents > self._settings.workflow_max_agents_per_execution:
            raise ValueError(
                f"Workflow would spawn {total_agents} agents, "
                f"exceeding limit of {self._settings.workflow_max_agents_per_execution}"
            )

        # 4. Persist execution + steps
        await self._workflow_state.create_execution(execution)

        # 5. Start all steps with no dependencies (roots of the DAG)
        root_steps = [s for s in execution.steps if not s.depends_on]
        for step in root_steps:
            await self._execute_step(execution, step)

        return execution.id

    async def advance(self, execution_id: str) -> None:
        """Called after a step completes. Determine and start next step(s).

        Implements the DAG traversal: find steps whose dependencies are all completed.

        **Concurrency control:** Multiple agents may complete simultaneously and call
        advance() concurrently. We use atomic status transitions to prevent double-execution:
        - Postgres: `SELECT ... FOR UPDATE` on the execution row to serialize advance() calls.
        - SQLite: `BEGIN EXCLUSIVE` transaction for the same effect.
        - Step start uses atomic CAS: `UPDATE workflow_steps SET status = 'running'
          WHERE id = ? AND status = 'pending'` -- returns 0 rows if already started.
        - Only the winning advance() call proceeds for each step; losers are no-ops.
        """
        # Acquire exclusive lock on execution (FOR UPDATE in Postgres, BEGIN EXCLUSIVE in SQLite)
        async with self._workflow_state.lock_execution(execution_id) as execution:
            steps = await self._workflow_state.get_steps(execution_id)

            # Find steps that are pending and whose deps are all completed
            completed_ids = {s.step_id for s in steps if s.status == "completed"}
            for step in steps:
                if step.status != "pending":
                    continue
                if all(dep in completed_ids for dep in step.depends_on):
                    # Atomic CAS: only proceeds if step is still pending
                    started = await self._workflow_state.try_start_step(step.id)
                    if started:
                        await self._execute_step(execution, step)

        # Check if workflow is complete (all steps completed or skipped)
        terminal_statuses = {"completed", "skipped", "failed"}
        if all(s.status in terminal_statuses for s in steps):
            # Find the last report/transform step output as the final result
            final_step = next(
                (s for s in reversed(steps) if s.status == "completed" and s.output),
                None,
            )
            await self._workflow_state.complete_execution(
                execution_id,
                result=final_step.output if final_step else None,
            )

    async def handle_agent_result(
        self,
        execution_id: str,
        step_id: str,
        agent_index: int,
        result: AgentResult,
    ) -> None:
        """Called when an agent K8s job completes. Store result, maybe advance."""
        # 1. Store agent result
        await self._workflow_state.store_agent_result(
            step_id=step_id,
            agent_index=agent_index,
            result=result,
        )

        # 2. Check if all agents for this step are done
        step = await self._workflow_state.get_step(step_id)
        agents = await self._workflow_state.get_step_agents(step_id)

        all_done = all(a.status in ("completed", "failed") for a in agents)
        if not all_done:
            return  # still waiting for other agents

        # 3. Handle partial failures
        failed = [a for a in agents if a.status == "failed"]
        succeeded = [a for a in agents if a.status == "completed"]

        if step.on_failure == "fail_workflow" and failed:
            await self._workflow_state.fail_step(step_id, error=f"{len(failed)} agents failed")
            await self._workflow_state.fail_execution(execution_id)
            return

        # 4. Merge successful results into step output
        merged_output = [a.output for a in succeeded if a.output]
        await self._workflow_state.complete_step(step_id, output=merged_output)

        # 5. Advance workflow
        await self.advance(execution_id)

    async def cancel(self, execution_id: str) -> None:
        """Cancel a running workflow. Kill active agent K8s jobs."""
        steps = await self._workflow_state.get_steps(execution_id)
        for step in steps:
            if step.status == "running":
                for job_name in step.agent_jobs:
                    self._spawner.delete_job(job_name)
                await self._workflow_state.fail_step(step.id, error="cancelled")
        await self._workflow_state.cancel_execution(execution_id)

    async def reconcile(self) -> None:
        """Crash recovery: reconcile in-flight workflows on startup and periodically.

        Runs once on controller startup, then every 60 seconds.

        1. Query: SELECT * FROM workflow_executions WHERE status = 'running'
        2. For each execution, check K8s job status of all active steps
        3. If K8s job completed but step not updated -> re-process the result
        4. If K8s job missing (node crash) -> mark step failed, advance workflow
        5. If step has been 'running' longer than 2x timeout -> mark failed, advance
        """
        stale_executions = await self._workflow_state.get_executions_by_status("running")
        for execution in stale_executions:
            steps = await self._workflow_state.get_steps(execution.id)
            for step in steps:
                if step.status != "running":
                    continue
                agents = await self._workflow_state.get_step_agents(step.id)
                for agent in agents:
                    if agent.status != "running":
                        continue
                    k8s_status = self._spawner.get_job_status(agent.k8s_job_name)
                    if k8s_status == "completed":
                        result = await self._spawner.get_job_result(agent.k8s_job_name)
                        await self.handle_agent_result(
                            execution.id, step.step_id, agent.agent_index, result
                        )
                    elif k8s_status == "not_found" or k8s_status == "failed":
                        await self._workflow_state.fail_agent(
                            agent.id, error=f"K8s job {k8s_status}"
                        )
            # Re-advance in case any steps were updated
            await self.advance(execution.id)

    async def _execute_step(self, execution, step) -> None:
        """Dispatch to the appropriate step executor."""
        await self._workflow_state.start_step(step.id)
        await self._executor.execute(execution, step)
```

### 5.3 StepExecutor

```python
class StepExecutor:
    """Executes individual workflow steps by type."""

    async def execute(self, execution, step) -> None:
        match step.step_type:
            case "fan_out":
                await self._execute_fan_out(execution, step)
            case "sequential":
                await self._execute_sequential(execution, step)
            case "aggregate":
                await self._execute_aggregate(execution, step)
            case "transform":
                await self._execute_transform(execution, step)
            case "report":
                await self._execute_report(execution, step)
            case "conditional":
                await self._execute_conditional(execution, step)

    async def _execute_fan_out(self, execution, step) -> None:
        """Spawn N agents in parallel with concurrency control.

        1. Expand cartesian product from step.over
        2. Check global concurrent agent limit
        3. Create workflow_step_agents records
        4. Spawn K8s jobs up to max_parallel
        5. Remaining agents queued, started as slots free up
        """
        expansions = self._expand_over(step.over, execution.parameters)

        # Enforce global concurrent agent limit
        # SELECT COUNT(*) FROM workflow_step_agents WHERE status = 'running'
        running_count = await self._workflow_state.count_running_agents()
        if running_count + len(expansions) > self._settings.workflow_max_concurrent_agents:
            raise ResourceExhaustedError(
                f"Global agent limit reached: {running_count} running, "
                f"{len(expansions)} requested, limit {self._settings.workflow_max_concurrent_agents}"
            )
        semaphore = asyncio.Semaphore(step.max_parallel)

        for i, params in enumerate(expansions):
            task = self._render_task(step.agent.task_template, params)
            agent_thread_id = f"{execution.thread_id}:wf:{execution.id}:s:{step.step_id}:a:{i}"

            # Build task payload for agent
            task_payload = {
                "task": task,
                "task_type": step.agent.task_type,
                "skills": step.agent.skills or [],
                "output_schema": step.agent.output_schema,
                "workflow_context": {
                    "execution_id": execution.id,
                    "step_id": step.step_id,
                    "agent_index": i,
                },
            }

            async with semaphore:
                await self._redis.push_task(agent_thread_id, task_payload)
                job_name = self._spawner.spawn(
                    thread_id=agent_thread_id,
                    github_token="",
                    redis_url=self._settings.redis_url,
                )
                await self._workflow_state.record_agent_job(
                    step_id=step.id,
                    agent_index=i,
                    k8s_job_name=job_name,
                    thread_id=agent_thread_id,
                )

    async def _execute_aggregate(self, execution, step) -> None:
        """Merge results from prior steps. No agent involved.

        Before merging, validate each agent's output against the `output_schema`
        defined in the originating step. Invalid results are logged, excluded from
        the merge, and reported in step metadata as `validation_errors`.

        Strategies:
        - merge_arrays: flatten all arrays into one
        - merge_objects: deep merge objects
        - concat: concatenate as strings
        """
        input_data, validation_errors = self._validate_and_filter_inputs(
            step.input, execution
        )
        if validation_errors:
            await self._workflow_state.update_step_metadata(
                step.id, {"validation_errors": validation_errors}
            )

        match step.strategy:
            case "merge_arrays":
                result = []
                for item in input_data:
                    if isinstance(item, list):
                        result.extend(item)
                    else:
                        result.append(item)
            case "merge_objects":
                result = {}
                for item in input_data:
                    if isinstance(item, dict):
                        result.update(item)
            case "concat":
                result = "\n".join(str(item) for item in input_data)

        await self._workflow_state.complete_step(step.id, output=result)
        await self._engine.advance(execution.id)

    async def _execute_transform(self, execution, step) -> None:
        """Apply deterministic transformations. No agent involved."""
        data = self._resolve_input(step.input, execution)

        for op in step.operations:
            match op:
                case {"op": "deduplicate", "key": key}:
                    fields = key.split("+")
                    seen = set()
                    unique = []
                    for item in data:
                        k = tuple(item.get(f, "") for f in fields)
                        if k not in seen:
                            seen.add(k)
                            unique.append(item)
                    data = unique

                case {"op": "filter", "condition": condition}:
                    data = [item for item in data if self._eval_condition(item, condition)]

                case {"op": "sort", "field": field, "order": order}:
                    data = sorted(data, key=lambda x: x.get(field, ""),
                                  reverse=(order == "desc"))

                case {"op": "limit", "count": count}:
                    data = data[:count]

        await self._workflow_state.complete_step(step.id, output=data)
        await self._engine.advance(execution.id)
```

### 5.4 WorkflowCompiler

```python
class WorkflowCompiler:
    """Compiles a template + parameters into an execution plan."""

    def validate_params(self, template, parameters: dict) -> None:
        """Validate parameters against template.parameter_schema using jsonschema."""
        if template.parameter_schema:
            jsonschema.validate(parameters, template.parameter_schema)

    def compile(self, template, parameters: dict, thread_id: str) -> WorkflowExecution:
        """Create execution plan from template.

        Steps:
        1. Validate template is a valid DAG (no cycles)
        2. Resolve depends_on from template step ordering
        3. Create WorkflowExecution + WorkflowStep records
        4. Interpolate parameter references in step configs
        """
        self._validate_dag(template.definition["steps"])

        execution_id = uuid.uuid4().hex
        steps = []

        for step_def in template.definition["steps"]:
            depends_on = step_def.get("depends_on", [])
            if not depends_on:
                # Infer from position: depends on previous step
                idx = template.definition["steps"].index(step_def)
                if idx > 0:
                    depends_on = [template.definition["steps"][idx - 1]["id"]]

            steps.append(WorkflowStep(
                id=uuid.uuid4().hex,
                execution_id=execution_id,
                step_id=step_def["id"],
                step_type=step_def["type"],
                depends_on=depends_on,
                input=self._resolve_step_input(step_def, parameters),
                max_retries=step_def.get("max_retries", 2),
            ))

        return WorkflowExecution(
            id=execution_id,
            template_id=template.id,
            template_version=template.version,
            thread_id=thread_id,
            parameters=parameters,
            steps=steps,
        )

    def _validate_dag(self, steps: list[dict]) -> None:
        """Topological sort to verify no cycles exist."""
        # Kahn's algorithm
        in_degree = {s["id"]: 0 for s in steps}
        graph = {s["id"]: [] for s in steps}
        for s in steps:
            for dep in s.get("depends_on", []):
                graph[dep].append(s["id"])
                in_degree[s["id"]] += 1

        queue = [n for n in in_degree if in_degree[n] == 0]
        visited = 0
        while queue:
            node = queue.pop(0)
            visited += 1
            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited != len(steps):
            raise ValueError("Workflow template contains a cycle -- DAG-only workflows supported")
```

---

## 6. Agent Contract

The formal interface between the workflow engine and agent pods. Agents are stateless workers -- they know nothing about the workflow.

### 6.1 Agent Input (Redis task payload)

```json
{
  "task": "Search eventbrite for music events in Dallas, TX",
  "task_type": "analysis",
  "system_prompt": "You are a data research agent...",
  "skills": [
    {
      "slug": "web-search",
      "name": "Web Search",
      "content": "..."
    }
  ],
  "output_schema": {
    "type": "array",
    "items": {
      "type": "object",
      "required": ["name", "date", "location", "source_url"],
      "properties": {
        "name": {"type": "string"},
        "date": {"type": "string", "format": "date"},
        "location": {"type": "string"},
        "source_url": {"type": "string", "format": "uri"},
        "description": {"type": "string"},
        "price": {"type": "string"}
      }
    }
  },
  "workflow_context": {
    "execution_id": "abc123",
    "step_id": "search",
    "agent_index": 3
  }
}
```

### 6.2 Agent Output (Redis result payload)

```json
{
  "result": [
    {
      "name": "Jazz in the Park",
      "date": "2026-04-15",
      "location": "Klyde Warren Park, Dallas, TX",
      "source_url": "https://eventbrite.com/e/jazz-in-the-park-123",
      "description": "Free outdoor jazz concert",
      "price": "Free"
    }
  ],
  "provenance": [
    {
      "url": "https://eventbrite.com/d/tx--dallas/music-events/",
      "query": "music events Dallas TX",
      "timestamp": "2026-03-25T14:32:01Z",
      "items_found": 12
    }
  ],
  "quality": {
    "items_returned": 12,
    "schema_valid": true,
    "missing_fields": []
  },
  "exit_code": 0,
  "stderr": ""
}
```

### 6.3 Contract Rules

| Rule | Description |
|:-----|:------------|
| **No workflow awareness** | Agents never read `workflow_context` to make decisions. It exists only for result routing. |
| **No child spawning** | Agents do not spawn sub-agents. Fan-out is the engine's job. |
| **Schema compliance** | If `output_schema` is provided, agent output MUST validate against it. |
| **Provenance required** | For `analysis` tasks, agents MUST include `provenance` in output. |
| **Timeout respected** | Agents must complete within `timeout_seconds` or be killed by K8s. |
| **Idempotent** | Agent tasks should be safe to retry (engine handles retry logic). |

---

## 7. Entrypoint Changes

Based on Q3 expert recommendation: output-type routing. The existing `entrypoint.sh` gets a routing layer.

### 7.1 Routing Logic

```bash
#!/bin/bash
set -euo pipefail

# Read task from Redis
TASK_JSON=$(redis-cli -u "$REDIS_URL" GET "task:$THREAD_ID")
TASK_TYPE=$(echo "$TASK_JSON" | jq -r '.task_type // "code_change"')

case "$TASK_TYPE" in
  code_change)
    # === CURRENT PATH (unchanged) ===
    # Git clone, branch, claude -p, push, auto-PR
    REPO_URL=$(echo "$TASK_JSON" | jq -r '.repo_url')
    BRANCH=$(echo "$TASK_JSON" | jq -r '.branch')

    git clone "$REPO_URL" /workspace
    cd /workspace
    git checkout -b "$BRANCH"

    # Inject skills, run claude
    claude -p "$TASK" --output-format json

    # Push and report
    git push origin "$BRANCH"
    # ... existing PR creation logic ...
    ;;

  analysis|file_output|api_action)
    # === NEW PATH: Non-code tasks ===
    # No git clone. Workspace dir only.
    mkdir -p /workspace && cd /workspace

    # Inject skills
    # ... same skill injection as code path ...

    # Run claude with output schema enforcement
    OUTPUT_SCHEMA=$(echo "$TASK_JSON" | jq -r '.output_schema // empty')
    if [ -n "$OUTPUT_SCHEMA" ]; then
      claude -p "$TASK" --output-format json \
        --system "Return ONLY valid JSON matching this schema: $OUTPUT_SCHEMA"
    else
      claude -p "$TASK" --output-format json
    fi

    # Post result to Redis (no git push)
    RESULT=$(cat /workspace/result.json 2>/dev/null || echo '{}')
    redis-cli -u "$REDIS_URL" SET "result:$THREAD_ID" "$RESULT" EX 3600
    ;;
esac
```

### 7.2 What Changes, What Stays

| Component | Code Tasks | Non-Code Tasks |
|:----------|:-----------|:---------------|
| Git clone | Yes | No |
| Branch creation | Yes | No |
| Claude invocation | Yes | Yes |
| Skill injection | Yes | Yes |
| Output schema | Optional | Required (when template specifies) |
| Result delivery | Git push + PR | Redis result key |
| MCP tools | Gateway-scoped | Gateway-scoped |
| Workspace | Git repo | Empty directory |

---

## 8. Intent Classifier

Based on Q1 expert recommendation: async pre-processing worker.

### 8.1 Architecture

```
Webhook arrives
    |
    v
Controller API
    |
    +---> POST /api/v1/internal/classify-intent
    |       (or inline call from handle_task)
    |
    +---> Push to Redis Stream: "df:intent_classify"
    |       payload: { thread_id, task, source, source_ref }
    |
    +---> Ack webhook immediately (<200ms)
              |
              v
    +----------------------------+
    | Intent Classification      |
    | Worker (async)             |
    |                            |
    | 1. Pop from Redis Stream   |
    | 2. Load active templates   |
    | 3. LLM call:               |
    |    "Given this request,    |
    |     which template and     |
    |     what parameters?"      |
    |                            |
    | 4. Fallback: rule-based    |
    |    keyword matching if     |
    |    LLM unavailable         |
    |                            |
    | 5. Validate extracted      |
    |    params against schema   |
    |                            |
    | 6. Push result to Redis:   |
    |    "df:intent_result:{id}" |
    +----------------------------+
              |
              v
    Orchestrator picks up result
    and calls WorkflowEngine.start()
```

### 8.2 Input Sanitization

User input is sanitized before LLM classification:
- XML/HTML tags stripped (prevents injection via markup)
- Maximum 2000 characters (truncated with warning)
- Prompt injection markers removed (`ignore previous`, `system:`, etc.)
- Confidence threshold: if classifier confidence < 0.7, fall back to `single-task` template

### 8.3 Intent Result Schema

```python
@dataclass
class IntentResult:
    thread_id: str
    template_slug: str | None          # None = no template matched, use single-agent
    parameters: dict                    # extracted parameters
    confidence: float                   # 0.0-1.0
    method: str                         # "llm" | "rule_based" | "explicit"
    raw_task: str                       # original task text
```

### 8.4 Confidence Thresholds

| Confidence | Action |
|:-----------|:-------|
| >= 0.8 | Execute template automatically |
| 0.7 - 0.8 | Execute template but log for review |
| < 0.7 | Fall back to `single-task` template |

### 8.5 Rule-Based Fallback

When the LLM is unavailable (circuit breaker tripped), the classifier falls back to keyword matching:

```python
RULES = [
    {
        "pattern": r"(find|search|look for).*(events?|concerts?|shows?)",
        "regions_pattern": r"in\s+(.+?)(?:\s+and\s+|\s*,\s*)",
        "template": "geo-search",
    },
    # ... more rules
]
```

This is intentionally simple. It handles the happy path when LLM is down. Edge cases get routed to single-agent.

---

## 9. API Reference

### 9.1 Workflow Templates

| Method | Path | Description |
|:-------|:-----|:------------|
| `POST` | `/api/v1/workflows/templates` | Create a new template |
| `GET` | `/api/v1/workflows/templates` | List all active templates |
| `GET` | `/api/v1/workflows/templates/{slug}` | Get template by slug |
| `PUT` | `/api/v1/workflows/templates/{slug}` | Update template (bumps version) |
| `DELETE` | `/api/v1/workflows/templates/{slug}` | Soft delete (sets `is_active = false`) |

**Create Template Request:**
```json
{
  "slug": "geo-search",
  "name": "Geographic Event Search",
  "description": "Fan-out search across regions and sources",
  "definition": { "...template JSON..." },
  "parameter_schema": { "...JSON Schema..." }
}
```

**Create Template Response:**
```json
{
  "id": "tmpl_abc123",
  "slug": "geo-search",
  "version": 1,
  "created_at": "2026-03-25T10:00:00Z"
}
```

### 9.2 Cost Estimation

| Method | Path | Description |
|:-------|:-----|:------------|
| `POST` | `/api/v1/workflows/estimate` | Estimate cost before executing |

**Request:**
```json
{
  "template_slug": "geo-search",
  "parameters": {
    "query": "music events",
    "regions": ["Dallas, TX", "Austin, TX", "Houston, TX"],
    "sources": ["eventbrite", "meetup"]
  }
}
```

**Response:**
```json
{
  "estimated_agents": 6,
  "estimated_steps": 4,
  "estimated_cost_usd": 3.50,
  "estimated_duration_seconds": 300,
  "warnings": ["fan-out produces 6 agents"]
}
```

### 9.3 Workflow Executions

| Method | Path | Description |
|:-------|:-----|:------------|
| `POST` | `/api/v1/workflows/executions` | Start a workflow |
| `GET` | `/api/v1/workflows/executions` | List executions (filterable by status) |
| `GET` | `/api/v1/workflows/executions/{id}` | Get execution with all steps |
| `POST` | `/api/v1/workflows/executions/{id}/cancel` | Cancel running workflow |

**Start Workflow Request:**
```json
{
  "template_slug": "geo-search",
  "parameters": {
    "query": "music events",
    "regions": ["Dallas, TX", "Austin, TX"],
    "sources": ["eventbrite", "meetup"]
  },
  "thread_id": "th_abc123"
}
```

**Get Execution Response:**
```json
{
  "id": "exec_def456",
  "template_slug": "geo-search",
  "status": "running",
  "started_at": "2026-03-25T10:01:00Z",
  "steps": [
    {
      "step_id": "search",
      "type": "fan_out",
      "status": "running",
      "agents": [
        {"index": 0, "status": "completed", "region": "Dallas, TX", "source": "eventbrite"},
        {"index": 1, "status": "running", "region": "Dallas, TX", "source": "meetup"},
        {"index": 2, "status": "completed", "region": "Austin, TX", "source": "eventbrite"},
        {"index": 3, "status": "pending", "region": "Austin, TX", "source": "meetup"}
      ]
    },
    {"step_id": "merge", "type": "aggregate", "status": "pending"},
    {"step_id": "dedupe", "type": "transform", "status": "pending"},
    {"step_id": "deliver", "type": "report", "status": "pending"}
  ]
}
```

### 9.4 Structured Error Codes

All API error responses include a machine-readable `error_code`:

| Code | HTTP Status | Description |
|:-----|:------------|:------------|
| `TEMPLATE_NOT_FOUND` | 404 | Template slug does not exist or is inactive |
| `INVALID_PARAMETERS` | 400 | Parameters fail JSON Schema validation |
| `AGENT_LIMIT_EXCEEDED` | 429 | Fan-out would exceed `max_agents_per_execution` |
| `GLOBAL_LIMIT_EXCEEDED` | 429 | System-wide concurrent agent limit reached |
| `EXECUTION_NOT_FOUND` | 404 | Execution ID does not exist |
| `EXECUTION_NOT_CANCELLABLE` | 409 | Execution already completed/failed |
| `TEMPLATE_CYCLE_DETECTED` | 400 | Template DAG contains a cycle |

**Error response format:**
```json
{
  "error_code": "AGENT_LIMIT_EXCEEDED",
  "message": "Workflow would spawn 25 agents, exceeding limit of 20",
  "details": {"requested": 25, "limit": 20}
}
```

### 9.5 Internal APIs

| Method | Path | Description |
|:-------|:-----|:------------|
| `POST` | `/api/v1/internal/classify-intent` | Classify a task (used by intent worker) |
| `POST` | `/api/v1/internal/agent-result` | Agent posts result (called by entrypoint) |

---

## 10. Starter Templates

### 10.1 `single-task` -- Wraps Current Behavior

```json
{
  "slug": "single-task",
  "name": "Single Task",
  "description": "Wraps the current single-agent execution as a workflow. Default template.",
  "parameters": {
    "task": {"type": "string", "required": true, "description": "The task to execute"},
    "task_type": {"type": "string", "default": "code_change"}
  },
  "steps": [
    {
      "id": "execute",
      "type": "sequential",
      "agent": {
        "task_template": "{{ task }}",
        "task_type": "{{ task_type }}"
      },
      "timeout_seconds": 1800
    }
  ]
}
```

### 10.2 `geo-search` -- Multi-Region, Multi-Source Search

```json
{
  "slug": "geo-search",
  "name": "Geographic Search",
  "description": "Fan-out search across regions and data sources, merge and deduplicate results.",
  "parameters": {
    "query": {"type": "string", "required": true, "description": "What to search for"},
    "regions": {"type": "array", "items": "string", "required": true, "description": "Geographic regions"},
    "sources": {"type": "array", "items": "string", "required": true, "description": "Data sources to search"}
  },
  "steps": [
    {
      "id": "search",
      "type": "fan_out",
      "over": "regions x sources",
      "agent": {
        "task_template": "Search {{ source }} for {{ query }} in {{ region }}. Return structured results.",
        "task_type": "analysis",
        "skills": ["web-search"],
        "output_schema": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["name", "date", "location", "source_url"],
            "properties": {
              "name": {"type": "string"},
              "date": {"type": "string"},
              "location": {"type": "string"},
              "source_url": {"type": "string"},
              "description": {"type": "string"},
              "price": {"type": "string"}
            }
          }
        }
      },
      "max_parallel": 10,
      "timeout_seconds": 300,
      "on_failure": "continue"
    },
    {
      "id": "merge",
      "type": "aggregate",
      "depends_on": ["search"],
      "input": "search.*",
      "strategy": "merge_arrays"
    },
    {
      "id": "dedupe",
      "type": "transform",
      "depends_on": ["merge"],
      "input": "merge",
      "operations": [
        {"op": "deduplicate", "key": "name+date+location"},
        {"op": "sort", "field": "date", "order": "asc"}
      ]
    },
    {
      "id": "deliver",
      "type": "report",
      "depends_on": ["dedupe"],
      "input": "dedupe",
      "format": "markdown",
      "delivery": "thread_reply"
    }
  ]
}
```

---

## 11. Phased Implementation

### Phase 1: Foundation (~1 week)

| Task | Description | Files |
|:-----|:------------|:------|
| Data model + migrations | Create tables, add columns to `jobs` | `alembic/versions/`, `workflows/state.py` |
| Pydantic models | Template, Execution, Step models | `workflows/models.py` |
| Template CRUD + API | CRUD operations, REST endpoints | `workflows/templates.py`, `workflows/api.py` |
| WorkflowEngine core | `start()`, `advance()`, sequential steps only | `workflows/engine.py` |
| WorkflowCompiler | Template validation, DAG check, compilation | `workflows/compiler.py` |
| `single-task` template | Seed template that wraps current behavior | DB seed migration |
| Feature flag | `DF_WORKFLOW_ENGINE_ENABLED=false` (off by default) | `config.py` |
| Orchestrator integration | Route to engine when flag on + template matches | `orchestrator.py` |

**Exit criteria:** `single-task` template executes identically to current single-agent path. All existing tests pass.

### Phase 2: Fan-Out (~1 week)

| Task | Description | Files |
|:-----|:------------|:------|
| Fan-out executor | Cartesian expansion, parallel agent spawning | `workflows/executor.py` |
| Aggregate step | merge_arrays, merge_objects, concat strategies | `workflows/executor.py` |
| Transform step | deduplicate, filter, sort, limit | `workflows/executor.py` |
| Report step | Format + deliver via integration | `workflows/executor.py` |
| `geo-search` template | Seed template for multi-region search | DB seed migration |
| Entrypoint routing | Code vs non-code task_type routing | `agent/entrypoint.sh` |
| Agent result posting | Non-code agents post to Redis | `agent/entrypoint.sh` |
| Partial failure | Handle N-of-M agents completing | `workflows/engine.py` |
| Observability | Tracing spans for workflow engine events | `workflows/engine.py` |
| Crash recovery | Reconciliation loop on startup + periodic | `workflows/engine.py` |

**Exit criteria:** `geo-search` template executes end-to-end with 4+ agents, results merged and deduplicated. Traces visible in observability backend.

### Phase 3: Intent + Polish (~1 week)

| Task | Description | Files |
|:-----|:------------|:------|
| Intent classifier worker | Async Redis Stream consumer | `workflows/intent.py` |
| LLM classification prompt | Template matching + param extraction | `workflows/intent.py` |
| Rule-based fallback | Keyword matching when LLM down | `workflows/intent.py` |
| Orchestrator intent flow | Ack fast, classify async, route to engine | `orchestrator.py` |
| Conditional step | Branch on expression evaluation | `workflows/executor.py` |
| Quality checks | Schema validation in aggregate step (Q4 Tier 1) | `workflows/executor.py` |

**Exit criteria:** End-to-end flow from webhook to delivered results. Intent classifier correctly routes known templates.

---

## 12. Configuration

New environment variables (all prefixed with `DF_`):

| Variable | Default | Description |
|:---------|:--------|:------------|
| `DF_WORKFLOW_ENABLED` | `false` | Master on/off switch (Settings.workflow_enabled) |
| `DF_WORKFLOW_ENGINE_ENABLED` | `false` | Feature flag for workflow engine routing |
| `DF_WORKFLOW_MAX_AGENTS_PER_EXECUTION` | `20` | Max agents a single workflow can spawn |
| `DF_WORKFLOW_MAX_CONCURRENT_AGENTS` | `50` | Global system-wide concurrent agent limit |
| `DF_WORKFLOW_MAX_STEPS` | `50` | Max steps per workflow template |
| `DF_WORKFLOW_STEP_TIMEOUT_DEFAULT` | `600` | Default step timeout (seconds) |
| `DF_WORKFLOW_INTENT_CONFIDENCE_THRESHOLD` | `0.5` | Min confidence to use template |
| `DF_WORKFLOW_INTENT_AUTO_THRESHOLD` | `0.8` | Confidence for auto-execution |
| `DF_INTENT_CLASSIFIER_ENABLED` | `false` | Enable async intent classification |
| `DF_INTENT_CLASSIFIER_CONCURRENCY` | `5` | Concurrent LLM calls per worker |
| `DF_INTENT_CLASSIFIER_FALLBACK` | `true` | Enable rule-based fallback |

---

## 13. Trade-offs and Risks

### 13.1 What We Gain

| Gain | Description |
|:-----|:------------|
| Scalable fan-out | N agents in parallel, deterministic merge |
| Predictable execution | DAG = always terminates, cost is bounded |
| Single-purpose agents | Simpler prompts, better results, easier debugging |
| Workflow templates as data | Version, test, roll back without code deploys |
| Output-type routing | Fast startup for non-code tasks (no git clone) |

### 13.2 What We Give Up

| Loss | Mitigation |
|:-----|:-----------|
| Agent flexibility | Agents can still do anything within their task scope |
| `spawn_subagent` | Replaced by fan-out steps (more predictable) |
| Dynamic agent chains | Must be pre-defined in template (conditional steps add some dynamism) |
| Live workflow editing | Immutable versions; create new version instead |

### 13.3 Risks

| Risk | Likelihood | Impact | Mitigation |
|:-----|:-----------|:-------|:-----------|
| Template expressiveness too limited | Medium | Medium | Conditional steps + predefined transforms; escape hatch to single-agent |
| Intent classification accuracy | Medium | Low | Confidence thresholds + rule-based fallback + single-agent default |
| Cost runaway (too many agents) | Low | High | `max_parallel`, global agent limit, per-workflow budget caps |
| Fan-out partial failures | Medium | Medium | `on_failure: continue` with error reporting |
| Workflow state corruption | Low | High | Postgres transactions, idempotent step transitions |
| Redis as result transport loses data | Low | Medium | TTL-based expiry (1hr); results also persisted to Postgres step_agents |

---

## 14. Architectural Decision Records

### ADR-002: Two-State Workflow Model

**Status:** Proposed

**Context:** The current system treats agents as autonomous entities that can spawn sub-agents, select their own tools, and decide their own execution strategy. This creates unpredictable cost, non-deterministic execution paths, and agents that are hard to debug. We need multi-step workflows (fan-out, merge, transform) but cannot trust agents to coordinate themselves.

**Decision:** Adopt a two-state model:
- **State 1 (Deterministic):** The workflow engine is Python code with no LLM calls. It compiles templates, executes DAGs, spawns agents, merges results, and delivers output. Every decision is deterministic and testable.
- **State 2 (Agent Reasoning):** Each agent receives a single task with skills and an output schema. The agent uses LLM reasoning to complete the task. It has no knowledge of the workflow, cannot spawn children, and must return structured output.

**Consequences:**
- Easier: Cost prediction, debugging, testing, workflow composition
- Harder: Agents cannot adapt to unexpected situations that require workflow changes
- Harder: Complex tasks must be decomposed into template steps at design time

---

### ADR-003: DAG-Only Workflows

**Status:** Proposed

**Context:** Workflow systems that support loops risk infinite execution and unbounded cost. Our agents are expensive ($0.50-$5.00 per invocation). A loop that runs 100 times costs $50-$500. We need iterative behavior (pagination, retries) but not at the workflow level.

**Decision:** Workflow templates are DAGs (directed acyclic graphs). No cycles allowed. The compiler validates this with topological sort at template creation time.

- **Pagination** is agent-internal (the agent pages through results within its task).
- **Retry** is infrastructure (the engine retries failed steps, not a loop in the template).
- **Fan-out** covers "do X for each Y" (deterministic expansion, not iteration).

Post-MVP, we may add conditional continuation (`while condition, add more fan-out agents`) with hard caps, but unbounded loops are permanently off the table.

**Consequences:**
- Easier: Cost is exactly predictable (steps * agents * cost-per-agent)
- Easier: Workflows always terminate
- Harder: Some workflows require creative decomposition to avoid loops

---

### ADR-004: Output-Type Routing

**Status:** Proposed

**Context:** The current entrypoint assumes every task needs a git repository. For data-gathering tasks (search, scrape, analyze), git clone adds 10-30 seconds of startup time and creates unnecessary branches. Non-code tasks produce structured data, not code changes.

**Decision:** The agent entrypoint routes on `task_type`:
- `code_change`: Full git flow (clone, branch, push, PR) -- current behavior, unchanged.
- `analysis`, `file_output`, `api_action`: Lightweight mode -- create `/workspace`, skip clone, agent writes results to stdout or a results file, post results to Redis.

**Consequences:**
- Easier: Non-code agents start 10-30 seconds faster
- Easier: No unnecessary git branches for data tasks
- Easier: Results flow through Redis to the workflow engine for aggregation
- Harder: Two code paths in entrypoint (mitigated: 80% shared setup logic)
- Harder: Non-code tasks lose git-based audit trail (mitigated: results persisted in `workflow_step_agents` table)

---

## 15. Migration from Current Architecture

### 15.1 Deprecation Plan

| Component | Status | Migration Path |
|:----------|:-------|:---------------|
| `spawn_subagent` MCP tool | Deprecated | Replaced by fan-out steps in workflow templates |
| Single-agent dispatch | Preserved | Wrapped as `single-task` template (default) |
| `gateway` MCP tool | Preserved | Used for code tasks, not used for non-code |
| `check_messages` MCP tool | Preserved | Useful for long-running code tasks |
| `report_result` integration method | Preserved | Used by report step for delivery |

### 15.2 Feature Flag Rollout

```
Phase 1: DF_WORKFLOW_ENGINE_ENABLED=false
  - Templates and API available but no routing
  - Can test via direct API calls to /api/v1/workflows/executions

Phase 2: DF_WORKFLOW_ENGINE_ENABLED=true, DF_INTENT_CLASSIFIER_ENABLED=false
  - single-task template active (wraps current behavior)
  - Manual workflow execution via API only

Phase 3: DF_INTENT_CLASSIFIER_ENABLED=true
  - Full pipeline: webhook -> classify -> route -> execute
  - Confidence thresholds gate automatic template selection
```

### 15.3 Rollback Strategy

Setting `DF_WORKFLOW_ENGINE_ENABLED=false` immediately reverts to the current single-agent path. No data migration needed. Workflow tables remain but are not read.

---

## Appendix A: Workflow Engine vs Temporal

We evaluated Temporal.io as an alternative to a custom engine (see `docs/research/workflow-engine-code-based.md`). The decision to build custom is based on:

| Factor | Custom Engine | Temporal |
|:-------|:-------------|:---------|
| Operational overhead | Zero (runs in controller) | Temporal server cluster + workers |
| Learning curve | Python (team knows it) | Temporal SDK concepts (activities, signals, queries) |
| Feature fit | Exactly what we need, nothing more | 90% of features unused |
| Migration path | If custom engine hits limits, extract to Temporal | Schema and concepts translate cleanly |
| Time to first workflow | ~1 week | ~2-3 weeks (including infra setup) |

The custom engine is designed to be extractable. The `WorkflowState` interface and step executor pattern map directly to Temporal activities if we need to migrate later.

---

## Appendix B: Glossary

| Term | Definition |
|:-----|:-----------|
| **Workflow template** | A JSON definition of a multi-step execution plan, stored in Postgres |
| **Workflow execution** | A single run of a template with specific parameters |
| **Step** | One unit of work within a workflow (agent invocation, merge, transform, etc.) |
| **Fan-out** | Spawning N agents in parallel, one per item in a cartesian product |
| **Fan-in** | Collecting results from all agents in a fan-out (aggregate step) |
| **DAG** | Directed acyclic graph -- the execution order of steps, with no cycles |
| **Intent classification** | Mapping a user's natural language request to a template + parameters |
| **Two-state model** | Separation of deterministic orchestration (engine) from LLM reasoning (agents) |
| **Output-type routing** | Using `task_type` to decide whether an agent needs git or just a workspace |
