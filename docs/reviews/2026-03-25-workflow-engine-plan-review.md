# Workflow Engine Implementation Plan Review

**Date:** 2026-03-25
**Plan:** `docs/superpowers/plans/2026-03-25-workflow-engine.md`
**Spec:** `docs/superpowers/specs/2026-03-25-workflow-engine-design.md`
**Reviewer:** Senior Code Reviewer (Opus 4.6)

---

## 1. Spec Section Coverage

Does every spec section have a corresponding task in the plan?

| Spec Section | Plan Task(s) | Verdict |
|:-------------|:-------------|:--------|
| S1. Executive Summary | N/A (overview) | N/A |
| S2. Architecture | Task 8 (orchestrator integration) | PASS |
| S3. Data Model (schema, state machine) | Task 1 (migration), Task 2 (models) | PASS |
| S4. Workflow Template Schema (types, interpolation, fan-out) | Task 2 (models), Task 4 (compiler) | PASS |
| S5. Workflow Engine (engine, executor, compiler) | Task 4, 5, 11, 12, 13 | PASS |
| S6. Agent Contract (input/output, rules) | Task 14 (entrypoint routing) | PASS |
| S7. Entrypoint Changes (routing logic) | Task 14 | PASS |
| S8. Intent Classifier | Task 18, 19 | PASS |
| S9. API Reference (templates, executions, estimate, errors) | Task 7 | PARTIAL -- see finding F1 |
| S10. Starter Templates | Task 15 | PASS |
| S11. Phased Implementation | Phase 1-3 structure | PASS |
| S12. Configuration | Task 6 | PASS |
| S13. Trade-offs and Risks | N/A (prose) | N/A |
| S14. ADRs | N/A (design rationale) | N/A |
| S15. Migration from Current Architecture | Task 8 | PASS |

**Finding F1 (Important):** The spec defines structured error codes (S9.4) with machine-readable `error_code` fields (`TEMPLATE_NOT_FOUND`, `INVALID_PARAMETERS`, `AGENT_LIMIT_EXCEEDED`, etc.). The plan's API implementation (Task 7) uses generic `HTTPException(detail=...)` without error codes. No task creates structured error responses.

**Finding F2 (Important):** The spec defines `GET /api/v1/workflows/executions` (list executions, filterable by status) in S9.3. The plan's Task 7 only implements `GET /executions/{id}` and `POST /executions`, but not the list endpoint.

**Finding F3 (Suggestion):** The spec's cost estimation response (S9.2) includes `estimated_cost_usd`, `estimated_duration_seconds`, and `warnings`. The plan's `EstimateResponse` only returns `total_agents` and `steps`. The enriched estimate fields are missing.

---

## 2. Review Findings Addressed

Are all 6 review findings from the codebase-alignment and implementability reviews addressed?

| # | Finding | Plan Task | Addressed? | Verdict |
|:--|:--------|:----------|:-----------|:--------|
| R1 | R1.6 -- Retry backoff strategy missing | Task 2 adds `retry_delay_seconds` field; Task 5 implements `calculate_retry_delay()` with exponential backoff | Yes, correctly | PASS |
| R2 | Design Decision #4 -- Quality checks under-specified | Task 21 implements all 6 checks (schema, completeness, freshness, dedup, source diversity, composite) | Yes, thoroughly | PASS |
| R3 | E2 -- `_execute_step` missing try/except | Task 5 wraps `_execute_step` in try/except, marks step failed on error, calls `advance()` | Yes, correctly | PASS |
| R4 | E3 -- `_resolve_input` returns None handling | Task 12 `_execute_aggregate` checks for None source_step/output and returns empty | Yes, correctly | PASS |
| R5 | Implicit dependency inference untested in Phase 1 | Task 4 adds `test_implicit_dependency_inference`; Task 10 has 2-step sequential E2E test | Yes, thoroughly | PASS |
| R6 | Config default contradiction (0.5 vs 0.7 threshold) | Task 6 uses 0.7 with explicit comment | Yes, correctly | PASS |

All 6 findings are addressed. PASS.

---

## 3. File Path Consistency

