# Agent Result Path Analysis

## Sequence Diagram

```
Agent Pod (entrypoint.sh)          Redis              Controller (orchestrator.py)        WorkflowEngine (engine.py)
    |                                |                        |                                |
    |  1. Agent finishes work        |                        |                                |
    |  2. Reads result.json if any   |                        |                                |
    |  3. SET result:{thread_id}  -->|                        |                                |
    |     JSON blob, TTL=3600        |                        |                                |
    |  4. exit                       |                        |                                |
    |                                |                        |                                |
    |                                |  5. _complete_job()    |                                |
    |                                |     called (bg task)   |                                |
    |                                |<-- GET result:{tid} ---|                                |
    |                                |  6. poll every 1s      |                                |
    |                                |     (max 60s)          |                                |
    |                                |--- result_data ------->|                                |
    |                                |                        |  7. Build AgentResult          |
    |                                |                        |  8. Persist to Job record      |
    |                                |                        |  9. Check workflow membership   |
    |                                |                        |     (active_job has             |
    |                                |                        |      workflow_execution_id?)    |
    |                                |                        |                                |
    |                                |                        |  10. handle_agent_result() --->|
    |                                |                        |      (exec_id, step_id,        |
    |                                |                        |       agent_index, result_dict) |
    |                                |                        |                                |
    |                                |                        |                     11. Lookup step
    |                                |                        |                     12. SEQUENTIAL:
    |                                |                        |                         store output,
    |                                |                        |                         mark COMPLETED
    |                                |                        |                     13. advance()
    |                                |                        |                         -> find next
    |                                |                        |                            runnable steps
    |                                |                        |                         -> _start_step()
    |                                |                        |                         -> or mark
    |                                |                        |                            execution done
```

## Answers

### 1. Exact Sequence

1. Agent writes `result.json` to filesystem (if `output_schema` was provided)
2. `entrypoint.sh` reads `result.json` into `RESULT_JSON` variable (defaults to `{}`)
3. Agent calls `redis-cli SET result:{THREAD_ID}` with JSON payload, TTL 3600s
4. Controller's `_complete_job(thread_id)` runs (triggered as a background task after job spawn)
5. `JobMonitor.wait_for_result()` polls Redis every 1s for up to 60s
6. `RedisState.get_result()` does `GET result:{thread_id}` and parses JSON
7. `AgentResult` dataclass built from result data
8. Result persisted to the Job record in state backend
9. If `active_job.workflow_execution_id` exists, forwards to workflow engine
10. `WorkflowEngine.handle_agent_result()` called with full result dict
11. For SEQUENTIAL steps: marks step COMPLETED with output=result, calls `advance()`
12. For FAN_OUT steps: stores per-agent result, checks if all done, then advances
13. `advance()` finds next runnable steps and calls `_start_step()` for each

### 2. Polling Loop (Not Pubsub)

The controller uses **polling**, not pubsub. `JobMonitor.wait_for_result()` loops with `asyncio.sleep(poll_interval)` checking Redis for the `result:{thread_id}` key. Default: 5s intervals, 1800s timeout (but orchestrator calls it with 1s/60s).

### 3. Agent Result Format

Redis key: `result:{thread_id}`, JSON blob:

```json
{
  "branch": "feature-xyz",
  "exit_code": 0,
  "commit_count": 3,
  "stderr": "",
  "task_type": "analysis",
  "result": { ... },          // <-- structured JSON from result.json
  "trace_events": [...]
}
```

The `result` field contains the structured output (read from `result.json` if the agent created one).

### 4. Can the Workflow Engine Extract Structured JSON?

**Partially -- there is a gap.** The orchestrator builds `result_dict` from `AgentResult` which only extracts: `branch`, `exit_code`, `commit_count`, `stderr`. The `result` field (containing the structured JSON) and `task_type` are **dropped** by `JobMonitor.wait_for_result()` because `AgentResult` does not include them.

The `handle_agent_result` receives `result_dict` (the AgentResult fields), NOT the raw Redis JSON. So the structured `result.json` content is **lost in transit**.

### 5. What Happens After handle_agent_result?

Yes, it **automatically advances**. For sequential steps, after marking the step COMPLETED, it calls `self.advance(execution_id)` which finds the next runnable steps and calls `_start_step()` for each. If all steps are terminal, it marks the entire execution COMPLETED or FAILED.

### 6. Identified Gaps

| # | Gap | Severity | Detail |
|---|-----|----------|--------|
| 1 | **Structured result dropped** | **CRITICAL** | `AgentResult` only captures `branch/exit_code/commit_count/stderr`. The `result` field (structured JSON from `result.json`) is never extracted from Redis. `handle_agent_result` receives a dict without it. | 
| 2 | **`task_type` dropped** | MEDIUM | Redis payload includes `task_type` but `AgentResult` ignores it. Workflow engine cannot distinguish result types. |
| 3 | **`trace_events` dropped** | LOW | Trace events from the agent are in Redis but never consumed by the controller. |
| 4 | **Polling, not event-driven** | LOW | The 1s polling loop works but adds latency. A Redis keyspace notification or pubsub would be more responsive. |
| 5 | **No `output_schema` validation** | MEDIUM | The entrypoint tells the agent to write `result.json` matching a schema, but never validates the output against that schema before publishing. |

### Fix for Gap #1 (Critical)

`JobMonitor.wait_for_result()` must pass through the `result` field. Either:
- Add a `result_payload` field to `AgentResult`
- Or have the orchestrator read the raw Redis data directly instead of going through `AgentResult`

Then the orchestrator must include it in `result_dict` before calling `handle_agent_result()`.
