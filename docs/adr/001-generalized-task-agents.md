# ADR-001: Generalized Task Agents — Beyond Code-Only Output

| Field       | Value                          |
|-------------|--------------------------------|
| **Status**  | Proposed                       |
| **Date**    | 2026-03-25                     |
| **Authors** | Ted Ahn                        |
| **Deciders**| Ted Ahn                        |

## Context

Ditto Factory was built as a **Kubernetes-native coding agent platform**. Today every agent task follows a single output path: clone a repo, run Claude Code, push a branch, and open a PR. The entire safety pipeline, result reporting, and integration layer assumes the deliverable is a GitHub pull request.

This assumption limits the platform's utility. Many high-value agent tasks produce results that are **not code changes**:

- **Data backfills** — inserting or transforming rows in a database
- **Report generation** — producing files (CSV, PDF, JSON) and uploading them to object storage
- **Configuration updates** — writing to config stores, feature flag systems, or secret managers
- **API orchestration** — calling external APIs to provision resources, trigger workflows, or sync systems
- **Analysis & audits** — querying data and posting structured findings back to the requester

These tasks share the same lifecycle (receive, classify, spawn, execute, validate, report) but diverge in what "execute" produces and how "validate" confirms correctness.

## Decision

We will **generalize the agent task model** to support multiple result types beyond pull requests. Specifically:

### 1. Introduce `task_type` and `result_type` enums

```python
class TaskType(str, Enum):
    CODE_CHANGE = "code_change"        # Current behavior (PR output)
    DB_MUTATION = "db_mutation"         # INSERT/UPDATE/DELETE against a database
    FILE_OUTPUT = "file_output"         # Generate and upload files to object storage
    API_ACTION  = "api_action"         # Call external APIs to perform actions
    ANALYSIS    = "analysis"           # Query, analyze, report findings (read-only)

class ResultType(str, Enum):
    PULL_REQUEST  = "pull_request"
    DB_ROWS       = "db_rows"
    FILE_ARTIFACT = "file_artifact"
    API_RESPONSE  = "api_response"
    REPORT        = "report"
```

`TaskType` describes the agent's mission. `ResultType` describes the concrete output. A single task type may produce multiple result types (e.g., `DB_MUTATION` might also produce a `REPORT` summarizing what was changed).

### 2. Extend `TaskRequest` and `AgentResult`

- `TaskRequest` gains a `task_type` field (default: `CODE_CHANGE` for backwards compatibility).
- `AgentResult` gains a `result_type` field plus a polymorphic `artifacts` payload.
- The orchestrator branches on `task_type` to select the appropriate safety validators and reporting path.

### 3. Result-type-specific safety pipelines

| Result Type     | Validation Strategy |
|-----------------|---------------------|
| `pull_request`  | Existing: PR creation check, anti-stall retry |
| `db_rows`       | Dry-run preview → human approval gate → execute. Row count sanity check. Require idempotency key. |
| `file_artifact` | Schema/format validation, checksum, size limits. Store reference in `task_artifacts` table. |
| `api_response`  | Dry-run mode where supported, response status validation, rollback instructions captured. |
| `report`        | Structure validation, completeness checks. No destructive side effects. |

### 4. Dry-run → Approve → Execute pattern for destructive operations

For `DB_MUTATION` and `API_ACTION` task types, the agent must:

1. **Preview** — Generate a plan/diff showing what will change (e.g., SQL with `EXPLAIN`, row count estimates).
2. **Pause** — Post the preview to the originating channel (Slack/GitHub/Linear) and wait for human approval.
3. **Execute** — Only proceed after explicit approval. Record the execution result.
4. **Verify** — Post-execution validation (row counts match preview, API responses are 2xx).

This mirrors the PR review gate but for non-code deliverables.

### 5. New agent Docker images per capability domain

The existing three-layer capability model (Agent Types → Skills → Subagents) already supports this. New agent images would include domain-specific tooling:

