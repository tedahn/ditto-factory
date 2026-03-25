# Code-Based Workflow Engines for AI Agent Orchestration

**Date**: 2026-03-22
**Status**: Research
**Context**: Ditto Factory dispatches ephemeral Claude Code agents in K8s. We need a deterministic workflow layer that controls WHAT agents do (steps, parallelism, branching) while agents only handle HOW (the actual coding work).

---

## Executive Summary

We evaluated five approaches for workflow orchestration: Temporal.io, Prefect/Dagster, LangGraph, Argo Workflows/GitHub Actions, and a custom Python engine. **For agent orchestration with long-running, heterogeneous tasks, Temporal.io is the strongest fit**, but a custom Python engine offers the best simplicity-to-power ratio for our specific use case. The recommendation is to start with a custom engine and migrate to Temporal if/when operational complexity demands it.

---

## 1. Temporal.io

### How It Works

Temporal implements "workflow-as-code" -- workflows are regular Python async functions decorated with `@workflow.defn` and `@workflow.run`. Activities (side-effectful operations) are separate functions decorated with `@activity.defn`. The Temporal runtime replays workflow function executions deterministically from an event history, so workflow code must be deterministic (no threading, no randomness, no I/O, no system time).

```python
@workflow.defn
class AgentWorkflow:
    @workflow.run
    async def run(self, input: WorkflowInput) -> WorkflowResult:
        # Parallel agent dispatch
        results = await asyncio.gather(
            workflow.execute_activity(run_agent, agent_a_input, start_to_close_timeout=timedelta(minutes=30)),
            workflow.execute_activity(run_agent, agent_b_input, start_to_close_timeout=timedelta(minutes=30)),
        )
        # Branching based on results
        if results[0].needs_review:
            await workflow.execute_child_workflow(ReviewWorkflow.run, results[0])
        return WorkflowResult(results)
```

### Evaluation

| Dimension | Assessment |
|-----------|------------|
| **Authoring** | Excellent. Pure Python, full IDE support, type-safe. Workflows read like normal async code. The determinism constraints (no I/O in workflow code) require discipline but map well to our "workflow controls WHAT, agent handles HOW" separation. |
| **Versioning** | Strong. Two mechanisms: (1) Worker Versioning -- tag workers with deployment versions, pin workflows to specific worker versions. (2) Patching -- `workflow.patched("my-change-id")` creates logical branches for in-flight vs new executions. Can run v1 and v2 side by side. |
| **Testing** | Good. Workflows can be tested with `WorkflowEnvironment` which provides a test server. Activities can be mocked. Time can be skipped. Not pure unit tests -- requires the test environment runtime. |
| **Parallelism** | Native. `asyncio.gather()` for parallel activities, `start_child_workflow()` for fan-out to child workflows. Parent Close Policies control child lifecycle. |
| **State** | Managed by Temporal Server. Event history is the source of truth. Replayed on worker restart. Durable across failures. State size limit: ~50K events per workflow (mitigated with Continue-As-New). |
| **Failure handling** | Best-in-class. Per-activity RetryPolicy (backoff, max attempts, non-retryable error types). Activity heartbeats for long-running tasks. Workflow-level timeouts. Saga pattern via compensation activities. |
| **Ops overhead** | **High.** Requires Temporal Server (or Temporal Cloud SaaS). Server needs PostgreSQL/MySQL/Cassandra + Elasticsearch. Workers are separate processes. Monitoring via Temporal Web UI. |
| **Agent fit** | **Excellent.** Designed for long-running, heterogeneous tasks. Activities can run for hours. Heartbeats detect stuck agents. Child workflows model complex agent pipelines naturally. |

### Trade-offs

- **Gaining**: Battle-tested durability, replay-based fault tolerance, production-grade observability
- **Giving up**: Operational simplicity (another stateful service to run), determinism constraints require careful coding, learning curve for the replay model

---

## 2. Prefect / Dagster

### How They Work

Both are Python-native workflow engines, originally built for data pipelines but increasingly general-purpose.

