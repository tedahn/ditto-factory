# Workflow Engine Design Spec -- Review

**Reviewer:** Senior Code Reviewer
**Date:** 2026-03-25
**Spec:** `docs/superpowers/specs/2026-03-25-workflow-engine-design.md`
**Verdict:** PASS with issues (7 items to address before implementation)

---

## 1. Requirements Coverage (R1-R3, X1)

**Verdict: PASS**

| Requirement | Covered | Notes |
|:------------|:--------|:------|
| R1.1 Structured intent -> execution plan | Yes | Intent classifier (sec 8) + compiler (sec 5.4) |
| R1.2 Sequential + parallel | Yes | Step types: sequential, fan_out |
| R1.3 Single-purpose agents | Yes | Agent contract (sec 6) enforces this |
| R1.4 Agents unaware of workflow | Yes | `workflow_context` is routing-only (sec 6.3) |
| R1.5 Results flow back to engine | Yes | `handle_agent_result()` in sec 5.2 |
| R1.6 Seq/parallel/conditional/retry | Yes | All step types + `max_retries` on steps |
| R1.7 State persisted in Postgres/SQLite | Yes | Schema in sec 3.1, `StateBackend` protocol preserved |
| R1.8 JSON-based definitions | Yes | Template schema in sec 4.1 |
| R1.9 Feature-flagged, backwards-compat | Yes | `DF_WORKFLOW_ENGINE_ENABLED`, `single-task` template |
| R2.1-R2.4 Intent classifier | Yes | Sec 8: async worker, LLM + rule fallback, typed schema |
| R3.1-R3.5 Template library | Yes | CRUD API (sec 9.1), 2 starter templates (sec 10) |
| X1.1-X1.4 Agent contracts | Yes | Sec 6: input/output schema, provenance, stateless |

**No gaps found.** All P0 requirements are addressed.

---

## 2. Codebase Alignment

**Verdict: PASS with 3 issues**

### Issue 1: `handle_agent_result` signature mismatch (Important)

The spec's `handle_agent_result` (sec 5.2) takes `(execution_id, step_id, agent_index, result)`, but the integration point in sec 2.3 calls it as:

```python
await self._workflow_engine.handle_agent_result(
    execution_id=active_job.workflow_execution_id,
    step_id=active_job.workflow_step_id,
    result=result,
)
```

Missing: `agent_index`. The Job model gets `workflow_execution_id` and `workflow_step_id` columns (sec 3.2), but no `workflow_agent_index` column.

**Fix:** Add `workflow_agent_index` to the `jobs` table ALTER statement, and include it in the integration point call.

### Issue 2: `spawner.spawn()` signature matches correctly (Pass)

The spec calls `self._spawner.spawn(thread_id, github_token, redis_url)` which matches the actual `JobSpawner.spawn()` signature: `(thread_id, github_token, redis_url, agent_image=None, extra_env=None)`. Correct.

### Issue 3: `spawner.delete_job()` vs `spawner.delete()` (Important)

In `WorkflowEngine.cancel()` (sec 5.2), the spec calls:

```python
self._spawner.delete_job(job_name)
```

But the actual `JobSpawner` method is `delete(job_name)`, not `delete_job()`.

**Fix:** Change `delete_job` to `delete` in the spec's cancel method.

### Issue 4: Missing `agent_image` in fan-out spawn (Important)

The fan-out executor (sec 5.3) calls `self._spawner.spawn(thread_id, github_token, redis_url)` without passing `agent_image`. The `AgentSpec` type includes `agent_type` but the resolution from agent_type to image (currently done by `AgentTypeResolver` in the orchestrator) is not handled in the workflow engine.

**Fix:** Add agent type resolution to the `StepExecutor` or document that all workflow agents use the default image unless `agent.agent_type` is specified in the template step.

---

## 3. Internal Consistency

**Verdict: PASS with 2 issues**

### Issue 5: `step.on_failure` not in all step types (Suggestion)

The `handle_agent_result` method (sec 5.2, line 572) checks `step.on_failure`, but only `FanOutStep` has `on_failure` in the TypeScript type definitions (sec 4.1). `SequentialStep` does not declare it.

This is arguably correct (sequential steps always fail the workflow on failure), but the behavior should be explicit. Either add `on_failure` to `SequentialStep` or document that only fan-out steps support partial failure.

### Issue 6: Implicit dependency inference creates ambiguity (Important)

The compiler (sec 5.4) infers `depends_on` from step ordering when not specified:

```python
if not depends_on:
    idx = template.definition["steps"].index(step_def)
    if idx > 0:
        depends_on = [template.definition["steps"][idx - 1]["id"]]
```

But the `single-task` template (sec 10.1) has only one step with no `depends_on`. This works.

However, this implicit behavior conflicts with explicit fan-out templates. In `geo-search` (sec 10.2), the first step `search` has no `depends_on` (correct -- it is a root). But if someone adds a step before `search` without setting `depends_on` on `search`, the compiler would wrongly make `search` depend on the new step.

**Fix:** Only infer dependencies when `depends_on` is explicitly absent from the JSON (i.e., key not present), not when it is an empty array. Use `"depends_on" not in step_def` instead of `not depends_on`.

