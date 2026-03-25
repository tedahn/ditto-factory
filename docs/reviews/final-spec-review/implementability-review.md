# Implementability Review: Two-State Workflow Engine Spec

**Reviewer:** Software Architect Agent
**Date:** 2026-03-25
**Spec:** `docs/superpowers/specs/2026-03-25-workflow-engine-design.md`
**Verdict:** Implementable with targeted fixes. 7 issues require clarification before a developer can build without guessing.

---

## 1. Ambiguous Pseudocode

**Rating: AMBIGUOUS -- 4 issues found**

| # | Location | Issue | Developer Impact |
|---|----------|-------|------------------|
| A1 | `_execute_fan_out` (L718-768) | Semaphore usage is wrong. The `async with semaphore` wraps only the spawn call, but the intent is to limit concurrency of *running* agents. Spawning is fast; execution is slow. The semaphore releases immediately after spawn, so all N agents run at once regardless of `max_parallel`. Need a queue-and-backfill pattern: spawn `max_parallel` agents, then start the next one when any completes. | **HIGH** -- developer will implement as written, then discover it does not throttle. |
| A2 | `_execute_report` (referenced in executor match/case) | No implementation shown at all. The match/case dispatches to `_execute_report`, but the method body is missing. A developer must guess: how does it format markdown/CSV? How does `thread_reply` delivery work? Does it call an integration registry method? | **HIGH** -- developer will skip or improvise. |
| A3 | `_eval_condition` (L827) | Used in transform filter, never defined. "JSONPath expression" is mentioned in the type def but no library is specified (jsonpath-ng? jsonpath-rw? python-jsonpath?). The expression syntax (`condition: string`) is unspecified -- is it `$.price != "Free"` or `item.price != "Free"`? | **MEDIUM** -- developer must pick a library and guess the expression format. |
| A4 | `_resolve_input` / `_resolve_step_input` (L811, L879) | Two helper methods referenced but never defined. The glob syntax `search.*` in AggregateStep.input is mentioned once but never formalized. Does `search.*` mean "all agent outputs from the step named search"? What does `merge` (plain string) resolve to -- the output of step `merge`? | **MEDIUM** -- conventions are guessable but should be explicit. |

**No TODOs or TBDs found in the spec.** This is good.

---

## 2. Missing Error Paths

**Rating: AMBIGUOUS -- 3 gaps**

| # | Component | Error Scenario | Specified? |
|---|-----------|----------------|------------|
| E1 | WorkflowCompiler | Template references a `depends_on` step ID that does not exist in the template | **MISSING** -- `_validate_dag` checks for cycles but not dangling references. A step with `depends_on: ["nonexistent"]` would silently never execute (never has its deps satisfied). |
| E2 | StepExecutor (fan-out) | `ResourceExhaustedError` is raised but never caught. What happens to the workflow execution? It should transition to `failed`, but the spec does not show error handling in `_execute_step`. | **MISSING** -- the `start()` method calls `_execute_step` without try/except. An exception during step execution leaves the workflow in `running` state forever. |
| E3 | StepExecutor (aggregate/transform) | What if `_resolve_input` returns `None` because the referenced step has no output (e.g., all agents failed but `on_failure: continue`)? The aggregate step will iterate over `None`. | **MISSING** -- need a "no data" path. |
| E4 | Reconciliation | Reconcile calls `handle_agent_result` which calls `advance()` which takes a lock. If reconcile is already processing an execution and an agent result arrives concurrently, is this safe? | **CLEAR** -- yes, the CAS pattern in `advance()` handles this. The lock serializes access. |
| E5 | Template validation | What if `parameter_schema` is invalid JSON Schema? | **MISSING** -- should validate the schema itself at template creation time. |

**Partial failure handling is well-specified** (`on_failure: continue` vs `fail_workflow`). This is a strength.

---

## 3. Undefined Terms

**Rating: CLEAR -- 1 minor issue**

| # | Term | Issue |
|---|------|-------|
| U1 | `MatchResult` (L518) | Return type of `match_template()` -- not defined in models. Should include at minimum: `slug`, `extracted_params`, `confidence`. The `IntentResult` dataclass (L1136) serves a similar purpose but is a different type. Are these the same? |
| U2 | `AgentResult` (L613) | Parameter type of `handle_agent_result()` -- not defined. Is this the raw Redis payload? A Pydantic model? What fields does it have beyond `result`, `provenance`, `quality`? |
| U3 | `ResourceExhaustedError` (L733) | Custom exception, never defined. |
| U4 | `JobSpawner` (L511) | Referenced as constructor dependency. Is this an existing class? If so, which module? If new, what is its interface? |

The glossary (Appendix B) is helpful and covers domain terms well. The gaps are all code-level types that a developer needs to write.

---

## 4. Phase Boundaries

**Rating: CLEAR**

Phase boundaries are well-defined with explicit exit criteria. A developer can implement Phase 1 without reading Phase 2/3 because:

- Phase 1 only requires `sequential` step type -- no fan-out, aggregate, or transform.
- The `single-task` template exercises only the sequential path.
- Phase 2 adds step types incrementally (fan-out, aggregate, transform, report).
- Phase 3 adds intent classification, which is a separate subsystem.