| File Path in Plan | Consistent Across Tasks? | Verdict |
|:------------------|:------------------------|:--------|
| `controller/migrations/004_workflow_engine.sql` | Used in Task 1, referenced in Task 3, 15 | PASS |
| `controller/migrations/005_seed_workflow_templates.sql` | Used in Task 15 | PASS |
| `controller/src/controller/workflows/__init__.py` | Created Task 2 | PASS |
| `controller/src/controller/workflows/models.py` | Created Task 2, imported everywhere | PASS |
| `controller/src/controller/workflows/templates.py` | Created Task 3, imported Task 7, 15, 19 | PASS |
| `controller/src/controller/workflows/compiler.py` | Created Task 4, imported Task 5, 7 | PASS |
| `controller/src/controller/workflows/engine.py` | Created Task 5, modified Tasks 11-13, 16, 19-20 | PASS |
| `controller/src/controller/workflows/state.py` | Created Task 5, used in Task 8 | PASS |
| `controller/src/controller/workflows/api.py` | Created Task 7, mounted Task 8 | PASS |
| `controller/src/controller/workflows/intent.py` | Created Task 18 | PASS |
| `controller/src/controller/workflows/quality.py` | Created Task 21 | PASS |
| `controller/src/controller/config.py` | Modified Task 6 | PASS |
| `controller/src/controller/models.py` | Modified Task 8 | PASS |
| `controller/src/controller/orchestrator.py` | Modified Task 8, 19 | PASS |
| `controller/src/controller/main.py` | Modified Task 8 | PASS |
| `images/agent/entrypoint.sh` | Modified Task 14 | PASS |

All file paths are consistent. PASS.

---

## 4. Task Dependencies

| Task | Depends On | Valid? | Verdict |
|:-----|:-----------|:-------|:--------|
| 1 (Migration) | None | Yes | PASS |
| 2 (Models) | None | Yes | PASS |
| 3 (Template CRUD) | 1, 2 | Yes -- needs schema + models | PASS |
| 4 (Compiler) | 2 | Yes -- needs models | PASS |
| 5 (Engine Core) | 2, 3, 4 | Yes -- needs all three | PASS |
| 6 (Config) | None | Yes | PASS |
| 7 (API) | 2, 3 | Yes -- needs models + registry | PASS |
| 8 (Orchestrator Integration) | 5, 6, 7 | Yes -- needs engine, config, API | PASS |
| 9 (Crash Recovery) | 5 | Yes -- extends engine | PASS |
| 10 (Phase 1 Tests) | 1-9 | Yes -- integration test | PASS |
| 11 (Fan-out) | 5 | Yes -- extends engine | PASS |
| 12 (Aggregate) | 11 | Yes -- needs fan-out output | PASS |
| 13 (Transform) | 12 | Yes -- needs aggregate output | PASS |
| 14 (Entrypoint) | None | Yes -- independent | PASS |
| 15 (Seed Templates) | 1, 3 | Yes -- needs schema + registry | PASS |
| 16 (Observability) | 5 | Yes -- adds tracing to engine | PASS |
| 17 (Phase 2 Tests) | 11-16 | Yes -- integration test | PASS |
| 18 (Intent Classifier) | 3, 6 | Yes -- needs templates + config | PASS |
| 19 (Intent Worker Integration) | 18, 8 | Yes -- needs classifier + orchestrator | PASS |
| 20 (Report Step) | 5 | Yes -- extends engine | PASS |
| 21 (Quality Checks) | 12 | Yes -- runs on aggregate output | PASS |
| 22 (Phase 3 Tests) | 11-21 | Yes -- final integration | PASS |

No circular dependencies. All dependency chains are valid. PASS.

**Finding F4 (Suggestion):** Task 7 (API) depends on Tasks 2 and 3, but the `start_execution` endpoint also depends on the WorkflowEngine (Task 5). The dependency list should include Task 5, though the test uses a mock engine so it technically works without it.

---

## 5. TDD Pattern Compliance

