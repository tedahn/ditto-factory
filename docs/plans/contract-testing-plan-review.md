# Contract Testing Plan -- Code Review

**Reviewer**: Senior Code Reviewer
**Date**: 2026-03-21
**Verdict**: Good plan with accurate contract analysis. Three specific issues below.

---

## 1. Contract Accuracy vs Source Code

**Mostly accurate.** All 11 contracts correctly reflect the actual data shapes in `models.py`. The `TaskRequest`, `AgentResult`, `Thread`, `Job` dataclasses and `StateBackend` protocol methods match the plan exactly.

### Issues Found

**[Important] Contract 3 table is incomplete.** The plan omits `get_job(job_id) -> Job | None` and `update_job_status(job_id, status, result)` from the protocol method table, but both exist in `protocol.py` (lines 5-6 of the Protocol class). The orchestrator calls `create_job` but the `handle_job_completion` path does not call `update_job_status` -- this is likely a bug in the application itself (job status is never updated to COMPLETED/FAILED).

**[Suggestion] Contract 9 misdescribes the provider.** The plan says SafetyPipeline calls GitHub API directly, but the actual code at `safety.py:23` calls `self._github_client.create_pr()` -- a client abstraction, not raw API calls. The contract should reference the `github_client` interface, not "GitHub API."

**[OK] Contract 4 (Spawner) is accurate.** `build_job_spec` and `spawn` signatures, env vars, security context, and `backoff_limit=1` all match `spawner.py`.

---

## 2. Bug Findings Validation

### (a) `int()` cast on non-numeric strings -- CONFIRMED, but low risk

`monitor.py:23-24` does `int(result_data.get("exit_code", 1))` and `int(result_data.get("commit_count", 0))`. If the agent writes `"exit_code": "abc"`, `int("abc")` raises `ValueError` and `wait_for_result` propagates it (no try/except). The plan correctly identifies this. However, the plan's proposed test (`test_result_with_string_numbers`) only tests valid numeric strings ("0", "3"), not truly invalid ones like "abc" or "". **Add a test for `int()` on non-numeric strings to verify the crash path.**

### (b) Queue drain not crash-safe -- CONFIRMED

`redis_state.py:30-36`: `drain_messages` uses `pipeline()` with `lrange` + `delete`. Redis pipelines are NOT transactional -- if the process crashes after `lrange` executes but before `delete`, messages are consumed but the key persists (duplicates on retry). Conversely, if `delete` executes but the process crashes before processing results, messages are lost. The plan correctly identifies this. **Recommend using `MULTI/EXEC` (transaction) or Lua script instead of bare pipeline.**

### (c) No TTL on queue keys -- CONFIRMED

`redis_state.py:28`: `queue_message` calls `rpush(f"queue:{thread_id}", message)` with no `expire` call. Task and result keys have TTL (3600s) but queue keys do not. Abandoned threads will leave orphaned queue keys indefinitely. **This is a real memory leak vector.**

---

## 3. Completeness -- Missing Contracts

**[Important] Missing: Integration Protocol contract.** There is a `controller/integrations/protocol.py` (visible in the source tree) defining the `Integration` protocol with `parse_webhook`, `fetch_context`, `report_result`, and `acknowledge`. The plan does not define a standalone contract for this protocol, though it is implicitly covered across contracts 1, 2, 10. A dedicated protocol conformance test (like Contract 3's approach) would catch implementations that drift.

**[Important] Missing: Orchestrator.handle_job_completion flow.** The plan covers `handle_task` thoroughly but does not analyze the `handle_job_completion` path (orchestrator.py:141-167), which constructs a new `SafetyPipeline` instance per call. This is a distinct contract boundary worth documenting.

**[Suggestion] Missing: Stream events contract.** `RedisState.append_stream_event` and `read_stream` (redis_state.py:38-52) are not covered by any contract. If any consumer relies on these, they need a contract.

---

## 4. Test Strategy Assessment

**Strengths:**
- Cross-process boundary focus (contracts 5/6) is correctly prioritized as CRITICAL
- Abstract test suite for StateBackend (Contract 3) running against both SQLite and Postgres is excellent
- E2E contract test with stubbed K8s is practical and well-structured

**[Important] The E2E test has a structural problem.** At `safety.py:36`, the retry path calls `self._spawner(thread.id, is_retry=True, retry_count=retry_count + 1)`, treating `_spawner` as a callable. But in `orchestrator.py:163`, it passes `self._spawn_job` (a bound method). The E2E test must verify this callable interface matches -- the plan's test fixtures use `AsyncMock()` for spawner, which will accept any arguments silently.

**[Suggestion] Add negative contract tests.** The plan tests mostly happy paths and edge cases. Add tests for contract violations: what happens when StateBackend raises, when Redis returns corrupted JSON, when integration.report_result throws?

---

## 5. Tooling Recommendations

**[OK] fakeredis + pytest-asyncio** -- appropriate for this async Python/FastAPI codebase.

**[Suggestion] Pact is overkill.** The plan recommends Pact but this is a single-repo monolith where both producer and consumer are in the same codebase. JSON Schema validation (already proposed) plus the contract test suite is sufficient. Pact adds CI complexity without proportional value here. Consider Pact only if the agent container becomes a separate repo.

**[OK] JSON Schema for cross-process contracts** -- good choice for the controller-agent boundary.

---

## Summary

| Category | Count |
|----------|-------|
| Critical | 0 |
| Important | 4 (missing protocol contract, missing handle_job_completion, incomplete Contract 3 table, spawner callable interface mismatch) |
| Suggestions | 4 (Pact overkill, stream events, negative tests, non-numeric int test) |
| Bugs confirmed | 3/3 (all valid findings) |
