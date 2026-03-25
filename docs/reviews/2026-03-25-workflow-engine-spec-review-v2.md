# Workflow Engine Design Spec -- Review v2 (Post-Fix)

**Reviewer:** Senior Code Reviewer
**Date:** 2026-03-25
**Spec:** `docs/superpowers/specs/2026-03-25-workflow-engine-design.md`
**Previous Review:** `docs/reviews/2026-03-25-workflow-engine-spec-review.md`
**Verdict:** PASS with 3 residual issues (down from 7+3 in v1)

---

## Fix Verification Checklist

### 1. Jinja2 replaced with safe string substitution
**VERIFIED**

Section 4.2 now explicitly states:
> "Template interpolation MUST NOT use Jinja2 or any engine that supports code execution. Use simple string substitution: `template.replace('{{ region }}', params['region'])`. All `{{ var }}` markers are resolved via `str.replace()` -- no expressions, no filters, no code execution."

This directly addresses FINDING-01 (SSTI) from the security review. The `{{ }}` syntax is retained as visual markers but resolved via `str.replace()`, not a template engine.

### 2. `custom_code` / `custom` strategy removed from AggregateStep
**VERIFIED**

Section 4.1, `AggregateStep` type now reads:
```typescript
strategy: "merge_arrays" | "merge_objects" | "concat";
// No user-defined code runs in the workflow engine. All operations are predefined.
```

The `custom` strategy and `custom_code` field are gone. The explicit comment reinforces the design principle. This addresses FINDING-02 from the security review.

### 3. Global agent limits added
**VERIFIED**

Two new settings in section 12:
- `DF_WORKFLOW_MAX_AGENTS_PER_EXECUTION` (default 20) -- per-workflow cap
- `DF_WORKFLOW_MAX_CONCURRENT_AGENTS` (default 50) -- global system-wide cap

Enforcement code present in two locations:
- `WorkflowEngine.start()` (line 546-555): pre-flight check before persisting execution
- `StepExecutor._execute_fan_out()` (line 731-736): runtime check before spawning

API error codes `AGENT_LIMIT_EXCEEDED` (429) and `GLOBAL_LIMIT_EXCEEDED` (429) defined in section 9.4. This addresses FINDING-03 from the security review and F8 from the SRE review.

### 4. Race condition in `advance()` fixed with row-level locking
**VERIFIED**

Section 5.2, `advance()` method (line 567-598) now includes:
- Detailed docstring explaining the concurrency problem and solution
- `SELECT ... FOR UPDATE` on execution row (Postgres)
- `BEGIN EXCLUSIVE` transaction (SQLite fallback)
- Atomic CAS on step start: `UPDATE ... SET status = 'running' WHERE id = ? AND status = 'pending'`
- `lock_execution()` context manager wrapping the entire advance logic

This addresses F10 from the SRE review.

### 5. Crash recovery reconciliation loop added
**VERIFIED**

New `reconcile()` method in `WorkflowEngine` (line 657-689):
- Queries all executions with `status = 'running'`
- Checks K8s job status for each running agent
- Re-processes completed-but-unrecorded results
- Marks missing/failed K8s jobs as failed
- Calls `advance()` to unstick workflows
- Docstring specifies: runs on startup + every 60 seconds

This addresses F1 and F6 from the SRE review and suggestion 7a from v1 review.

### 6. Agent output schema validation before merge
**VERIFIED**

`_execute_aggregate()` (line 770-807) now:
- Calls `_validate_and_filter_inputs()` before merging
- Invalid results are excluded from the merge
- Validation errors stored in step metadata via `update_step_metadata()`
- Comment confirms: "validate each agent's output against the `output_schema` defined in the originating step"

This addresses Q4 expert recommendation (Tier 1 quality checks).

### 7. Intent classifier input sanitization + confidence threshold
**VERIFIED**

Section 8.2 "Input Sanitization" (line 1124-1130) adds:
- XML/HTML tag stripping
- Maximum 2000 character truncation
- Prompt injection marker removal (`ignore previous`, `system:`, etc.)
- Confidence threshold: < 0.7 falls back to `single-task`

Section 8.4 "Confidence Thresholds" (line 1146-1151) defines three tiers:
- >= 0.8: auto-execute
- 0.7-0.8: execute but log for review
- < 0.7: fallback to single-task

Configuration: `DF_WORKFLOW_INTENT_CONFIDENCE_THRESHOLD` (0.5) and `DF_WORKFLOW_INTENT_AUTO_THRESHOLD` (0.8) in section 12.

Note: the config default (0.5) contradicts the prose (0.7). See residual issue #1 below.

### 8. Unique constraints added to schema
**VERIFIED**

Three unique constraints added:
- `workflow_templates.slug`: `TEXT UNIQUE NOT NULL` (line 165)
- `workflow_steps(execution_id, step_id)`: `CREATE UNIQUE INDEX idx_wf_steps_exec_step` (line 230-231)
- `workflow_step_agents(step_id, agent_index)`: `CREATE UNIQUE INDEX idx_wf_step_agents_unique` (line 259-260)

These address the data architecture review concerns about duplicate steps and duplicate agent indices.

### 9. Template versioning history table added
**VERIFIED**

New `workflow_template_versions` table (line 269-279):
- Immutable version records with `template_id`, `version`, `definition`, `parameter_schema`
- `changelog` field for human-readable diff notes
- `UNIQUE (template_id, version)` constraint
- Comment: "same pattern as skill_versions"

This addresses the data architecture review's concern about version history being lost on template updates.

### 10. Cost estimation endpoint added
**VERIFIED**