| Task | Test Written First? | Test Runs Red? | Implementation Follows? | Verdict |
|:-----|:--------------------|:---------------|:------------------------|:--------|
| 1 (Migration) | N/A (SQL, verified by script) | N/A | Yes | PASS |
| 2 (Models) | Step 2: write test | Step 3: verify fail | Step 4: implement | PASS |
| 3 (Template CRUD) | Step 1: write test | Step 2: verify fail | Step 3: implement | PASS |
| 4 (Compiler) | Step 1: write test | Step 2: verify fail | Step 3: implement | PASS |
| 5 (Engine Core) | Step 1: write test | Step 2: verify fail | Steps 3-4: implement | PASS |
| 6 (Config) | Step 1: write test | Step 2: verify fail | Step 3: implement | PASS |
| 7 (API) | Step 1: write test | Step 2: verify fail | Step 3: implement | PASS |
| 8 (Orchestrator) | Step 1: write test | Step 2: verify fail | Steps 3-5: implement | PASS |
| 9 (Crash Recovery) | Step 1: write test | Step 2: already passes (reconcile in Task 5) | N/A | PARTIAL -- see F5 |
| 10 (Phase 1 Tests) | Step 1: integration test | Step 2: run all | N/A | PASS |
| 11 (Fan-out) | Step 1: write test | Step 2: verify fail | Step 3: implement | PASS |
| 12 (Aggregate) | Step 1: write test | Step 2: verify fail | Step 3: implement | PASS |
| 13 (Transform) | Step 1: write test | Step 2: verify fail | Step 3: implement | PASS |
| 14 (Entrypoint) | Step 1: write test | N/A (pattern check) | Step 2: implement | PASS |
| 15 (Seed Templates) | Step 2: write test | N/A (seed data) | Step 1: write SQL | PASS |
| 16 (Observability) | No test | No test | Just adds tracing | FAIL -- see F6 |
| 17 (Phase 2 Tests) | Step 1: E2E test | Step 2: run all | N/A | PASS |
| 18 (Intent Classifier) | Step 1: write test | Step 2: verify fail | Step 3: implement | PASS |
| 19 (Intent Worker) | No test | No test | Just wires match_template | FAIL -- see F6 |
| 20 (Report Step) | Step 1: write test | Step 2: verify fail (implied) | Step 2: implement | PASS |
| 21 (Quality Checks) | Step 1: write test | Step 2: verify fail (implied) | Step 2: implement | PASS |
| 22 (Phase 3 Tests) | Step 1: final E2E | Step 2: run all | N/A | PASS |

**Finding F5 (Suggestion):** Task 9 writes tests for reconciliation but notes the implementation already exists in Task 5. This is fine, but the "Run test to verify it fails" step says "reconcile is already implemented" -- which means the test never runs red. Acceptable since the test adds coverage for specific reconcile scenarios.

**Finding F6 (Important):** Tasks 16 (Observability) and 19 (Intent Worker Integration) have no tests at all. Task 16 adds tracing spans and Task 19 adds `match_template` to the engine -- both should have at least a unit test verifying the method exists and handles the happy path.

---

## 6. Commit Message Consistency

| Pattern | Count | Examples | Verdict |
|:--------|:------|:---------|:--------|
| `feat(workflow): ...` | 17 | "add database migration", "add workflow engine data models" | PASS |
| `test(workflow): ...` | 5 | "add crash recovery reconciliation tests", "add Phase 1 end-to-end integration tests" | PASS |

All commits follow `type(scope): description` convention. Scope is consistently `workflow`. Types are `feat` for new code and `test` for test-only commits. PASS.

---

## 7. Phase 1 Self-Containment

Can Phase 1 (Tasks 1-10) ship independently?

| Requirement | Covered? | Details |
|:------------|:---------|:--------|
| Data model + migrations | Yes | Task 1 |
| Pydantic/dataclass models | Yes | Task 2 |
| Template CRUD | Yes | Task 3 |
| Compiler (DAG validation, single-step) | Yes | Task 4 |
| Engine core (start, advance, cancel) | Yes | Task 5 |
| Config + feature flags | Yes | Task 6 (default: disabled) |
| API endpoints | Yes | Task 7 |
| Orchestrator integration | Yes | Task 8 |
| Crash recovery | Yes | Task 9 |
| E2E tests | Yes | Task 10 |
| Feature-flagged off by default | Yes | `workflow_enabled=False`, `workflow_engine_enabled=False` |
| Backwards compatible | Yes | Falls through to existing `_spawn_job` when no template matches |
| Fan-out stubbed | Yes | Task 5 routes fan_out to sequential (single agent) |
| Non-code step types stubbed | Yes | aggregate/transform/report log warning and complete with `{}` |

Phase 1 is self-contained and can ship without Phase 2/3. PASS.