**Prefect** uses `@flow` and `@task` decorators. Tasks are the unit of retry/caching. Parallelism via `.map()` and `.submit()`. State tracked in Prefect Server (or Prefect Cloud).

```python
@task(retries=3, retry_delay_seconds=10)
def run_agent(config: AgentConfig) -> AgentResult:
    # dispatch agent, wait for completion
    ...

@flow
def multi_agent_workflow(inputs: list[AgentConfig]) -> list[AgentResult]:
    results = run_agent.map(inputs)  # parallel execution
    return [r.result() for r in results]
```

**Dagster** uses an asset-centric model (`@asset`, `@op`, `@job`). Assets represent data artifacts. Ops are computation units. The asset graph defines dependencies. Strong emphasis on data lineage and observability.

```python
@dg.asset
def agent_output(context: dg.AssetExecutionContext) -> str:
    # Run agent, return artifact
    ...

@dg.asset(deps=[agent_output])
def reviewed_output(agent_output) -> str:
    # Run review agent on previous output
    ...
```

### Evaluation

| Dimension | Assessment |
|-----------|------------|
| **Authoring** | Excellent. Pure Python decorators. Prefect feels more like "enhanced Python functions." Dagster is more opinionated with its asset model. Both have strong IDE support. |
| **Versioning** | Weak-to-moderate. Prefect: deployments can be versioned, but no built-in side-by-side execution of workflow versions. Dagster: code locations provide some versioning, but primarily designed for "latest code wins." |
| **Testing** | Good. Both support unit testing of individual tasks/ops without the runtime. Dagster has `materialize_to_memory()` for testing assets in isolation. Prefect tasks are just functions when called outside a flow context. |
| **Parallelism** | Good. Prefect: `.map()` for fan-out, `.submit()` for concurrent tasks, task runners (Dask, Ray) for distributed execution. Dagster: asset dependencies define implicit parallelism; ops within a job run in parallel when dependencies allow. |
| **State** | Prefect: task run states (Pending, Running, Completed, Failed, etc.) stored in Prefect Server DB. Dagster: asset materializations stored in the Dagster instance DB (SQLite or PostgreSQL). |
| **Failure handling** | Moderate. Both support retries with configurable delays. Prefect has `@task(retries=N)`. Dagster has `RetryPolicy` with exponential backoff. Neither has Temporal-level compensation/saga support. No heartbeat mechanism for long-running tasks. |
| **Ops overhead** | Moderate. Prefect: Prefect Server (or Cloud) + workers. Dagster: Dagster webserver/daemon + code locations. Both lighter than Temporal but still require server infrastructure. |
| **Agent fit** | **Moderate.** Both are optimized for data pipeline patterns (short tasks, data transforms). Long-running agent tasks (30+ minutes) are not their sweet spot. No built-in heartbeat for detecting stuck agents. Prefect's task runner model assumes tasks complete in seconds-to-minutes. |

### Trade-offs

- **Gaining**: Familiar Python patterns, good observability UIs, strong data pipeline ecosystem
- **Giving up**: Not designed for long-running heterogeneous tasks, weak versioning for side-by-side execution, no saga/compensation patterns

---

## 3. LangGraph

### How It Works

LangGraph models workflows as state machines (directed graphs). Nodes are functions that transform state. Edges define transitions (can be conditional). State is a typed dictionary (typically using `TypedDict` or Pydantic models) that flows through the graph.

```python
from langgraph.graph import StateGraph, START, END

class AgentState(TypedDict):
    task: str
    code: str
    review: str
    status: str

def code_agent(state: AgentState) -> dict:
    # LLM call to generate code
    return {"code": generated_code, "status": "coded"}

def review_agent(state: AgentState) -> dict:
    # LLM call to review code
    return {"review": review_result, "status": "reviewed"}

def route(state: AgentState) -> str:
    if state["status"] == "reviewed" and "LGTM" in state["review"]:
        return "done"
    return "revise"

graph = StateGraph(AgentState)
graph.add_node("code", code_agent)
graph.add_node("review", review_agent)
graph.add_edge(START, "code")
graph.add_edge("code", "review")
graph.add_conditional_edges("review", route, {"done": END, "revise": "code"})
app = graph.compile()
```

