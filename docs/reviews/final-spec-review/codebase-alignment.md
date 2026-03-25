# Codebase Alignment Review: Workflow Engine Spec

**Date:** 2026-03-25
**Spec:** `docs/superpowers/specs/2026-03-25-workflow-engine-design.md`
**Reviewer:** Backend Architect Agent

---

## 1. Orchestrator (`controller/src/controller/orchestrator.py`)

### Constructor Parameters

| Parameter | Spec Assumes | Code Has | Match? | Fix Needed |
|:----------|:-------------|:---------|:-------|:-----------|
| `settings: Settings` | Yes | Yes | YES | -- |
| `state: StateBackend` | Yes | Yes | YES | -- |
| `redis_state: RedisState` | Yes | Yes | YES | -- |
| `registry: IntegrationRegistry` | Yes (used by engine for delivery) | Yes | YES | -- |
| `spawner: JobSpawner` | Yes (passed to engine) | Yes | YES | -- |
| `monitor: JobMonitor` | Not referenced in spec | Yes | YES | No conflict; spec does not touch monitor |
| `classifier: TaskClassifier` | Not referenced (spec adds IntentClassifier instead) | Yes (optional) | WARN | Spec's IntentClassifier is a separate worker; both can coexist |
| `workflow_engine: WorkflowEngine` | Spec adds this new param | Not present | MISSING | Must add as optional param in Phase 1 |

### Method: `handle_task(self, task_request: TaskRequest) -> None`

| Aspect | Spec Says | Code Says | Match? | Fix Needed |
|:-------|:----------|:----------|:-------|:-----------|
| Signature | `handle_task(self, task_request: TaskRequest) -> None` | `handle_task(self, task_request: TaskRequest) -> None` | YES | -- |
| Lock acquisition | Spec inserts workflow routing after lock, before `_spawn_job` | Code acquires lock at line 86, calls `_spawn_job` at line 92 | YES | Insert routing between lock and `_spawn_job` |
| Settings field | `self._settings.workflow_engine_enabled` | Does not exist yet | MISSING | Add to Settings (see config section) |
| Workflow engine call | `self._workflow_engine.match_template(task_request)` | No workflow engine reference | MISSING | Expected -- this is new code to add |
| Return after workflow | `return` after `_workflow_engine.start()` | N/A | OK | Compatible; early return skips `_spawn_job` |

### Method: `_spawn_job(self, thread, task_request, is_retry, retry_count) -> None`

| Aspect | Spec Says | Code Says | Match? | Fix Needed |
|:-------|:----------|:----------|:-------|:-----------|
| Signature | Not modified by spec | `_spawn_job(self, thread: Thread, task_request: TaskRequest, is_retry: bool = False, retry_count: int = 0) -> None` | YES | -- |
| `push_task` call | Spec assumes same pattern | Code calls `self._redis.push_task(thread_id, task_payload)` at line 347 | YES | -- |
| `spawner.spawn` call | Spec assumes same pattern | Code calls `self._spawner.spawn(thread_id=..., github_token=..., redis_url=..., agent_image=...)` at line 350 | YES | -- |

### Method: `handle_job_completion(self, thread_id: str) -> None`

| Aspect | Spec Says | Code Says | Match? | Fix Needed |
|:-------|:----------|:----------|:-------|:-----------|
| Signature | `handle_job_completion(self, thread_id: str) -> None` | `handle_job_completion(self, thread_id: str) -> None` | YES | -- |
| Check `workflow_execution_id` on job | `active_job.workflow_execution_id` | `Job` dataclass has no `workflow_execution_id` field | MISSING | Add nullable field to `Job` model |
| Check `workflow_step_id` on job | `active_job.workflow_step_id` | `Job` dataclass has no `workflow_step_id` field | MISSING | Add nullable field to `Job` model |
| Workflow engine call | `self._workflow_engine.handle_agent_result(...)` | No workflow engine reference | MISSING | Expected -- new code |
| Fallback to SafetyPipeline | Spec preserves existing pipeline for non-workflow jobs | Code at lines 450-459 | YES | Wrap in `else` branch |

---

## 2. Models (`controller/src/controller/models.py`)

### Job Dataclass

| Field | Spec Says | Code Has | Match? | Fix Needed |
|:------|:----------|:---------|:-------|:-----------|
| `id` | Yes | Yes | YES | -- |
| `thread_id` | Yes | Yes | YES | -- |
| `k8s_job_name` | Yes | Yes | YES | -- |
| `status` | Yes | Yes (JobStatus enum) | YES | -- |
| `task_context` | Yes | Yes | YES | -- |
| `result` | Yes | Yes | YES | -- |
| `agent_type` | Yes | Yes | YES | -- |
| `skills_injected` | Yes | Yes | YES | -- |
| `started_at` | Yes | Yes | YES | -- |
| `completed_at` | Yes | Yes | YES | -- |
| `workflow_execution_id` | Spec adds (nullable TEXT) | **Not present** | MISSING | Add: `workflow_execution_id: str \| None = None` |
| `workflow_step_id` | Spec adds (nullable TEXT) | **Not present** | MISSING | Add: `workflow_step_id: str \| None = None` |