**Finding F7 (Suggestion):** Phase 1 stubs fan-out as sequential and non-code steps as no-ops. This is documented in the `_execute_step` match/case but not in the API documentation or README. A brief note in the API response or template listing would help operators understand what is functional vs. stubbed.

---

## 8. Test Commands Runnable

| Task | Test Command | Runnable? | Issues |
|:-----|:-------------|:----------|:-------|
| 1 | `python -c "import sqlite3..."` | Yes | In-memory SQLite, no deps | PASS |
| 2 | `python -m pytest controller/tests/test_workflow_models.py -x -q` | Yes | Needs `pytest` | PASS |
| 3 | `python -m pytest controller/tests/test_workflow_templates.py -x -q` | Yes | Needs `aiosqlite`, `pytest-asyncio` | PASS |
| 4 | `python -m pytest controller/tests/test_workflow_compiler.py -x -q` | Yes | Needs `jsonschema` | PASS |
| 5 | `python -m pytest controller/tests/test_workflow_engine.py -x -q` | Yes | Uses in-memory mocks | PASS |
| 6 | `python -m pytest controller/tests/test_config.py::TestWorkflowConfig -x -q` | Yes | Needs `pydantic-settings` | PASS |
| 7 | `python -m pytest controller/tests/test_workflow_api.py -x -q` | Yes | Needs `fastapi`, `httpx` | PASS |
| 8 | `python -m pytest controller/tests/test_workflow_integration.py -x -q` | Yes | Uses mocks | PASS |
| 9 | `python -m pytest controller/tests/test_workflow_engine.py::TestReconcile -x -q` | Yes | | PASS |
| 10 | Full suite command | Yes | | PARTIAL -- see F8 |
| 11 | `python -m pytest controller/tests/test_workflow_fanout.py -x -q` | Yes | | PARTIAL -- see F9 |
| 12-22 | Various | Yes | | PASS |

**Finding F8 (Important):** Task 10's test (`test_workflow_full_phase1.py`) imports `from test_workflow_engine import InMemoryWorkflowState, MockSpawner, MockRedisState`. This uses a bare module name, which only works if `controller/tests/` is in `sys.path`. Most pytest configs use package-relative imports. Should be `from controller.tests.test_workflow_engine import ...` or, better, extract shared fixtures into a `conftest.py`.

**Finding F9 (Important):** Same bare-import issue affects Tasks 11, 17, and 22 (`from test_workflow_engine import InMemoryWorkflowState`). All fan-out, aggregate, and E2E tests share this problem. The `InMemoryWorkflowState`, `MockSpawner`, and `MockRedisState` should be extracted to a shared `conftest.py` or a `controller/tests/workflow_fixtures.py` module.

---

## 9. Additional Findings

**Finding F10 (Critical):** The spec's `handle_agent_result` signature (S5.2, line 608-614) takes `agent_index: int` as a parameter. The plan's `handle_agent_result` in Task 5 does NOT take `agent_index` -- it looks up the step by `step_id` string only. This works for sequential steps (one agent per step) but creates an ambiguity: the orchestrator's `handle_job_completion` (Task 8, line 3853) calls `handle_agent_result(execution_id, step_id, result, success)` without `agent_index`. For fan-out steps (Task 11), a separate `handle_fan_out_agent_result` method is created that DOES take `agent_index`. This means the orchestrator completion hook in Task 8 cannot correctly route fan-out agent results because it does not pass `agent_index`. The completion hook needs to determine whether the job is fan-out or sequential and call the appropriate method.

**Finding F11 (Important):** The plan's migration (Task 1) uses `ALTER TABLE jobs ADD COLUMN` which will fail if the column already exists (e.g., re-running the migration). The spec notes this but the plan has no idempotency guard. Should use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` or wrap in exception handling. Note: SQLite does not support `ADD COLUMN IF NOT EXISTS` before version 3.35.0. Need a version check or try/except.

**Finding F12 (Important):** The `WorkflowStep` dataclass in Task 2 has a `config` field but no `on_failure` field. The spec's `FanOutStep` has `on_failure: "fail_workflow" | "continue"`. In the plan, `on_failure` is accessed via `step.config.get("on_failure", "fail")` (Task 11, line 4436). This works but the default value is `"fail"` in the plan vs `"fail_workflow"` in the spec. These must match to avoid confusion. The spec uses `"fail_workflow"`, the plan should use the same string.