### Evaluation

| Dimension | Assessment |
|-----------|------------|
| **Authoring** | Good. Graph-based mental model is intuitive for agent workflows. Conditional edges handle branching naturally. However, complex workflows become hard to read as graphs grow. No native support for "steps" -- everything is a node. |
| **Versioning** | Weak. No built-in workflow versioning. Graphs are compiled at import time. Version management is left to the developer (different graph definitions, feature flags). |
| **Testing** | Good. Nodes are pure functions (state in, state out). Easy to unit test individual nodes. The compiled graph can be invoked directly with test state. No runtime dependency needed for testing. |
| **Parallelism** | Limited. LangGraph supports parallel node execution via `Send()` API for fan-out patterns, but it is not as natural as `asyncio.gather()`. Subgraphs provide some composability. |
| **State** | Graph state is a dictionary passed between nodes. Persistence via checkpointers (SQLite, PostgreSQL). Supports "time travel" -- rewind to any checkpoint. State is scoped per thread/conversation. |
| **Failure handling** | Basic. No built-in retry policies. No heartbeats. Interrupts allow human-in-the-loop patterns. Error handling is manual (try/except in nodes). |
| **Ops overhead** | Low for basic use. Library only, no server needed. LangGraph Platform (commercial) adds deployment, scaling, and monitoring. |
| **Agent fit** | **Good for LLM-centric workflows, poor for infrastructure orchestration.** Designed specifically for LLM agent loops (tool calling, reflection, multi-agent). But it couples tightly to LangChain ecosystem and assumes agents are LLM calls, not ephemeral K8s pods. |

### Trade-offs

- **Gaining**: Purpose-built for agent patterns, good state management with checkpointing, visual graph representation
- **Giving up**: Tight coupling to LangChain, weak parallelism, no production-grade failure handling, no workflow versioning

---

## 4. Argo Workflows / GitHub Actions

### How They Work

Both use YAML-based workflow definitions. Argo Workflows is Kubernetes-native (CRD-based). GitHub Actions is CI/CD-focused but increasingly used for general automation.

**Argo Workflows**: Each step is a container. Supports DAG and step-based workflows. Parallel execution via DAG dependencies or nested step lists.

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Workflow
spec:
  entrypoint: agent-pipeline
  templates:
  - name: agent-pipeline
    dag:
      tasks:
      - name: code-agent
        template: run-agent
        arguments:
          parameters: [{name: task, value: "implement-feature"}]
      - name: test-agent
        template: run-agent
        dependencies: [code-agent]
        arguments:
          parameters: [{name: task, value: "write-tests"}]
      - name: review-agent
        template: run-agent
        dependencies: [code-agent]
        arguments:
          parameters: [{name: task, value: "review-code"}]
  - name: run-agent
    container:
      image: ditto-agent:latest
      command: [python, -m, agent]
