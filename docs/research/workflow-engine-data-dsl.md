# Workflow Engine Research: Data-Driven & DSL-Based Approaches for AI Agent Orchestration

**Date:** 2026-03-22
**Context:** Ditto Factory dispatches ephemeral Claude Code agents in K8s. We need workflow templates that non-developers can author, version, test, and manage through a platform API.

---

## 1. Database-Backed Workflow Templates

### n8n
- **Storage model:** Workflows stored as JSON documents in PostgreSQL (or SQLite for dev). Each workflow is a JSON blob with nodes, connections, and settings. The `workflow_entity` table stores `id`, `name`, `active`, `nodes` (JSON), `connections` (JSON), `settings`, `staticData`, `createdAt`, `updatedAt`.
- **Versioning:** No built-in version history in community edition. Enterprise adds workflow history with rollback. Community users export/import JSON files and use git.
- **Testing:** Manual execution with test data. No built-in unit testing framework for workflows. Debug via "Executions" list that records every run with input/output per node.
- **Authoring:** Visual canvas editor. Non-developers can build workflows by dragging nodes. Each node is a typed operation (HTTP Request, IF, Code, etc.).

### Zapier
- **Storage model:** Proprietary. Zaps are stored as ordered step lists (trigger + actions). Each step references an "app" and an "action" with field mappings. Internally likely a document store with step arrays.
- **Versioning:** Zap versions with draft/published states. No git-style branching. Rollback is limited to reverting to last published version.
- **Testing:** Per-step testing with sample data. Can test individual steps or run entire Zap with test trigger data.

### Windmill
- **Storage model:** Flows are JSON documents in the **OpenFlow** format (an open standard). Stored in PostgreSQL. The spec is formally defined:
  ```typescript
  type OpenFlow = {
    summary?: string;
    description?: string;
    value: FlowValue;   // modules[], failure_module, same_worker
    schema?: any;        // JSON Schema for inputs
  };
  type FlowValue = {
    modules: FlowModule[];
    failure_module?: FlowModule;
    same_worker: boolean;
  };
  type FlowModule = {
    value: Identity | RawScript | PathScript | ForloopFlow | BranchOne | BranchAll;
    summary?: string;
    stop_after_if?: { expr: string; skip_if_stopped: boolean };
    sleep?: StaticTransform | JavascriptTransform;
    suspend?: { required_events?: integer, timeout: integer };
    retry?: Retry;
  };
  ```
