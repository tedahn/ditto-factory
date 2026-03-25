# Code Review: Generalized Task Agents Implementation Plan

**Reviewer:** Code Review Agent
**Date:** 2026-03-25
**Verdict:** Issues Found (fixable, none are architectural blockers)

---

## Summary

The plan is well-structured, follows TDD methodology correctly, and the task ordering is sound. The ADR-001 alignment is accurate. However, there are several concrete issues that would cause test failures or runtime bugs if implemented as-written. All are fixable without architectural changes.

---

## Issues Found

### BLOCKER: Task 5 — Safety refactor loses spawner call signature

**File:** Plan lines 1028-1032, compared to current `safety.py` line 38

The current `safety.py` calls `self._spawner(thread.id, is_retry=True, retry_count=retry_count + 1)` where `self._spawner` is actually `self._spawn_job` from the orchestrator (see `orchestrator.py` line 449: `spawner=self._spawn_job`).

However, `_spawn_job` signature is `async def _spawn_job(self, thread: Thread, task_request: TaskRequest, ...)` — it takes a `Thread` and `TaskRequest`, not `thread.id`.

The current safety.py line 38 already has this mismatch — it passes `thread.id` as first arg. So this is a **pre-existing bug** that the plan faithfully preserves. But the plan should fix it rather than carry it forward.

**Recommendation:** The plan should note this and fix the spawner call or document it as a known pre-existing issue.

---

### BLOCKER: Task 5 — Test `test_pr_retry_on_empty_result` asserts wrong spawner call

**File:** Plan line 870

The test asserts `spawner.assert_called_once()` but the refactored `_process_pr` calls `self._spawner(thread.id, ...)`. The mock `spawner = AsyncMock()` will work, but the test doesn't verify the arguments passed. Since the spawner signature mismatch exists (see above), this test would pass but mask the real bug.

**Recommendation:** Add argument verification: `spawner.assert_called_once_with(thread.id, is_retry=True, retry_count=1)`

---

### SUGGESTION: Task 3 — SQLite line number reference is slightly off

**File:** Plan says "after the `locks` table (line 56)"

The `locks` table creation ends at line 56 with `""")`, and `await db.commit()` is on line 57. The new table creation should be inserted **before** `await db.commit()` (line 57), not after line 56. Technically line 56 is correct for "after the locks table CREATE", but the instruction should explicitly say "before `await db.commit()`" to avoid inserting after the commit.

---

### SUGGESTION: Task 3 — Postgres backend `get_artifacts_for_task` uses `json.loads` but Postgres JSONB auto-deserializes

**File:** Plan line 493-496

In the Postgres backend, `asyncpg` automatically deserializes JSONB columns to Python dicts. The plan uses `json.loads(row["metadata"])` which would fail if `metadata` is already a dict (raises `TypeError: the JSON object must be str, bytes or bytearray, not dict`).

**Fix:** Use `row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"])` or just `row["metadata"] or {}`.

---

### SUGGESTION: Task 3 — Missing `list_threads` method on Postgres backend

**File:** `postgres.py`

The `StateBackend` protocol defines `list_threads()` but it's missing from the Postgres backend. This is pre-existing but worth noting since the plan is modifying this file.

---

### SUGGESTION: Task 5 — `test_report_stores_artifacts` uses `thread.id` as `task_id`

**File:** Plan line 966

The test asserts `state.create_artifact.assert_called_once_with(task_id=thread.id, artifact=artifact)`. Using `thread.id` as the `task_id` for artifact storage is a design choice, but the `task_artifacts` table column is called `task_id` and was designed to reference `tasks(id)` (per the ADR SQL). In the current codebase there's no `tasks` table — there's `jobs`. Using `thread.id` means artifacts are associated with threads, not jobs. This should be `job.id` or the plan should document this intentional deviation from the ADR.

---

### SUGGESTION: Task 4 — Tests missing `pytest.mark.asyncio`

**File:** Plan lines 563-673

All async test methods in `TestPRValidator`, `TestReportValidator`, etc. need `@pytest.mark.asyncio` or a `pytest-asyncio` mode configuration. The plan doesn't mention this. Without it, the async tests will silently pass without actually running the coroutine (they'll just check that the coroutine object is truthy).

Same issue in Task 3 (`test_artifact_storage.py`) and Task 5 (`test_safety_dispatch.py`).

**Fix:** Either add `@pytest.mark.asyncio` to each async test or add `asyncio_mode = "auto"` to `pyproject.toml` / `pytest.ini`.

---

### SUGGESTION: Task 5 — Refactored safety.py doesn't use validators from Task 4

**File:** Plan lines 978-1062

Task 4 creates a `ResultValidator` protocol and `get_validator()` dispatch function. Task 5 then... doesn't use them. The safety pipeline dispatches via `if/elif` on `result_type` instead of calling `get_validator(result_type).validate(result, thread)`. This makes Task 4's validator protocol dead code in this plan.

**Recommendation:** Either (a) wire the validators into the safety pipeline, or (b) remove Task 4 from this plan and defer it to a future phase. As-is, it creates code that nothing calls.

---

### NIT: Task 1 — Plan says "insert after line 18" but `JobStatus` ends at line 19

**File:** Plan line 170

The plan says "insert after the existing `JobStatus` enum (line 18)". `JobStatus` actually spans lines 14-18, and line 19 is blank. The `TaskRequest` class starts at line 22. The instruction should say "insert after line 19 (the blank line after `JobStatus`), before `TaskRequest` on line 22."

---

### NIT: Task 6 — Test is trivial / doesn't test actual wiring

**File:** Plan lines 1100-1114

`TestOrchestratorTaskTypePassthrough.test_task_type_in_redis_payload` just creates a `TaskRequest` and checks `task_type`. It doesn't test that the orchestrator actually puts `task_type` into the Redis payload. The plan even admits this with "This is a documentation test." A real test would mock `_redis.push_task` and verify the payload dict contains `"task_type": "analysis"`.

---

### NIT: Task 8 — Plan modifies integration protocol signature but doesn't show it

**File:** File structure table says "report_result gains `artifacts` param"

The plan's file structure table says `protocol.py` will change `report_result` to accept `artifacts`, but none of the implementation tasks actually modify the integration protocol. The formatting module is standalone. This is either an abandoned design decision or a missing task.

---

## Backwards Compatibility Assessment

Backwards compatibility IS properly preserved for the core changes:
- `TaskRequest.task_type` defaults to `CODE_CHANGE`
- `AgentResult.result_type` defaults to `PULL_REQUEST`
- `AgentResult.artifacts` defaults to `[]`
- `build_system_prompt` accepts `task_type` as optional kwarg
- Safety pipeline falls back to PR path for unknown types

---

## Task Ordering Assessment

The ordering is correct. Each task builds on the previous:
1. Models (no deps)
2. Config (no deps)
3. State backends (depends on models from Task 1)
4. Validators (depends on models from Task 1)
5. Safety refactor (depends on models from Task 1; should depend on Task 4 but doesn't)
6. Orchestrator wiring (depends on Task 1 models)
7. Prompt builder (depends on Task 1 models)
8. Integration formatting (depends on Task 1 models)

Tasks 3 and 4 could run in parallel. Tasks 6, 7, 8 could run in parallel.

---

## Circular Dependency Check

No circular dependencies introduced. All new imports flow downward:
- `validators.py` imports from `models.py` (OK)
- `safety.py` imports from `models.py` and `config.py` (OK, same as before)
- `formatting.py` imports from `models.py` (OK)
- `builder.py` imports from `models.py` (new, OK — no cycle)