### TaskRequest Dataclass

| Field | Spec References | Code Has | Match? | Fix Needed |
|:------|:----------------|:---------|:-------|:-----------|
| `thread_id` | Yes | Yes | YES | -- |
| `source` | Yes | Yes | YES | -- |
| `source_ref` | Yes | Yes | YES | -- |
| `repo_owner` | Yes | Yes | YES | -- |
| `repo_name` | Yes | Yes | YES | -- |
| `task` | Yes | Yes | YES | -- |
| `task_type` | Yes (spec uses `.value` for routing) | Yes (`TaskType` enum, default `CODE_CHANGE`) | YES | -- |
| `skill_overrides` | Not referenced by spec | Yes | YES | No conflict |
| `agent_type_override` | Not referenced by spec | Yes | YES | No conflict |

### TaskType Enum

| Value | Spec References | Code Has | Match? | Fix Needed |
|:------|:----------------|:---------|:-------|:-----------|
| `code_change` | Yes (entrypoint routing) | Yes | YES | -- |
| `analysis` | Yes (entrypoint routing) | Yes | YES | -- |
| `file_output` | Yes (entrypoint routing) | Yes | YES | -- |
| `api_action` | Yes (entrypoint routing) | Yes | YES | -- |
| `db_mutation` | Not in spec routing | Yes | WARN | Spec entrypoint only routes `analysis\|file_output\|api_action`; `db_mutation` would hit default case. Consider adding to routing. |

### AgentResult Dataclass

| Field | Spec Agent Output | Code Has | Match? | Fix Needed |
|:------|:------------------|:---------|:-------|:-----------|
| `branch` | Yes | Yes | YES | -- |
| `exit_code` | Yes | Yes | YES | -- |
| `commit_count` | Yes | Yes | YES | -- |
| `stderr` | Yes | Yes | YES | -- |
| `pr_url` | Yes | Yes | YES | -- |
| `trace_events` | Yes | Yes | YES | -- |
| `result` (structured output) | Spec adds top-level `result` field for analysis output | **Not present** | MISSING | Add: `result: dict \| None = None` for structured workflow output |
| `provenance` | Spec requires for analysis tasks | **Not present** | MISSING | Add: `provenance: list[dict] = field(default_factory=list)` |
| `quality` | Spec includes quality metadata | **Not present** | MISSING | Add: `quality: dict \| None = None` |

---

## 3. Config (`controller/src/controller/config.py`)

### New Settings Fields from Spec

| Spec Variable | Spec Default | Exists in Code? | Match? | Fix Needed |
|:--------------|:-------------|:-----------------|:-------|:-----------|
| `workflow_enabled` | `false` | No | MISSING | Add to Settings |
| `workflow_engine_enabled` | `false` | No | MISSING | Add to Settings |
| `workflow_max_agents_per_execution` | `20` | No | MISSING | Add to Settings |
| `workflow_max_concurrent_agents` | `50` | No | MISSING | Add to Settings |
| `workflow_max_steps` | `50` | No | MISSING | Add to Settings |
| `workflow_step_timeout_default` | `600` | No | MISSING | Add to Settings |
| `workflow_intent_confidence_threshold` | `0.5` | No | MISSING | Add to Settings |
| `workflow_intent_auto_threshold` | `0.8` | No | MISSING | Add to Settings |
| `intent_classifier_enabled` | `false` | No | MISSING | Add to Settings |
| `intent_classifier_concurrency` | `5` | No | MISSING | Add to Settings |
| `intent_classifier_fallback` | `true` | No | MISSING | Add to Settings |

### Existing Fields Conflict Check

| Existing Field | Spec Conflict? | Notes |
|:---------------|:---------------|:------|
| `agent_image` | No | Spec reuses correctly (line 169 of orchestrator) |
| `redis_url` | No | Spec reuses correctly |
| `max_job_duration_seconds` | No | Spec adds per-step timeout, does not modify this |
| `skill_registry_enabled` | No | Spec does not modify; coexists with IntentClassifier |
| `subagent_enabled` | WARN | Spec deprecates `spawn_subagent` MCP tool. Setting remains for backwards compat but spec notes it's replaced by fan-out. |
| `swarm_enabled` | No | Spec does not reference swarm |
| All other fields | No | No conflicts detected |