- **Versioning:** Git-based. Flows are stored as files, synced to git repos. Every deployment creates a version. Full rollback support.
- **Testing:** Dedicated flow testing UI. Can run individual steps or full flows with mock inputs. Supports approval steps (suspend/resume).
- **Key insight:** OpenFlow separates the *what* (declarative module graph) from the *how* (each module's code). Modules can be `RawScript` (inline code) or `PathScript` (reference to a versioned script). This is the closest existing model to what we need.

### Assessment for Ditto Factory

| Platform | Non-dev authoring | API CRUD | Versioning | Composability |
|----------|------------------|----------|------------|---------------|
| n8n | Strong (visual) | Yes (REST API) | Weak (enterprise only) | Limited (sub-workflows) |
| Zapier | Strong (wizard) | Limited (API is read-heavy) | Weak | None |
| Windmill | Moderate (low-code) | Yes (full REST API) | Strong (git-native) | Strong (PathScript refs) |

---

## 2. YAML/JSON DSL Approaches

### Argo Workflows
- **Definition format:** Kubernetes CRDs in YAML. A `Workflow` has a `spec` with `templates` and an `entrypoint`.
- **Template types (9 total):**
  - *Definitions* (do work): Container, Script, Resource, Suspend
  - *Invocators* (control flow): Steps (sequential + parallel lists), DAG (dependency graph), HTTP, Plugin
- **Composability:** `WorkflowTemplate` is a reusable library of templates. Workflows reference templates by name. `ClusterWorkflowTemplate` for cross-namespace sharing.
- **Key strengths:** DAG-based execution, artifact passing between steps, parameter substitution, conditional execution (`when:` clauses), retry policies per step.
- **Key weaknesses:** YAML verbosity. Complex workflows become 500+ line YAML files. No type checking at authoring time. Error messages are cryptic K8s controller errors.

### Tekton
- **Definition format:** K8s CRDs. Three-level hierarchy: Steps -> Tasks -> Pipelines.
- **Execution model:** Each Task runs as a K8s Pod; each Step is a container within that Pod. Pipelines create DAGs of Tasks.
- **Composability:** Tasks are independently versioned and reusable. Tekton Catalog provides a shared library. Pipelines compose Tasks.
- **Key insight:** The Step-as-container model maps directly to our ephemeral agent pattern. A "Task" in Tekton is conceptually similar to a single agent job in Ditto Factory.

### Concourse
- **Definition format:** YAML with three primitives: Resources (external state), Jobs (execution units), Tasks (containerized scripts).
- **Key differentiator:** Resources are first-class. A pipeline is defined by how resources flow between jobs. This "resource-centric" model is elegant but opinionated.
- **Weakness:** Steep learning curve. The resource abstraction is powerful but confusing for non-developers.

### Common DSL Pitfalls

1. **Turing-completeness creep:** Argo added expression evaluation, conditional logic, loops, and recursion. The YAML DSL became a programming language without IDE support, type checking, or debugging tools. Avoid this.
2. **Lack of type safety:** YAML parameter passing is stringly-typed. Template inputs/outputs have no schema validation at authoring time. Errors surface only at runtime.
3. **Portability illusion:** K8s CRDs tie you to Kubernetes. The workflow definition is not portable to other execution environments.
4. **Version management:** CRDs version with the cluster, not with git. Requires external tooling (Argo CD, Flux) for git-based versioning.

### Assessment for Ditto Factory

| Platform | Non-dev authoring | API CRUD | Versioning | Fit for agents |
|----------|------------------|----------|------------|----------------|
| Argo Workflows | Weak (raw YAML) | Via K8s API | CRD-based (needs GitOps) | Strong (K8s native) |
| Tekton | Weak (raw YAML) | Via K8s API | CRD + Catalog | Strong (Step=container) |
| Concourse | Weak (YAML) | REST API | Git pipeline config | Moderate |

---

## 3. State Machines as Workflows

### AWS Step Functions (Amazon States Language)
- **Definition format:** JSON-based ASL (Amazon States Language). Each workflow is a set of named states with explicit transitions.
- **State types:** Task (invoke a resource), Choice (conditional branching), Parallel (fan-out), Map (iterate), Wait (delay), Pass (transform data), Succeed, Fail.
- **Key strengths:**
  - Explicit state transitions make the workflow fully inspectable at any point
  - Built-in retry/catch per state with exponential backoff
  - Visual workflow designer in AWS Console
  - Express Workflows for high-throughput, Standard for long-running (up to 1 year)
  - JSONata/JSONPath for data transformation between states
- **Key weaknesses:**
  - Vendor lock-in to AWS
  - 256KB payload limit between states
  - No local execution (LocalStack provides partial emulation)
  - ASL is verbose for simple sequences

### XState (Statecharts)
- **Definition format:** JavaScript/TypeScript objects defining state machines with the actor model.
- **Key concepts:** States, events, transitions, guards (conditions), actions (side effects), actors (concurrent processes), context (extended state).
- **Key strengths:**
  - Formal statechart semantics (Harel statecharts) -- mathematically proven model
  - Visual editor (Stately Studio) for non-developers
  - Serializable machine definitions (JSON export)
  - Actor model allows hierarchical/parallel composition
  - Can be used server-side for workflow orchestration
- **Key weaknesses:**
  - Primarily a frontend state management tool; server-side workflow use is secondary
  - No built-in persistence/durable execution (must add your own)
  - TypeScript-only ecosystem

### State Machines vs DAGs for Agent Orchestration

| Concern | State Machine | DAG |
|---------|--------------|-----|
| **Explicit "where am I?"** | First-class (current state) | Requires tracking (which nodes completed) |
| **Conditional branching** | Native (Choice/guards) | Bolted on (when: clauses) |
| **Loops/retries** | Natural (transition back to earlier state) | Awkward (retry policies are metadata) |
| **Human-in-the-loop** | Natural (Wait state, suspend) | Possible but not native |
| **Agent autonomy** | Tension -- agents want to decide next steps, state machines want to prescribe them | Better fit -- DAG defines available paths, agent picks |
| **Composability** | Hierarchical (nested machines) | Graph composition (sub-DAGs) |
| **Non-dev readability** | High (visual state diagrams) | Moderate (DAG visualizations) |

**Verdict for Ditto Factory:** State machines are the better fit for our "two-state model" (deterministic orchestration vs agent reasoning). A state machine cleanly separates "the platform decides what happens next" from "the agent decides how to accomplish this step." Each Task state invokes an agent; the state machine handles transitions, error recovery, and human approval gates.

---

## 4. Hybrid Approaches (Data + Code)

### Inngest
- **Model:** Functions defined in code (TypeScript/Python) but triggered and orchestrated by the Inngest platform. Each function has:
  - **Triggers:** Events, cron schedules, or webhooks
  - **Flow control:** Concurrency limits, throttling, debounce, rate limiting
  - **Steps:** Retriable checkpoints within a function. Each `step.run()` is durably executed -- if it succeeds, the result is memoized. If the function crashes, it resumes from the last completed step.
- **Durable execution:** The Inngest engine replays functions from the last checkpoint. Steps are the unit of retry/recovery.
- **Key insight:** Steps are code, but the orchestration (retry, fan-out, wait-for-event) is platform-managed. This is "workflow-as-code" with infrastructure-level durability. Non-developers cannot author Inngest functions (they are TypeScript/Python code).

### Retool Workflows
- **Model:** Visual canvas where each block is either a query (SQL, REST, GraphQL), code (JavaScript), AI action, or control flow (branch, loop).
- **Triggers:** Cron or webhook/API call.
- **Execution:** Durable -- each block's result is persisted. Failed workflows can be inspected block-by-block.
- **Authoring:** Low-code visual editor. Non-developers can build workflows by connecting blocks. Code blocks allow escape hatches for developers.
- **Weakness:** Closed-source, SaaS-only. Cannot self-host.

### The Hybrid Pattern (Generalized)

The most successful hybrid systems share this architecture:

```
┌─────────────────────────────────────────────┐
│           Workflow Definition (Data)         │
│  Stored in DB, editable via API/UI          │
│  - Step ordering and dependencies (DAG)     │
│  - Conditional routing rules                │
│  - Retry/timeout policies                   │
│  - Input/output schemas                     │
│  - Human approval gates                     │
├─────────────────────────────────────────────┤
│           Step Implementation (Code)         │
│  Referenced by path/ID from the definition  │
│  - Versioned independently                  │
│  - Can be shared across workflows           │
│  - Typed inputs/outputs                     │
│  - Testable in isolation                    │
└─────────────────────────────────────────────┘
```

**Windmill's OpenFlow** is the best open-source example of this pattern. The workflow definition is a JSON document (data), but each module references either inline code (`RawScript`) or a versioned script (`PathScript`). The platform handles execution, retry, and data passing.

### Assessment for Ditto Factory

| Platform | Non-dev authoring | API CRUD | Versioning | Two-state separation |
|----------|------------------|----------|------------|---------------------|
| Inngest | No (code-only) | Yes (dashboard) | Git (code) | Weak (code = orchestration) |
| Retool | Strong (visual) | Limited | Weak | Strong (visual + code blocks) |
| Windmill | Moderate | Yes (full API) | Strong (git) | Strong (OpenFlow + PathScript) |

---

## 5. AI Agent-Specific Workflow Systems

### CrewAI Flows
- **Definition format:** Python classes with decorator-based routing.
  ```python
  class MyFlow(Flow[MyState]):
      @start()
      def begin(self): ...

      @router(begin)
      def route(self):
          return "success" if self.state.ok else "failed"

      @listen("success")
      def handle_success(self): ...
  ```
- **State management:** Pydantic BaseModel or unstructured dict. State is shared across all methods. Supports persistence (SQLite) for recovery.
- **Composability:** Flows can contain Crews (teams of agents). A Flow orchestrates the deterministic parts; Crews handle the agent-reasoning parts.
- **Key insight:** CrewAI explicitly separates "flow" (deterministic routing) from "crew" (agent autonomy). This is exactly our two-state model. However, flows are Python code, not data -- non-developers cannot author them.

### AutoGen Teams
- **Definition format:** Python code composing agent teams.
- **Team types:**
  - `RoundRobinGroupChat`: Agents take turns in sequence
  - `SelectorGroupChat`: An LLM selects the next speaker
  - `MagenticOneGroupChat`: Generalist multi-agent system
  - `Swarm`: Agents use `HandoffMessage` to pass control
- **Termination:** Configurable conditions (text mention, max messages, external signal, token limit).
- **Key weakness:** No workflow definition format. Teams are composed in Python code. No persistence, no versioning, no API management. Purely a library, not a platform.

### OpenAI Swarm
- **Model:** Lightweight agent handoff framework. Agents define `instructions` and `functions`. Functions can return an `Agent` to hand off control.
- **Key insight:** Minimal orchestration. The "workflow" is emergent from agent handoffs, not prescribed. Good for exploratory tasks; terrible for predictable, auditable pipelines.

### Assessment for Ditto Factory

| System | Non-dev authoring | Deterministic control | Agent autonomy | Platform-ready |
|--------|------------------|----------------------|----------------|----------------|
| CrewAI Flows | No (Python) | Strong (@router) | Strong (Crews) | Moderate (persistence exists) |
| AutoGen | No (Python) | Moderate (team types) | Strong | Weak (library only) |
| OpenAI Swarm | No (Python) | Weak (emergent) | Very strong | Weak (no persistence) |

---

## 6. Comparative Evaluation Matrix

### Evaluation Criteria Across All Approaches

| Criterion | DB-Backed (Windmill) | YAML DSL (Argo) | State Machine (Step Functions) | Hybrid (Inngest) | AI-Agent (CrewAI) |
|-----------|---------------------|-----------------|-------------------------------|-------------------|-------------------|
| **Non-dev authoring** | Moderate (low-code UI) | Weak (YAML) | Moderate (visual designer) | Weak (code) | Weak (Python) |
| **API CRUD** | Full REST API | K8s API | AWS API | REST API | None |
| **Versioning + rollback** | Git-native | CRD + GitOps | Versioned aliases | Git (code) | Git (code) |
| **A/B testing** | Possible via API | Not native | Versioned aliases enable this | Not native | Not native |
| **Composability** | Strong (PathScript refs) | Strong (WorkflowTemplate) | Moderate (nested state machines) | Moderate (fan-out steps) | Strong (Flow + Crew) |
| **Observability** | Step-level execution logs | Pod logs + Argo UI | Built-in execution history | Step-level with memoization | Limited |
| **Migration path** | Start simple, add complexity | Start YAML, stays YAML | Start simple, states grow | Start simple functions | Start Crew, add Flow |
| **Two-state model fit** | Strong (data=orchestration, code=steps) | Moderate (DAG=orchestration, container=work) | Strong (state machine=orchestration, Task=agent) | Weak (code is both) | Strong (Flow=orchestration, Crew=agent) |

---

## 7. Recommendations for Ditto Factory

### Primary Recommendation: Hybrid State Machine + Data Store

Combine the best patterns observed across the research:

1. **Workflow definitions as JSON documents in PostgreSQL** (like Windmill's OpenFlow), managed via REST API with full CRUD.
2. **State machine semantics** (like Step Functions ASL) for workflow execution, providing explicit "where am I?" at all times.
3. **Steps reference agent configurations by ID** (like Windmill's PathScript), not inline code. Each step says "run agent type X with these inputs" rather than containing the agent logic.
4. **Versioning with immutable snapshots** -- each workflow edit creates a new version. Running workflows pin to a version. Rollback = activate an older version.

### Proposed Workflow Definition Schema (Sketch)

```json
{
  "id": "wf_review_and_deploy",
  "version": 3,
  "name": "Code Review and Deploy",
  "description": "Reviews a PR, runs tests, deploys if approved",
  "input_schema": {
    "type": "object",
    "properties": {
      "pr_url": { "type": "string" },
      "repo": { "type": "string" }
    },
    "required": ["pr_url", "repo"]
  },
  "states": {
    "review": {
      "type": "agent_task",
      "agent_type": "code-reviewer",
      "input": { "pr_url": "$.input.pr_url", "repo": "$.input.repo" },
      "timeout_seconds": 600,
      "retry": { "max_attempts": 2, "backoff": "exponential" },
      "on_success": "check_approval",
      "on_failure": "notify_failure"
    },
    "check_approval": {
      "type": "choice",
      "choices": [
        { "condition": "$.review.output.approved == true", "next": "run_tests" },
        { "condition": "$.review.output.approved == false", "next": "request_changes" }
      ],
      "default": "request_changes"
    },
    "run_tests": {
      "type": "agent_task",
      "agent_type": "test-runner",
      "input": { "repo": "$.input.repo", "branch": "$.review.output.branch" },
      "timeout_seconds": 900,
      "on_success": "deploy",
      "on_failure": "notify_failure"
    },
    "request_changes": {
      "type": "agent_task",
      "agent_type": "code-reviewer",
      "input": { "pr_url": "$.input.pr_url", "action": "request_changes", "comments": "$.review.output.comments" },
      "on_success": "wait_for_update",
      "on_failure": "notify_failure"
    },
    "wait_for_update": {
      "type": "wait",
      "event": "pr_updated",
      "timeout_seconds": 86400,
      "on_event": "review",
      "on_timeout": "notify_timeout"
    },
    "deploy": {
      "type": "agent_task",
      "agent_type": "deployer",
      "input": { "repo": "$.input.repo", "branch": "$.review.output.branch" },
      "on_success": "success",
      "on_failure": "notify_failure"
    },
    "success": { "type": "succeed" },
    "notify_failure": { "type": "fail", "cause": "Step failed" },
    "notify_timeout": { "type": "fail", "cause": "Timed out waiting for PR update" }
  },
  "start_at": "review"
}
```

### Why This Approach

| Design choice | Inspiration | Trade-off |
|---------------|-------------|-----------|
| JSON in PostgreSQL | Windmill OpenFlow | Gives up visual editor (for now), gains API-first management |
| State machine semantics | AWS Step Functions | Gives up DAG parallelism (add later), gains explicit state tracking |
| Agent type references | Windmill PathScript | Gives up inline flexibility, gains versioned separation of concerns |
| Immutable versions | Windmill git sync | Gives up live editing of running workflows, gains auditability |
| JSONPath expressions | Step Functions ASL | Gives up Turing-completeness, gains predictability and validation |

### What We Explicitly Avoid

1. **Turing-complete DSL** -- No loops, no arbitrary code in the workflow definition. Workflow definitions are data, not programs. Agent steps handle the complexity.
2. **YAML** -- JSON is more API-friendly, has better schema validation tooling, and avoids YAML's footguns (Norway problem, implicit type coercion).
3. **Code-first workflow definition** -- CrewAI and Inngest are powerful but require Python/TS to author. Our target users (team leads) should not need to write code.
4. **K8s CRDs as the workflow format** -- Argo/Tekton tie workflow definitions to the cluster. We want workflow definitions portable across environments (dev laptop with Docker Compose, staging K8s, production K8s).

### Migration Path

1. **Month 1:** Simple sequential workflows (linear chain of agent steps). No branching, no loops. Just: step A -> step B -> step C.
2. **Month 2:** Add Choice states for conditional routing. Add Wait states for human-in-the-loop approval and external events.
3. **Month 3:** Add Parallel states for fan-out/fan-in. Add sub-workflow references for composability.
4. **Month 4+:** Visual workflow editor (optional). A/B testing with version aliases. Workflow analytics and optimization.

---

## Appendix A: Key Sources

| Source | URL | What we learned |
|--------|-----|-----------------|
| Windmill OpenFlow | https://www.windmill.dev/docs/openflow | Best open-source workflow-as-data format |
| AWS Step Functions ASL | https://states-language.net/spec.html | State machine DSL design |
| Argo Workflows | https://argo-workflows.readthedocs.io/ | K8s-native DAG workflows, template composition |
| Tekton | https://tekton.dev/docs/concepts/ | Step-as-container model |
| CrewAI Flows | https://docs.crewai.com/concepts/flows | Two-state model (Flow + Crew) |
| AutoGen Teams | https://microsoft.github.io/autogen/ | Multi-agent team patterns |
| Inngest | https://www.inngest.com/docs/ | Durable execution, step memoization |
| XState | https://stately.ai/docs/xstate | Statechart formalism, actor model |
| n8n | https://docs.n8n.io/workflows/ | Visual workflow authoring, JSON storage |

## Appendix B: Glossary

| Term | Definition |
|------|------------|
| **DAG** | Directed Acyclic Graph -- a workflow where steps have dependencies but no cycles |
| **ASL** | Amazon States Language -- JSON DSL for AWS Step Functions |
| **OpenFlow** | Windmill's open standard for defining workflow DAGs as JSON |
| **Durable execution** | Execution model where step results are persisted, enabling resume-from-checkpoint |
| **Two-state model** | Architecture separating deterministic orchestration from non-deterministic agent reasoning |
| **PathScript** | Windmill concept: a workflow step that references versioned code by path instead of inline |
| **Statechart** | Extended state machine formalism by David Harel supporting hierarchy, parallelism, and history |
