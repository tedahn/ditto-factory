# Architecture Pattern: Agent Type to Result Type

| Field       | Value                          |
|-------------|--------------------------------|
| **Date**    | 2026-03-25                     |
| **Origin**  | [ADR-001: Generalized Task Agents](../adr/001-generalized-task-agents.md) |
| **Status**  | Draft                          |

## Pattern Name

**Agent Type → Result Type Dispatch**

## Problem

A platform that orchestrates autonomous agents needs to handle tasks that produce fundamentally different kinds of output — code changes, database mutations, file artifacts, API calls, analytical reports. Hardcoding the platform around a single output type (e.g., pull requests) limits utility, while making every component aware of every output type creates coupling and complexity.

## Solution

Decouple the **task lifecycle** (receive, classify, spawn, execute, validate, report) from the **result handling** by introducing two orthogonal type dimensions:

```
TaskType   — What the agent is asked to do (its mission)
ResultType — What the agent concretely produces (its deliverable)
```

The orchestrator owns the lifecycle. Result-type-specific behavior is isolated into pluggable handlers for validation, storage, and reporting.

## Structure

```
                    ┌──────────────────────┐
                    │    Task Lifecycle     │
                    │  (type-agnostic)      │
                    │                      │
                    │  Receive             │
                    │  Classify            │
                    │  Spawn               │
                    │  Execute             │
                    │  ◆ Validate ─────────┼──► ResultValidator (per ResultType)
                    │  ◆ Store ────────────┼──► ArtifactStore  (per ResultType)
                    │  ◆ Report ───────────┼──► Reporter       (per ResultType)
                    └──────────────────────┘
```

### Core Abstractions

```python
# The task declares its type up front
class TaskRequest:
    task_type: TaskType          # CODE_CHANGE, DB_MUTATION, FILE_OUTPUT, ...
    # ... common fields ...

# The result carries its type + polymorphic payload
class AgentResult:
    result_type: ResultType      # PULL_REQUEST, DB_ROWS, FILE_ARTIFACT, ...
    artifacts: list[Artifact]    # Type-specific payloads
    # ... common fields ...

# Pluggable per-result-type handlers
class ResultValidator(Protocol):
    async def validate(self, result: AgentResult) -> ValidationOutcome: ...

class ArtifactStore(Protocol):
    async def store(self, result: AgentResult) -> StorageReference: ...

class ResultReporter(Protocol):
    async def report(self, result: AgentResult, origin: TaskOrigin) -> None: ...
```

### Dispatch Table

The orchestrator uses a registry to dispatch to the correct handler:

```python
VALIDATORS: dict[ResultType, ResultValidator] = {
    ResultType.PULL_REQUEST:  PRValidator(),
    ResultType.DB_ROWS:       DBMutationValidator(),
    ResultType.FILE_ARTIFACT: FileArtifactValidator(),
    ResultType.API_RESPONSE:  APIResponseValidator(),
    ResultType.REPORT:        ReportValidator(),
}

async def complete_task(result: AgentResult):
    validator = VALIDATORS[result.result_type]
    outcome = await validator.validate(result)
    if outcome.approved:
        await STORES[result.result_type].store(result)
        await REPORTERS[result.result_type].report(result, origin)
```

## Key Properties

### 1. Lifecycle remains singular
There is one orchestration loop. Adding a new result type does not fork the control flow — it only requires registering new handler implementations.

### 2. TaskType and ResultType are orthogonal
A `DB_MUTATION` task might produce both a `DB_ROWS` result and a `REPORT` result. A `CODE_CHANGE` task produces a `PULL_REQUEST`. The mapping is N:M, not 1:1.

### 3. Safety scales per result type
Each result type defines its own validation contract:

| ResultType      | Safety Model |
|-----------------|-------------|
| `PULL_REQUEST`  | Git-native: branch diff, PR review, revert |
| `DB_ROWS`       | Dry-run → human approval → execute. Idempotency required. |
| `FILE_ARTIFACT` | Schema validation, size limits, checksum |
| `API_RESPONSE`  | Dry-run where supported, status validation, rollback capture |
| `REPORT`        | Completeness checks. No side effects. |

### 4. Reversibility is a first-class concern
Each handler declares a `ReversibilityLevel`:

```python
class ReversibilityLevel(str, Enum):
    TRIVIAL    = "trivial"     # git revert, delete file
    POSSIBLE   = "possible"    # DB rollback if within window
    DIFFICULT  = "difficult"   # API side effects, partial rollback
    IMPOSSIBLE = "impossible"  # External notifications sent, money moved
```

The approval gate strictness scales with reversibility level. `TRIVIAL` can auto-execute; `IMPOSSIBLE` requires explicit human approval with a confirmation code.

## When to Use

- Your platform orchestrates agents that produce **heterogeneous outputs**
- You want to **add new output types without modifying the core lifecycle**
- Different outputs need **different safety/validation models**
- You need a **consistent interaction model** (Slack, GitHub, etc.) regardless of what the agent does

## When NOT to Use

- All agents produce the same output type — the dispatch overhead is unnecessary
- The platform is a thin wrapper around a single tool — over-engineering
- You don't need human-in-the-loop approval — the safety layer is the main value of the pattern

## Related Patterns

- **Strategy Pattern** — The per-result-type handlers are strategies selected by result type
- **Plugin Architecture** — New result types are registered, not hardcoded
- **Command Pattern** — Each task is a command object with type metadata that determines execution
- **Pipeline Pattern** — The lifecycle stages (validate → store → report) form a mini-pipeline per result

## Relationship to Ditto Factory

This pattern was extracted from the design of [ADR-001: Generalized Task Agents](../adr/001-generalized-task-agents.md) in the Ditto Factory project. The existing codebase already uses protocol-based abstractions (`StateBackend`, `Integration`) that follow the same dispatch principle — this pattern extends it to the result layer.

Key implementation touchpoints:
- `controller/src/controller/models.py` — `TaskType`, `ResultType` enums
- `controller/src/controller/orchestrator.py` — Lifecycle dispatch
- `controller/src/controller/jobs/safety.py` — Result-type-specific validation
- `controller/src/controller/skills/resolver.py` — Agent image selection by task type
