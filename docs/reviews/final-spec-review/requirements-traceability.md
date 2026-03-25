# Requirements Traceability Matrix -- Workflow Engine Spec Review

**Date:** 2026-03-25
**Spec:** `docs/superpowers/specs/2026-03-25-workflow-engine-design.md`
**Requirements:** `docs/requirements/product-requirements.md`
**Reviewer:** Senior Code Reviewer

---

## Wave 1 Requirements Coverage

### R1: Two-State Workflow Engine (P0)

| Req ID | Requirement | Spec Section(s) | Status | Notes |
|:-------|:-----------|:----------------|:-------|:------|
| R1.1 | Accepts structured intent, compiles to deterministic execution plan | S2.2 (Request Flow), S5.2 (WorkflowEngine.start), S5.4 (WorkflowCompiler) | COVERED | Intent -> template matching -> compiler -> execution plan. Full code shown. |
| R1.2 | Executes steps sequentially or in parallel (fan-out/fan-in) | S4.1 (Step Types: SequentialStep, FanOutStep), S5.3 (StepExecutor) | COVERED | Both sequential and fan-out executors designed with code examples. |
| R1.3 | Each step spawns one focused, single-purpose agent | S6 (Agent Contract), S5.3 (_execute_fan_out) | COVERED | AgentSpec defines single-task agents. Fan-out spawns N independent agents. |
| R1.4 | Agents have no awareness of the broader workflow | S6.3 (Contract Rules: "No workflow awareness") | COVERED | Explicitly stated: agents never read workflow_context for decisions. |
| R1.5 | Step results flow back to engine, engine decides next step deterministically | S5.2 (handle_agent_result, advance) | COVERED | Results stored, advance() traverses DAG to find next steps. Concurrency control documented. |
| R1.6 | Supports: sequential, parallel, conditional branching, retry with backoff | S4.1 (Step types), S3.3 (State Machine), S3.1 (retry_count, max_retries) | PARTIAL | Sequential, parallel (fan-out), conditional: all designed. Retry: retry_count/max_retries fields exist but **backoff strategy not specified** (no exponential backoff config or retry delay logic shown). |
| R1.7 | Workflow state persisted in Postgres/SQLite (survives controller restart) | S3 (Data Model), S5.2 (reconcile method) | COVERED | Full schema with SQLite compatibility notes. Crash recovery via reconcile() on startup + periodic. |
| R1.8 | JSON-based workflow definitions (Step Functions ASL-inspired, stored in DB) | S4 (Template Schema), S3.1 (workflow_templates.definition JSONB) | COVERED | Complete TypeScript type definitions, JSON examples, stored in DB. |
| R1.9 | Feature-flagged, backwards-compatible | S12 (Configuration: DF_WORKFLOW_ENGINE_ENABLED), S10.1 (single-task template), S15.2 (Feature Flag Rollout) | COVERED | Feature flag off by default. single-task template preserves current behavior. Rollback strategy documented. |

### R3: Workflow Template Library (P0)

| Req ID | Requirement | Spec Section(s) | Status | Notes |
|:-------|:-----------|:----------------|:-------|:------|
| R3.1 | Templates are parameterized workflow definitions | S4.1 (ParameterDef), S4.2 (Template Interpolation), S10.2 (geo-search example) | COVERED | Parameters with types, defaults, and `{{ var }}` interpolation. |
| R3.2 | Stored in Postgres, versioned, soft-deletable | S3.1 (workflow_templates table: version, is_active), S3.1 (workflow_template_versions table) | COVERED | Version history table, is_active for soft delete, same pattern as skill registry. |
| R3.3 | Templates reference agent types and skills by ID, not inline logic | S4.1 (AgentSpec: skills as string[], agent_type as string) | COVERED | AgentSpec references skills by slug and agent_type by name. |
| R3.4 | CRUD API for templates | S9.1 (POST/GET/PUT/DELETE /api/v1/workflows/templates) | COVERED | Full REST API with request/response examples. Note: API path uses `/workflows/templates` not `/templates` as requirements suggest -- acceptable deviation for namespace clarity. |
| R3.5 | Ship with at least 2 starter templates: single-task and fan-out-search | S10.1 (single-task), S10.2 (geo-search) | COVERED | Both templates fully defined with JSON. geo-search serves the fan-out-search role. |

### X1: Agent Contracts (P0)

| Req ID | Requirement | Spec Section(s) | Status | Notes |
|:-------|:-----------|:----------------|:-------|:------|
| X1.1 | Every agent receives: task description, skills, tool access, output schema | S6.1 (Agent Input payload) | PARTIAL | Task, skills, output_schema: all present. **Tool access not explicitly in the contract** -- agents get tools via MCP Gateway (mentioned in S7.2 table) but the agent input payload does not declare which tools are available. This may be intentional (tools come via skills), but worth confirming. |
| X1.2 | Every agent returns: structured result, provenance metadata, exit code | S6.2 (Agent Output payload) | COVERED | Result, provenance array, quality metadata, exit_code, stderr all defined. |
| X1.3 | Contract is the same regardless of task type | S6.3 (Contract Rules), S7.1 (Routing Logic) | COVERED | Same contract schema for all task types. Entrypoint routing only changes git vs workspace setup. |
| X1.4 | Agents are stateless -- no memory of previous runs, no awareness of other agents | S6.3 ("No workflow awareness", "No child spawning"), S14 (ADR-002) | COVERED | Explicitly stated in contract rules and ADR-002. |