```

**GitHub Actions**: YAML workflows triggered by events. Jobs run in parallel by default; `needs` keyword defines dependencies.

### Evaluation

| Dimension | Assessment |
|-----------|------------|
| **Authoring** | Poor for complex logic. YAML is verbose and error-prone for branching/conditionals. No IDE type-checking. Argo templates help with reuse but add indirection. GitHub Actions has a better marketplace ecosystem but same YAML limitations. |
| **Versioning** | Argo: WorkflowTemplates can be versioned as K8s resources. GitHub Actions: workflows versioned with git (branch/tag refs for action versions). Both support side-by-side via different template/workflow versions. |
| **Testing** | Poor. YAML workflows cannot be unit tested. Argo has no test framework. GitHub Actions has `act` for local testing but it is limited. Integration testing only. |
| **Parallelism** | Good. Argo: DAG tasks run in parallel when dependencies allow; Steps inner lists run in parallel. GitHub Actions: jobs run in parallel by default. Both handle fan-out well. |
| **State** | Argo: workflow state is the Workflow CRD status in K8s etcd. Parameters and artifacts pass data between steps. GitHub Actions: job outputs and artifacts. Neither has rich state management. |
| **Failure handling** | Moderate. Argo: `retryStrategy` per template, `activeDeadlineSeconds` for timeouts. GitHub Actions: `timeout-minutes`, limited retry (via marketplace actions). Neither has saga/compensation. |
| **Ops overhead** | Argo: requires Argo controller in K8s cluster (we already run K8s, so incremental cost is low). GitHub Actions: SaaS, no infra overhead but runner costs. |
| **Agent fit** | **Argo: Good for container orchestration, poor for dynamic workflows.** Each agent step is naturally a container. But dynamic branching (e.g., "if review fails, loop back") is awkward in YAML. GitHub Actions is too CI/CD-focused. |

### Trade-offs

- **Gaining**: Native K8s integration (Argo), container-per-step model matches our agent model
- **Giving up**: YAML authoring pain, poor testability, limited dynamic branching, no code-level composability

---

## 5. Custom Python Engine

### How It Would Work

A lightweight workflow engine built on Python async primitives and a state machine pattern. No external runtime dependency.

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable
import asyncio

class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"

@dataclass
class Step:
    name: str
    handler: Callable[..., Awaitable[Any]]
    depends_on: list[str] = field(default_factory=list)
    retry_policy: dict = field(default_factory=lambda: {"max_retries": 3, "backoff": 2.0})
    timeout_seconds: int = 600
    condition: Callable[[dict], bool] | None = None  # skip if returns False

@dataclass
class WorkflowDefinition:
    name: str
    version: str
    steps: list[Step]

class WorkflowEngine:
    def __init__(self, state_store: StateStore):
        self.state_store = state_store

    async def execute(self, workflow: WorkflowDefinition, context: dict) -> dict:
        state = await self.state_store.load_or_create(workflow.name, context)

        # Build dependency graph, execute steps with topological sort
        ready = self._get_ready_steps(workflow.steps, state)
        while ready:
            # Run all ready steps in parallel
            results = await asyncio.gather(
                *[self._execute_step(step, state) for step in ready],
                return_exceptions=True
            )
            for step, result in zip(ready, results):
                if isinstance(result, Exception):
                    state = await self._handle_failure(step, result, state)
                else:
                    state.step_results[step.name] = result
                    state.step_statuses[step.name] = StepStatus.COMPLETED
            await self.state_store.save(state)
            ready = self._get_ready_steps(workflow.steps, state)

        return state.step_results

    async def _execute_step(self, step: Step, state: WorkflowState) -> Any:
        if step.condition and not step.condition(state.step_results):
            state.step_statuses[step.name] = StepStatus.SKIPPED
            return None
        state.step_statuses[step.name] = StepStatus.RUNNING
        return await asyncio.wait_for(
            step.handler(state.step_results),
            timeout=step.timeout_seconds
        )
```

### Evaluation

| Dimension | Assessment |
|-----------|------------|
| **Authoring** | Excellent. Pure Python, full IDE support, no framework learning curve. Workflows are data structures (list of Steps), not DSL. Easy to generate programmatically. |
| **Versioning** | Manual but flexible. Version is a field on WorkflowDefinition. Side-by-side execution is trivial -- different WorkflowDefinition instances. No built-in migration for in-flight workflows (but our agents are ephemeral, so this matters less). |
| **Testing** | Excellent. Steps are plain async functions. WorkflowDefinition is a dataclass. Can unit test everything without any runtime. Mock the StateStore for integration tests. |
| **Parallelism** | Native. `asyncio.gather()` for steps whose dependencies are met. DAG-based scheduling is simple to implement (~50 lines of topological sort). |
| **State** | Developer-controlled. StateStore interface can be backed by Redis (we already use it), PostgreSQL, or even filesystem. State schema is explicit and versioned. |
| **Failure handling** | Build what we need. Retry with backoff (simple loop). Timeouts via `asyncio.wait_for()`. Compensation requires explicit implementation. No replay-based recovery (unlike Temporal). |
| **Ops overhead** | **Minimal.** No additional infrastructure. Runs in-process with the orchestrator. State store is the only dependency (Redis, which we already have). |
| **Agent fit** | **Good.** We design it for our exact use case. Steps map directly to agent dispatches. But we own all the complexity: failure modes, edge cases, observability. |