---

## 4. JobSpawner (`controller/src/controller/jobs/spawner.py`)

### Constructor

| Parameter | Spec Says | Code Has | Match? | Fix Needed |
|:----------|:----------|:---------|:-------|:-----------|
| `settings: Settings` | Yes | Yes | YES | -- |
| `batch_api: k8s.BatchV1Api` | Yes | Yes | YES | -- |
| `namespace: str` | Yes | `"default"` | YES | -- |

### Method: `spawn()`

| Aspect | Spec Says | Code Says | Match? | Fix Needed |
|:-------|:----------|:----------|:-------|:-----------|
| Signature | `spawn(thread_id, github_token, redis_url, agent_image?)` | `spawn(self, thread_id, github_token, redis_url, agent_image=None, extra_env=None) -> str` | YES | Spec omits `extra_env` but it's optional; no conflict |
| Return type | Implicit (used as `job_name = self._spawner.spawn(...)`) | `-> str` (returns `job.metadata.name`) | YES | -- |

### Method: `delete()` / `delete_job()`

| Aspect | Spec Says | Code Says | Match? | Fix Needed |
|:-------|:----------|:----------|:-------|:-----------|
| Name | `self._spawner.delete_job(job_name)` (in cancel()) | `self.delete(self, job_name: str)` | MISMATCH | Spec calls `delete_job` but method is `delete`. Either rename method or adjust spec. |

### Methods: `get_job_status()`, `get_job_result()`

| Method | Spec References | Code Has | Match? | Fix Needed |
|:-------|:----------------|:---------|:-------|:-----------|
| `get_job_status(job_name)` | Used in `reconcile()` | **Not present** | MISSING | Must implement: query K8s API for job status |
| `get_job_result(job_name)` | Used in `reconcile()` | **Not present** | MISSING | Must implement: read result from Redis for completed job |

---

## 5. RedisState (`controller/src/controller/state/redis_state.py`)

### Existing Methods

| Method | Spec Uses | Code Has | Match? | Fix Needed |
|:-------|:----------|:---------|:-------|:-----------|
| `push_task(thread_id, task_context)` | Yes (fan-out executor) | Yes, `set("task:{thread_id}", ..., ex=TASK_TTL)` | YES | -- |
| `get_task(thread_id)` | Yes (agent reads task) | Yes | YES | -- |
| `push_result(thread_id, result)` | Yes (agent posts result) | Yes, `set("result:{thread_id}", ..., ex=RESULT_TTL)` | YES | -- |
| `get_result(thread_id)` | Yes (engine reads result) | Yes | YES | -- |
| `queue_message(thread_id, message)` | Not directly used by spec | Yes | YES | No conflict |
| `drain_messages(thread_id)` | Not directly used by spec | Yes | YES | No conflict |

### Redis Key Patterns -- Conflict Check

| Spec Key Pattern | Existing Key Pattern | Conflict? | Notes |
|:-----------------|:---------------------|:----------|:------|
| `task:{thread_id}` | `task:{thread_id}` (line 14) | POTENTIAL | Workflow fan-out uses thread_ids like `{thread_id}:wf:{exec_id}:s:{step_id}:a:{index}`. These are unique per agent, so no collision with main thread task keys. Safe. |
| `result:{thread_id}` | `result:{thread_id}` (line 21) | POTENTIAL | Same as above -- workflow agent thread_ids are namespaced. Safe. |
| `df:intent_classify` | Not present | NO | New stream for intent classification |
| `df:intent_result:{thread_id}` | Not present | NO | New key for intent results |
| `queue:{thread_id}` | `queue:{thread_id}` (line 28) | NO | Spec does not modify queue behavior |
| `agent:{thread_id}` | `agent:{thread_id}` (line 39) | NO | Spec does not modify stream behavior |

---

## 6. Main (`controller/src/controller/main.py`)

### Orchestrator Construction

| Aspect | Spec Says | Code Says | Match? | Fix Needed |
|:-------|:----------|:----------|:-------|:-----------|
| Passes `workflow_engine` to Orchestrator | Yes (Phase 1) | Not present | MISSING | Add WorkflowEngine init + pass to Orchestrator |
| Feature flag check | `settings.workflow_engine_enabled` | Not present | MISSING | Add gated init block (same pattern as tracing/gateway) |
| Mount workflow API | `workflows/api.py` router | Not present | MISSING | Add `app.include_router(workflow_router)` |
| Reconcile on startup | `WorkflowEngine.reconcile()` call | Not present | MISSING | Add to lifespan startup |

### Import Path Check