---

## Design Decisions Verification

| Decision | Requirements Doc Reference | Spec Location | Status | Notes |
|:---------|:--------------------------|:-------------|:-------|:------|
| 1. Intent classification: Async pre-processing | Resolved Decision #1 | S8 (Intent Classifier), S2.2 (Request Flow) | CORRECT | Redis Stream worker, LLM + rule-based fallback, async from webhook ack. |
| 2. Workflow state: Same Postgres/SQLite | Resolved Decision #2 | S3 (Data Model), S3.1 (SQLite compatibility notes) | CORRECT | New tables in same DB. Crash recovery via orphaned-row detection (reconcile method). |
| 3. Fan-out + git: Output-type routing | Resolved Decision #3 | S7 (Entrypoint Changes), S14 (ADR-004) | CORRECT | Code tasks use git, non-code skip git. Routing on task_type in entrypoint.sh. |
| 4. Result quality: Tiered approach | Resolved Decision #4 | S5.3 (_execute_aggregate: validation), S10.2 (dedupe transform) | PARTIAL | Schema validation in aggregate step is present. However, **only 1 of the 6 planned automated checks (schema validation) is designed**. Completeness, freshness, URL liveness, source diversity, and dedup-rate checks are not detailed in the spec. The transform step handles dedup but as a data operation, not a quality signal. |
| 5. Workflow loops: No loops (DAG-only) | Resolved Decision #5 | S5.4 (_validate_dag: topological sort), S14 (ADR-003) | CORRECT | Kahn's algorithm validates DAG. ADR-003 documents the decision with rationale. |

---

## Phased Implementation Alignment

| Wave (Requirements) | Phase (Spec) | Alignment | Notes |
|:--------------------|:-------------|:----------|:------|
| Wave 1: R1 + R3 + X1 | Phase 1 (Foundation) + Phase 2 (Fan-Out) | ALIGNED | Phase 1 covers core engine + templates + single-task. Phase 2 adds fan-out, aggregate, transform, report. Together they cover all of Wave 1. |
| Wave 2: R2 + R4 + R6 | Phase 3 (Intent + Polish) | PARTIAL | R2 (Intent Classifier) is in Phase 3. R6 (Result Aggregation) is in Phase 2. R4 (Observability) gets tracing spans in Phase 2 but the full R4 scope (provenance queries, execution timeline API) is not explicitly phased. |
| Wave 3: R5 + D1 + R7 | Not in spec | NOT COVERED | Expected -- spec is scoped to the workflow engine (R1/R3/X1 + supporting R2). Wave 3 items are future work. |

---

## Issues Summary

### Critical (Must Fix)

None. All Wave 1 P0 requirements are addressed.

### Important (Should Fix)

1. **R1.6 -- Retry backoff strategy missing.** The schema has `retry_count` and `max_retries` but no backoff configuration (delay, exponential factor). The `advance()` method has no retry delay logic. Add a `retry_delay_seconds` field or backoff strategy to the step schema and document the retry timing in the engine.

2. **Design Decision #4 -- Quality checks under-specified.** The resolved decision commits to 6 automated quality checks in the merge step (schema validation, completeness, freshness, URL liveness, dedup rate, source diversity). Only schema validation appears in the spec. Either design the remaining 5 checks or explicitly defer them to a later phase with a note in the spec.

### Suggestions (Nice to Have)

1. **X1.1 -- Tool access in agent contract.** The requirement says agents receive "tool access" but the agent input payload does not include a tool manifest. If tools are implicitly provided via skills and MCP Gateway, document this explicitly in the contract section.

2. **R4 phasing.** The observability requirement (R4) spans multiple sub-requirements (trace spans, provenance queries, execution timeline API). The spec adds tracing in Phase 2 but does not indicate where the query APIs (R4.3, R4.4, R4.5) will land. Consider noting these as Wave 2 items.

3. **API path convention.** Requirements suggest `/api/v1/templates/...` (R3.4) but spec uses `/api/v1/workflows/templates/...`. The spec's namespacing is arguably better, but worth confirming this is intentional.

---

## Verdict

The spec provides thorough coverage of all Wave 1 requirements (R1, R3, X1). The two important issues (retry backoff and quality checks) are implementation details that can be resolved during Phase 1/2 development. All 5 resolved design decisions appear correctly in the spec. The phased implementation aligns well with the Wave 1/2/3 priority structure from requirements.

**Recommendation:** Approve with the two "Important" items tracked as implementation tasks.