### Trade-offs

- **Gaining**: Zero operational overhead, perfect fit for our model, full control, easy to test
- **Giving up**: We own all edge cases (network partitions, partial failures, state corruption). No replay. No built-in observability. Engineering time to build and maintain.

---

## Comparison Matrix

| Dimension | Temporal | Prefect/Dagster | LangGraph | Argo/GHA | Custom Python |
|-----------|----------|-----------------|-----------|----------|---------------|
| Authoring | ★★★★★ | ★★★★★ | ★★★★ | ★★ | ★★★★★ |
| Versioning | ★★★★★ | ★★★ | ★★ | ★★★ | ★★★ |
| Testing | ★★★★ | ★★★★ | ★★★★★ | ★★ | ★★★★★ |
| Parallelism | ★★★★★ | ★★★★ | ★★★ | ★★★★ | ★★★★ |
| State mgmt | ★★★★★ | ★★★★ | ★★★★ | ★★ | ★★★ |
| Failure handling | ★★★★★ | ★★★ | ★★ | ★★★ | ★★★ |
| Ops overhead | ★★ | ★★★ | ★★★★★ | ★★★ | ★★★★★ |
| Agent fit | ★★★★★ | ★★★ | ★★★ | ★★★★ | ★★★★ |

---

## Recommendation

### Phase 1: Custom Python Engine (Now)

Start with a custom workflow engine. Rationale:

1. **Our agents are ephemeral** -- we do not need Temporal's replay-based recovery for long-lived workflows. Each workflow run dispatches agents that run for minutes-to-hours, then complete.
2. **We already have Redis** -- state persistence is solved without new infrastructure.
3. **Testability is paramount** -- we want to unit test workflow logic without spinning up a workflow server.
4. **We control the abstraction** -- we can design the Step interface to map directly to our agent dispatch model (K8s pod creation, status polling, result collection).

The custom engine should implement:
- DAG-based step scheduling with `asyncio.gather()`
- Redis-backed state persistence
- Configurable retry policies per step
- Step conditions for branching
- Timeout enforcement
- Event emission for observability (structured logs + metrics)

### Phase 2: Evaluate Temporal (When Needed)

Migrate to Temporal when ANY of these conditions are met:
- Workflows become multi-day with complex compensation requirements
- We need deterministic replay for debugging production failures
- Multiple teams need to compose workflows independently
- We hit edge cases in our custom engine faster than we can fix them

### Why Not the Others?

- **Prefect/Dagster**: Data pipeline tools. Wrong abstraction for agent orchestration.
- **LangGraph**: Couples us to LangChain ecosystem. Our agents are Claude Code in K8s pods, not LangChain tool-calling loops.
- **Argo Workflows**: Strong K8s integration but YAML authoring and poor testability make iteration slow. Could be a good complement (Argo for pod orchestration, our engine for workflow logic) but adds complexity.
- **GitHub Actions**: CI/CD tool, not a workflow engine.

---

## ADR-001: Workflow Engine Selection

### Status
Proposed

### Context
Ditto Factory needs a deterministic workflow layer to orchestrate ephemeral Claude Code agents. The workflow layer controls WHAT agents do (step ordering, parallelism, branching, retries), while agents handle HOW (the actual coding work). We need strong testability, low operational overhead, and support for parallel step execution.

### Decision
Start with a custom Python workflow engine backed by Redis state storage. Design the Step abstraction to map directly to agent pod dispatch. Plan a migration path to Temporal.io if workflow complexity exceeds what the custom engine can handle.

### Consequences
- **Easier**: Testing, debugging, deploying (no new infrastructure), iterating on the workflow model
- **Harder**: Handling exotic failure modes (network partitions during state writes), building observability from scratch, convincing future team members that "not invented here" was the right call