**Finding F13 (Suggestion):** The spec defines a `ConditionalStep` type (S4.1) and the plan includes `"conditional"` in the StepType enum (Task 2) and the `_execute_step` match/case (Task 5), but no task implements the conditional step executor. It is stubbed as completed-with-empty-output in Phase 1 but never gets a real implementation in Phase 2 or 3. This is a gap -- no task covers conditional step execution.

---

## 10. Per-Task Summary

| Task | Phase | Title | Verdict | Issues |
|:-----|:------|:------|:--------|:-------|
| 1 | 1 | Data Model + Migrations | PASS | F11: ALTER TABLE idempotency |
| 2 | 1 | Pydantic Models | PASS | |
| 3 | 1 | Template CRUD | PASS | |
| 4 | 1 | Workflow Compiler | PASS | |
| 5 | 1 | Workflow Engine Core | PASS | |
| 6 | 1 | Config + Feature Flags | PASS | |
| 7 | 1 | API Endpoints | PARTIAL | F1: missing error codes, F2: missing list executions |
| 8 | 1 | Orchestrator Integration | PARTIAL | F10: fan-out agent_index routing gap |
| 9 | 1 | Crash Recovery | PASS | F5: test never runs red |
| 10 | 1 | Phase 1 Final Tests | PARTIAL | F8: bare module imports |
| 11 | 2 | Fan-out Step | PARTIAL | F9: bare imports, F12: on_failure default mismatch |
| 12 | 2 | Aggregate Step | PASS | |
| 13 | 2 | Transform Step | PASS | |
| 14 | 2 | Entrypoint Routing | PASS | |
| 15 | 2 | Seed Templates | PASS | |
| 16 | 2 | Observability | FAIL | F6: no tests |
| 17 | 2 | Phase 2 Tests | PARTIAL | F9: bare imports |
| 18 | 3 | Intent Classifier | PASS | |
| 19 | 3 | Intent Worker Integration | FAIL | F6: no tests |
| 20 | 3 | Report Step | PASS | |
| 21 | 3 | Quality Checks | PASS | |
| 22 | 3 | Phase 3 Tests | PARTIAL | F9: bare imports |

---

## 11. Overall Verdict

**PASS WITH CONDITIONS**

The plan is comprehensive, well-structured, and closely aligned with the spec. 22 tasks cover all major spec sections across 3 phases. All 6 review findings are explicitly addressed. The TDD pattern is followed in 18/22 tasks. Phase 1 is self-contained and can ship independently.

### Must fix before implementation (3 items):

1. **F10 (Critical):** Resolve the `handle_agent_result` vs `handle_fan_out_agent_result` split. The orchestrator completion hook (Task 8) must handle both sequential and fan-out jobs. Either unify into one method that detects the step type, or add `agent_index` to the completion hook routing logic.

2. **F8/F9 (Important):** Extract `InMemoryWorkflowState`, `MockSpawner`, and `MockRedisState` into `controller/tests/conftest.py` or a shared fixtures module. The bare `from test_workflow_engine import ...` pattern will fail in standard pytest configurations.

3. **F6 (Important):** Add tests for Tasks 16 (Observability) and 19 (Intent Worker Integration). Even minimal smoke tests would catch import errors and verify method signatures.

### Should fix (4 items):

4. **F1:** Add structured error codes to API responses per spec S9.4.
5. **F2:** Add `GET /api/v1/workflows/executions` list endpoint per spec S9.3.
6. **F11:** Add idempotency guard for `ALTER TABLE jobs ADD COLUMN`.
7. **F12:** Align `on_failure` default from `"fail"` to `"fail_workflow"` to match spec.

### Nice to have (3 items):

8. **F3:** Enrich estimate response with cost, duration, warnings.
9. **F13:** Add conditional step executor implementation (or explicitly defer to a future phase).
10. **F7:** Document which step types are stubbed in Phase 1.

---

**Quality score: 8.5/10.** This is a thorough, implementation-ready plan. The code snippets are complete, the test coverage is extensive, and the phasing is well-designed. The critical issue (F10) is a routing gap that would surface during integration testing, but is better caught now. The shared test fixture issue (F8/F9) is a practical concern that would block pytest runs in most CI configurations.
