# E2E Integration Test Plan -- Code Review

> Reviewer: Senior Code Reviewer | Date: 2026-03-21

## Verdict: Solid plan with several constructor mismatches and a missing pipeline gap that will block implementation.

---

## Critical Issues (Must Fix)

### 1. JobMonitor constructor mismatch

The plan creates `JobMonitor(settings=settings, redis_state=redis_state)` in the conftest (line 334-335). The actual `JobMonitor.__init__` requires `(redis_state, batch_api, namespace)` -- no `settings` param, and `batch_api` + `namespace` are mandatory. Tests will fail at fixture creation.

**Fix:** `JobMonitor(redis_state=redis_state, batch_api=k8s_clients["batch"], namespace=namespace)`

### 2. K8s label selector uses wrong label key

The plan queries `ditto-factory/thread-id={unique_thread_id[:8]}`. The actual spawner sets labels as `{"app": "ditto-factory-agent", "df/thread": short_id}`. The correct selector is `df/thread={unique_thread_id[:8]}`.

### 3. Happy path test never triggers handle_job_completion

The test calls `orch.handle_task()`, waits for the Job and Redis result, then asserts. But it never calls `orch.handle_job_completion()` -- so the SafetyPipeline never runs, `report_result` is never called, and thread status never returns to IDLE. The plan's architecture diagram claims these are verified, but the test code does not exercise the result-processing half of the pipeline.

**Fix:** After waiting for the Redis result, call `await orch.handle_job_completion(unique_thread_id)` and then assert `thread.status == ThreadStatus.IDLE` and `mock_integration.report_result.assert_called_once()`.

### 4. Mock agent Dockerfile uses build-args that the entrypoint reads from env vars

The CI workflow builds variant images with `--build-arg MOCK_FAIL_PHASE=clone`, but the Dockerfile has no `ARG`/`ENV` directives to wire build args into environment variables. The entrypoint reads `MOCK_FAIL_PHASE` from env at runtime. All variant images will behave identically to the default.

**Fix:** Add `ARG MOCK_FAIL_PHASE=""` and `ENV MOCK_FAIL_PHASE=${MOCK_FAIL_PHASE}` (and same for MOCK_COMMIT_COUNT, MOCK_DELAY_SECONDS) to the Dockerfile. The plan's own recommendation to use env var overrides instead of variant images (Section 10) is the better approach and avoids this entirely.

### 5. Spawner requires ANTHROPIC_API_KEY secret

`build_job_spec` unconditionally adds a `V1EnvVarSource` referencing secret `df-secrets` key `anthropic-api-key`. If this Secret does not exist in the `e2e-ditto-test` namespace, every pod will fail with `CreateContainerConfigError`. The plan's manifests do not include this secret.

**Fix:** Add a manifest creating a dummy `df-secrets` Secret, or modify the spawner to make the secret optional for test scenarios.

---

## Important Issues (Should Fix)

### 6. RedisState constructor mismatch in conftest

The plan creates `RedisState(redis_client)` from the fixture. The actual constructor is `RedisState(redis: Redis)` -- this works, but the `redis_client` fixture connects to `localhost:16379` (host side), while the spawner passes `REDIS_URL` pointing to the in-cluster address. Both URLs must resolve correctly in their respective contexts. The plan handles this correctly (host URL for assertions, cluster URL for pods), but should document this dual-URL requirement more explicitly.

### 7. Settings fixture uses non-existent fields

`settings.job_timeout_seconds` (test_error_paths.py line 670) does not exist in Settings. The actual field is `max_job_duration_seconds`. The test will raise `ValidationError`.

### 8. Missing `agent:` stream key cleanup

The cleanup fixture deletes `task:`, `result:`, `queue:`, `agent:` prefixed keys. The `agent:` keys are Redis streams (XADD), and `DELETE` works for streams, but the RedisState also has `append_stream_event` / `read_stream` methods. If tests exercise streaming, residual stream data could leak between tests.

### 9. No test for handle_job_completion flow

The plan has no dedicated test that exercises the full completion path: `handle_job_completion -> monitor.wait_for_result -> SafetyPipeline.process -> integration.report_result`. This is the most complex code path and the most likely place for integration bugs.

---

## Suggestions (Nice to Have)

### 10. Redis port-forward fragility in CI

`kubectl port-forward ... &` with `sleep 2` is a known source of CI flakiness. Consider using a NodePort service (the kind config already maps port 30379) instead, which eliminates the background process entirely.

### 11. The `dump_state_on_failure` fixture references `get_job_logs` without importing it

The conftest comment block (Section 7.2) uses `get_job_logs` but does not import it from helpers.

### 12. Concurrency test timing assumption

`TestDuplicateWebhook` fires two `handle_task` calls via `asyncio.gather`. Whether the second call sees RUNNING status depends on whether the first call's state updates have committed before the second call reads. With in-memory SQLite and no true advisory locking, this race may not reproduce reliably.

---

## What Was Done Well

- Redis key naming (`task:`, `result:`, `queue:`) matches the actual `RedisState` implementation exactly.
- The mock agent entrypoint correctly mirrors the real protocol (env vars THREAD_ID, REDIS_URL, GITHUB_TOKEN).
- The cleanup strategy (per-test namespace cleanup, unique thread IDs) is sound.
- Observability section (Section 7) with structured log dumping and pod log capture is thorough.
- The decision to keep SQLite for E2E and test Postgres separately is pragmatic.