- `ditto-factory-data-agent` — psql, DuckDB, cloud SQL clients
- `ditto-factory-file-agent` — S3/GCS SDKs, file format libraries
- `ditto-factory-ops-agent` — Terraform, cloud CLIs, API clients

Skills are still injected per-task; the image provides the runtime capabilities.

### 6. Artifact storage

A new `task_artifacts` table stores references to non-code outputs:

```sql
CREATE TABLE task_artifacts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id     UUID NOT NULL REFERENCES tasks(id),
    result_type TEXT NOT NULL,
    location    TEXT NOT NULL,          -- S3 URI, table name, API endpoint
    metadata    JSONB DEFAULT '{}',     -- Row counts, checksums, schema info
    created_at  TIMESTAMPTZ DEFAULT now()
);
```

## Consequences

### Positive

- **Broader utility** — Ditto Factory becomes a general-purpose task agent platform, not just a coding tool.
- **Same operational model** — Teams interact the same way (Slack message, GitHub issue, Linear comment) regardless of task type.
- **Incremental adoption** — `CODE_CHANGE` remains the default. New task types are opt-in via skill configuration.
- **Reusable architecture** — The `task_type → result_type` pattern is a generalizable architecture pattern (see [Architecture Pattern: Agent Type to Result Type](../patterns/agent-type-result-type-pattern.md)).

### Negative

- **Safety complexity increases** — PR review is a well-understood gate. DB mutations and API calls need bespoke validation logic per domain.
- **Scope risk** — "General-purpose task platform" is a much larger surface area than "coding agent." Must be disciplined about which task types to support and when.
- **Reversibility varies** — `git revert` is trivial. Reversing a database mutation or an API side effect may be impossible. The dry-run gate is load-bearing.
- **Testing burden** — Each new result type needs its own e2e test harness.

### Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Accidental destructive DB write | Mandatory dry-run gate; no auto-execute for mutations |
| Scope creep dilutes coding quality | Task types are feature-flagged; ship one at a time |
| Agent image sprawl | Lean images; prefer MCP Gateway tools over baked-in binaries |
| Artifact storage costs | TTLs on task_artifacts; configurable retention policies |

## Alternatives Considered

### A. Keep code-only, use external pipelines for data tasks

- **Rejected because:** Forces teams to maintain two separate systems for tasks that share the same lifecycle. Fragmenting the workflow defeats the purpose of an integrated agent platform.

### B. Encode all outputs as git commits (e.g., commit a CSV to a branch)

- **Rejected because:** Abusing git as a general-purpose artifact store creates bloated repos, confusing PRs, and doesn't work for database mutations or API calls.

### C. Build a separate "data agent" product

- **Rejected because:** 80% of the infrastructure (webhooks, skill classification, job spawning, reporting) is shared. Forking creates maintenance burden without architectural justification.

## Implementation Plan

Phase 1 (foundation):
- Add `TaskType` and `ResultType` enums to `models.py`
- Extend `TaskRequest` and `AgentResult` with new fields (backwards-compatible defaults)
- Create `task_artifacts` table migration
- Refactor `jobs/safety.py` to dispatch validation by result type

Phase 2 (first non-code type — `ANALYSIS`):
- Safest starting point: read-only, no destructive side effects
- Agent queries data, produces a structured report, posts back to origin
- Validates the pattern end-to-end without mutation risk

Phase 3 (destructive types — `DB_MUTATION`, `FILE_OUTPUT`):
- Implement dry-run → approve → execute flow
- Build approval gate into integrations (Slack interactive messages, GitHub check runs)
- New agent images with data tooling

Phase 4 (`API_ACTION` and beyond):
- External API orchestration with rollback capture
- Composable tasks (one task triggers another)

## References

- [Architecture Pattern: Agent Type to Result Type](../patterns/agent-type-result-type-pattern.md)
- Current architecture: `controller/src/controller/orchestrator.py`
- Safety pipeline: `controller/src/controller/jobs/safety.py`
- Skill resolver: `controller/src/controller/skills/resolver.py`