| Spec Module Path | Valid in Current Structure? | Notes |
|:-----------------|:---------------------------|:------|
| `controller.workflows.engine` | Yes | `controller/src/controller/workflows/` does not exist yet; needs creation |
| `controller.workflows.compiler` | Yes | Same |
| `controller.workflows.executor` | Yes | Same |
| `controller.workflows.models` | Yes | Same |
| `controller.workflows.templates` | Yes | Same |
| `controller.workflows.intent` | Yes | Same |
| `controller.workflows.state` | Yes | Same |
| `controller.workflows.api` | Yes | Same |

All import paths follow the existing `controller.skills.*`, `controller.state.*` patterns. Module structure is consistent.

---

## 7. Entrypoint (`images/agent/entrypoint.sh`)

### Compatibility with Spec Changes

| Aspect | Spec Says | Code Has | Match? | Fix Needed |
|:-------|:----------|:----------|:-------|:-----------|
| Redis connection | `redis-cli -u "$REDIS_URL"` | `redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT"` (parsed from URL) | MISMATCH | Spec uses `-u` flag. Current code parses host/port manually. Stick with current approach (more robust for auth). Update spec. |
| Task JSON read | `GET "task:$THREAD_ID"` | Same (line 54) | YES | -- |
| `task_type` field read | `jq -r '.task_type // "code_change"'` | Not present -- current code does not route by task_type | MISSING | Must add `case` routing block |
| `output_schema` field read | `jq -r '.output_schema // empty'` | Not present | MISSING | Must add for non-code path |
| `workflow_context` field | Spec adds to task payload | Not read by entrypoint (contract says agents ignore it) | OK | Intentional -- agents don't read workflow_context |
| `code_change` path | Git clone, branch, claude, push | Lines 74-88, 129-166 | YES | Current code IS the code_change path |
| `analysis` path | No git clone, mkdir /workspace | Not present | MISSING | Must add non-code path |
| Claude invocation | `claude -p "$TASK" --output-format json` | `claude "${CLAUDE_ARGS[@]}"` with `--allowedTools '*'` | MISMATCH | Spec uses `--output-format json`; current code does not. Spec omits `--allowedTools`. Reconcile: keep `--allowedTools`, add `--output-format json` for non-code path. |
| Skill injection | Same pattern | Lines 91-112 | YES | Shared between both paths |
| Gateway MCP injection | Same pattern | Lines 116-126 | YES | Shared between both paths |
| Tracing | Spec does not modify tracing | Lines 14-31 (safe_trace helpers) | YES | No conflict |
| Result publishing | Spec: `redis-cli -u "$REDIS_URL" SET "result:$THREAD_ID"` | Lines 178-182: same key pattern | YES | Key format matches |

---

## 8. Summary of Required Changes

### Must Fix Before Implementation

| # | File | Change | Priority |
|:--|:-----|:-------|:---------|
| 1 | `models.py` | Add `workflow_execution_id`, `workflow_step_id` to `Job` | P0 |
| 2 | `models.py` | Add `result`, `provenance`, `quality` to `AgentResult` | P0 |
| 3 | `config.py` | Add all 11 workflow Settings fields | P0 |
| 4 | `orchestrator.py` | Add `workflow_engine` optional param to constructor | P0 |
| 5 | `main.py` | Add WorkflowEngine init + router mount + reconcile | P0 |
| 6 | `spawner.py` | Add `get_job_status()` and `get_job_result()` methods | P0 |
| 7 | `entrypoint.sh` | Add task_type routing (code_change vs analysis paths) | P1 |

### Spec Corrections Needed

| # | Spec Section | Issue | Fix |
|:--|:-------------|:------|:----|
| 1 | 5.2 `cancel()` | Calls `self._spawner.delete_job()` | Change to `self._spawner.delete()` to match actual method name |
| 2 | 7.1 Routing | Uses `redis-cli -u "$REDIS_URL"` | Change to match current host/port parsing pattern |
| 3 | 7.1 Routing | Missing `--allowedTools '*'` in Claude invocation | Add to spec's Claude command |
| 4 | 7.1 Routing | Missing `--system-prompt` flag | Add to spec's Claude command |
| 5 | 7.1 Routing | Does not handle `db_mutation` task_type | Add to routing case or document as unsupported |
| 6 | 2.3 Integration | `handle_agent_result` signature in spec has `agent_index` param | In completion hook (section 2.3 line 131), agent_index is missing from the call. Section 5.2 signature has it. Reconcile. |

### No Changes Needed (Confirmed Compatible)

- `RedisState` methods and key patterns
- `TaskRequest` dataclass fields
- `JobSpawner.spawn()` signature
- Module import path conventions
- `IntegrationRegistry` usage pattern
- Tracing infrastructure (coexists)
- Skill classification (coexists with IntentClassifier)
- Gateway MCP injection pattern