---

## 4. Feasibility and Phased Estimates

**Verdict: PASS**

The 3-phase approach (1 week each) is realistic given the existing codebase:

- **Phase 1** is mostly data models, CRUD, and routing -- straightforward. The `single-task` template is a thin wrapper. One week is tight but achievable.
- **Phase 2** is the core complexity (fan-out, aggregate, transform, entrypoint routing). One week is aggressive but feasible if the Phase 1 foundation is solid.
- **Phase 3** (intent classifier) is well-scoped. The async Redis worker pattern already exists (skill classifier).

The exit criteria for each phase are clear and testable. The phased feature flags allow incremental rollout.

---

## 5. Expert Recommendations

**Verdict: PASS**

All 5 expert recommendations are correctly incorporated:

| Expert Decision | Spec Section | Correct? |
|:----------------|:-------------|:---------|
| Q1: Async intent pre-processing | Sec 8.1: Redis Stream worker, rule-based fallback | Yes |
| Q2: Same Postgres/SQLite, new tables | Sec 3.1: 4 new tables, partial indexes | Yes |
| Q3: Output-type routing | Sec 7: entrypoint branches on `task_type` | Yes |
| Q4: Tiered quality checks | Sec 11 Phase 3: schema validation in aggregate step | Yes |
| Q5: DAG-only, no loops | Sec 5.4: topological sort validation, ADR-003 | Yes |

---

## 6. Migration Safety

**Verdict: PASS with 1 issue**

### Issue 7: `single-task` template does not fully replicate current behavior (Important)

The `single-task` template (sec 10.1) wraps the task as a sequential step, but the current `_spawn_job()` method does significantly more:

1. Skill classification and injection (lines 167-286 in orchestrator.py)
2. Agent type resolution (lines 189-213)
3. Gateway MCP scope setup (lines 316-330)
4. Trace span emission (lines 134-154, 294-313, 357-377)
5. Performance tracking (lines 399-408)
6. Conversation history management (lines 117-131, 157-161)

The spec's `StepExecutor._execute_sequential()` is not shown in detail, but the `single-task` template only passes `task_template` and `task_type`. Skills, gateway, tracing, and conversation history are not handled.

**Fix:** Document explicitly how the `single-task` sequential step executor reuses the existing `_spawn_job()` logic. Options:
  - (a) The sequential step executor delegates to `Orchestrator._spawn_job()` directly for `single-task` templates.
  - (b) The `StepExecutor` replicates all of those capabilities (skill injection, gateway, tracing). This duplicates code.

Option (a) is strongly recommended. The spec should clarify this.

---

## 7. Missing Pieces

### 7a. Crash recovery (Suggestion)

The Q2 expert recommendation mentions "crash recovery via orphaned-row detection." The spec mentions Postgres transactions (sec 13.3) but does not describe a recovery mechanism for workflows that are `running` when the controller crashes. The `advance()` method is event-driven (called after step completion). If the controller dies between agent completion and `advance()`, the workflow stalls.

**Recommendation:** Add a periodic reconciliation loop that checks for executions in `running` status where all steps are either completed/failed/skipped but the execution has not been finalized. Similar to the existing `JobMonitor` pattern.

### 7b. Concurrency control on `advance()` (Suggestion)

In a fan-out with N agents completing near-simultaneously, N `handle_agent_result` calls could race on `advance()`. Two concurrent `advance()` calls could both find the same pending step eligible and start it twice.

**Recommendation:** Use Postgres advisory locks or a `SELECT ... FOR UPDATE` on the execution row in `advance()` to serialize transitions.

### 7c. Cost tracking hook (Suggestion)

R5.5 mentions "budget limits" and X2 covers cost management (P1). The spec does not include any cost tracking hooks in the engine. While this is P1 (not in scope for initial implementation), adding a `_record_step_cost()` hook point in `advance()` would make Phase 2 integration easier.

---

## Summary

| Section | Verdict |
|:--------|:--------|
| 1. Requirements coverage | PASS |
| 2. Codebase alignment | PASS (3 issues) |
| 3. Internal consistency | PASS (2 issues) |
| 4. Feasibility | PASS |
| 5. Expert recommendations | PASS |
| 6. Migration safety | PASS (1 issue) |
| 7. Missing pieces | 3 suggestions |

### Issues to address before implementation

| # | Severity | Description |
|:--|:---------|:------------|
| 1 | Important | Add `workflow_agent_index` to jobs table ALTER; include in integration call |
| 2 | Important | Fix `delete_job` -> `delete` method name in cancel() |
| 3 | Important | Handle agent_image resolution in fan-out executor |
| 4 | Suggestion | Clarify `on_failure` behavior for non-fan-out steps |
| 5 | Important | Fix implicit dependency inference to check key presence, not truthiness |
| 6 | Important | Clarify how `single-task` reuses existing _spawn_job() logic |
| 7 | Suggestion | Add crash recovery reconciliation loop description |

No critical (must-fix) issues found. The spec is well-structured, thorough, and ready for implementation after addressing the 5 Important items above.