Section 9.2 (line 1205-1232):
- `POST /api/v1/workflows/estimate`
- Request takes `template_slug` + `parameters`
- Response returns `estimated_agents`, `estimated_steps`, `estimated_cost_usd`, `estimated_duration_seconds`, `warnings`
- Addresses the DX review's request for preview/estimation capabilities

---

## Residual Issues (Not Fixed)

### Issue A: `delete_job` vs `delete` method name (Important -- carried from v1 Issue #2)
**NOT FIXED**

Line 653 in `cancel()` still calls `self._spawner.delete_job(job_name)`. The actual `JobSpawner` method is `delete(job_name)`. This will cause a runtime `AttributeError` when cancelling workflows.

**Fix:** Change `self._spawner.delete_job(job_name)` to `self._spawner.delete(job_name)`.

### Issue B: Missing `agent_index` in integration point call (Important -- carried from v1 Issue #1)
**NOT FIXED**

The `handle_agent_result` method signature (line 608-613) requires 4 parameters: `execution_id`, `step_id`, `agent_index`, `result`. But the integration point at section 2.3 (line 131-134) only passes 3:
```python
await self._workflow_engine.handle_agent_result(
    execution_id=active_job.workflow_execution_id,
    step_id=active_job.workflow_step_id,
    result=result,
)
```

Missing: `agent_index`. The `jobs` table ALTER (line 300-301) adds `workflow_execution_id` and `workflow_step_id` but NOT `workflow_agent_index`.

**Fix:** Add `ALTER TABLE jobs ADD COLUMN workflow_agent_index INTEGER;` and include `agent_index=active_job.workflow_agent_index` in the call.

### Issue C: Implicit dependency inference still uses truthiness (Important -- carried from v1 Issue #5)
**NOT FIXED**

Line 866-871 in the compiler:
```python
depends_on = step_def.get("depends_on", [])
if not depends_on:
    # Infer from position
```

This treats `depends_on: []` (explicit empty -- "I have no dependencies") the same as `depends_on` not being present (implicit -- "infer for me"). A step with `"depends_on": []` should be treated as a root node, but will instead get an inferred dependency on the previous step.

**Fix:** Change to `if "depends_on" not in step_def:` to distinguish explicit empty from absent.

---

## Consistency Check (Post-Fix)

### Schema <-> Engine Agreement
- Schema defines 5 tables; engine code references all 5 via `_workflow_state` methods. **Consistent.**
- `workflow_template_versions` table added but no engine code references it. Acceptable -- version writes happen in the template CRUD layer, not the engine.

### Schema <-> API Agreement
- API error codes reference `AGENT_LIMIT_EXCEEDED` and `GLOBAL_LIMIT_EXCEEDED`; engine raises corresponding errors in `start()` and `_execute_fan_out()`. **Consistent.**
- Cost estimation endpoint references template expansion logic that exists in the compiler. **Consistent.**

### Config <-> Code Agreement
- `DF_WORKFLOW_MAX_AGENTS_PER_EXECUTION` referenced as `self._settings.workflow_max_agents_per_execution` in engine. **Consistent.**
- `DF_WORKFLOW_MAX_CONCURRENT_AGENTS` referenced as `self._settings.workflow_max_concurrent_agents` in executor. **Consistent.**
- **Inconsistency:** `DF_WORKFLOW_INTENT_CONFIDENCE_THRESHOLD` defaults to `0.5` in the config table (line 1474), but section 8.2 prose says "if classifier confidence < 0.7, fall back to single-task." The config and prose disagree on the threshold. This is confusing but not blocking -- the config value wins at runtime.

### New Contradictions Introduced
- None found beyond the confidence threshold inconsistency noted above.

### Phased Implementation
- Phase 2 now includes crash recovery (reconciliation loop) -- correct placement since it depends on fan-out.
- Phase 3 includes quality checks (schema validation in aggregate) -- correct placement.
- No phase ordering issues introduced by the fixes. **Still makes sense.**

---

## Expert Review Cross-Check

| Expert Review | Key Concern | Status |
|:-------------|:-----------|:-------|
| Security | SSTI via Jinja2 | Fixed (str.replace) |
| Security | custom_code RCE | Fixed (removed) |
| Security | Unbounded fan-out | Fixed (global limits) |
| Data Architecture | Missing unique constraints | Fixed |
| Data Architecture | Missing version history | Fixed |
| DX/API | No cost estimation | Fixed |
| DX/API | No structured error codes | Fixed (section 9.4) |
| SRE | Crash recovery (F1, F6) | Fixed (reconcile loop) |
| SRE | Race condition (F10) | Fixed (row-level locking) |
| SRE | Connection pool exhaustion (F4) | Not addressed -- still a risk with large fan-outs |

---

## Summary

| Category | v1 Verdict | v2 Verdict |
|:---------|:-----------|:-----------|
| Fixes applied | -- | 10/10 verified present |
| Residual issues | 7 issues | 3 remaining (A, B, C) |
| New contradictions | -- | 1 minor (confidence threshold default) |
| Internal consistency | -- | Good (one config/prose mismatch) |
| Phased plan | -- | Still sound |

### Remaining Issues

| # | Severity | Description |
|:--|:---------|:------------|
| A | Important | `delete_job` should be `delete` in cancel() (line 653) |
| B | Important | Missing `agent_index` + `workflow_agent_index` column in integration point (sec 2.3, line 131-134) |
| C | Important | Dependency inference uses `not depends_on` instead of key-absence check (line 867) |
| -- | Minor | Confidence threshold config (0.5) vs prose (0.7) mismatch |

### Final Verdict

**PASS** -- The 10 requested fixes are all present and correctly implemented. The spec is substantially improved from v1. Three residual issues remain from the original review (all Important, none Critical). These are straightforward fixes that should take minutes to apply. The spec is ready for implementation once issues A, B, and C are addressed.