**One concern:** The compiler's implicit dependency inference (L867-871: "if no `depends_on`, depend on previous step") is implemented in Phase 1 but is only tested by Phase 2's multi-step templates. Phase 1's `single-task` template has only one step, so this logic goes untested until Phase 2. Recommend: add a 2-step sequential template to Phase 1 tests.

---

## 5. Test Strategy

**Rating: AMBIGUOUS -- insufficient detail**

The spec has **no testing section**. The phase exit criteria describe *what to verify* but not *how to test*.

A developer needs answers to:

| Question | Answer in spec? |
|----------|-----------------|
| Unit test the compiler (DAG validation, fan-out expansion, parameter interpolation)? | Implied but not specified |
| How to test the engine without K8s? | **MISSING** -- need a mock `JobSpawner` strategy |
| Integration test for fan-out with real Redis? | **MISSING** |
| How to test the reconciliation loop? | **MISSING** -- requires simulating crashed jobs |
| How to test intent classification accuracy? | **MISSING** -- need a test dataset |
| Load test for concurrent workflows? | **MISSING** |

**Recommendation:** Add a Section 14 "Test Strategy" with:
1. Unit test targets per module (compiler: 10+ cases, executor: per-step-type, engine: state transitions)
2. Integration test approach (mock `JobSpawner`, real Redis, real Postgres)
3. A fixture template for testing (simpler than geo-search, exercises all step types)

---

## 6. Dependencies

**Rating: AMBIGUOUS -- 2 gaps**

**Explicitly mentioned:**
- Postgres (existing)
- SQLite (compatibility noted)
- Redis Streams (existing, used for intent classification queue)
- K8s Jobs (existing, via `JobSpawner`)
- `jsonschema` library (for parameter validation)
- `claude` CLI (for agent execution)

**Missing:**
| # | Dependency | Where needed | Issue |
|---|------------|-------------|-------|
| D1 | JSONPath library | `TransformOp.filter.condition`, `ConditionalStep.condition` | No library specified. `jsonpath-ng` is the common choice but must be declared. |
| D2 | Template interpolation | `_render_task` | The spec says "simple string substitution" via `str.replace()`, but `{{ steps.search.output }}` requires dot-path resolution, which is more than `str.replace()`. Need to clarify: is this a regex-based resolver or a micro-template engine? |

**Well-handled:** Redis, Postgres, K8s are all existing dependencies. No new infrastructure required for Phase 1-2.

---

## 7. Config Completeness

**Rating: CLEAR**

All 11 config variables are listed in Section 12 with:
- Environment variable name (all `DF_` prefixed)
- Default value
- Description

**Two minor notes:**
- `DF_WORKFLOW_ENABLED` and `DF_WORKFLOW_ENGINE_ENABLED` overlap in purpose. The spec should clarify: is `DF_WORKFLOW_ENABLED` a master switch that also disables the API? Or is it identical to `DF_WORKFLOW_ENGINE_ENABLED`?
- Reconciliation interval (mentioned as "60 seconds" in `reconcile()` docstring, L661) is not a config variable. Should be `DF_WORKFLOW_RECONCILE_INTERVAL_SECONDS`.

---

## Summary: Issues Requiring Resolution Before Implementation

| Priority | ID | Issue | Effort to Fix |
|----------|----|-------|---------------|
| **P0** | A1 | Fan-out semaphore does not actually throttle | Rewrite with queue-and-backfill pattern |
| **P0** | A2 | `_execute_report` method body missing | Write 20-30 lines of spec |
| **P0** | E2 | Unhandled exceptions in `_execute_step` leave workflow stuck in `running` | Add try/except with `fail_step`/`fail_execution` |
| **P1** | A3 | JSONPath library and expression syntax unspecified | Pick library, show 2-3 example expressions |
| **P1** | A4 | Input resolution conventions (`search.*` vs `merge`) not formalized | Add a "Step Input Resolution" subsection |
| **P1** | E1 | Dangling `depends_on` references not validated | Add check to `_validate_dag` |
| **P1** | E3 | No-data path for aggregate when all agents failed | Specify behavior (empty array? skip step?) |
| **P2** | Test | No test strategy section | Add Section 14 |
| **P2** | D2 | Template interpolation for dot-paths underspecified | Clarify resolver mechanism |
| **P2** | U1-U4 | 4 undefined types | Add to models.py spec or reference existing code |
| **P3** | Config | `DF_WORKFLOW_ENABLED` vs `DF_WORKFLOW_ENGINE_ENABLED` overlap | Clarify or merge |
| **P3** | Config | Reconciliation interval not configurable | Add env var |

---

## Verdict

The spec is **substantially implementable**. The architecture, data model, state machine, API, phasing, and ADRs are all strong. A developer can understand the system and start building.

The 3 P0 issues (fan-out throttling, missing report executor, unhandled step exceptions) must be resolved first -- each one would cause a developer to either build the wrong thing or get stuck. The P1 issues are fillable by a developer who asks clarifying questions, but should not require guessing. P2/P3 issues are nice-to-haves that can be resolved during implementation.

**Overall quality: 8/10.** This is an unusually complete spec. The code-level detail (actual Python, actual SQL, actual JSON) makes it actionable. The gaps are localized and fixable.
