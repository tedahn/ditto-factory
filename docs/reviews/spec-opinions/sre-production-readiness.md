# SRE Production Readiness Review: Two-State Workflow Engine

**Reviewer:** SRE Agent
**Date:** 2026-03-25
**Spec:** `docs/superpowers/specs/2026-03-25-workflow-engine-design.md`
**Verdict:** CONDITIONAL APPROVAL -- ship Phase 1 after addressing P0 gaps below

---

## Risk Matrix

Likelihood: L=Low, M=Medium, H=High
Impact: 1=Minor, 2=Moderate, 3=Service-degrading, 4=Data-loss/outage

| ID | Failure Mode | Likelihood | Impact | Risk Score | Status in Spec |
|----|-------------|-----------|--------|------------|----------------|
| F1 | Controller crashes mid-workflow (advance loop) | M | 3 | **6** | Not addressed |
| F2 | Agent pod OOM during fan-out | H | 2 | **4** | Partially (timeout, on_failure) |
| F3 | Redis goes down | L | 4 | **4** | Partially (results also in Postgres) |
| F4 | Postgres connection pool exhausted by fan-out | M | 4 | **8** | Not addressed |
| F5 | LLM API 500 during intent classification | M | 1 | **2** | Addressed (rule-based fallback) |
| F6 | Workflow stuck in "running" forever | M | 3 | **6** | Not addressed |
| F7 | Orphaned K8s jobs after cancel/crash | M | 2 | **4** | Partially (cancel deletes jobs) |
| F8 | Cartesian explosion (50 regions x 10 sources = 500 agents) | L | 4 | **4** | Partially (max_parallel) |
| F9 | Redis memory exhaustion from result payloads | M | 3 | **6** | Not addressed |
| F10 | Race condition in advance() with concurrent agent completions | H | 3 | **6** | Not addressed |

---

## 1. Failure Modes -- Specific Gaps

### F1: Controller crash mid-workflow (P0)

**Problem:** `advance()` is called in-process after an agent result arrives. If the controller pod restarts between storing the agent result and calling `advance()`, the workflow stalls permanently. There is no recovery loop.

**Mitigation:**
- Add a periodic reconciliation loop (every 60s) that queries `workflow_executions WHERE status = 'running'` and calls `advance()` on each. This makes the engine crash-recoverable. The `advance()` function is already idempotent (checks pending steps whose deps are completed).
- Add a `last_heartbeat` column to `workflow_executions`. Alert if `now() - last_heartbeat > 5m` for running executions.

### F4: Postgres connection pool exhaustion (P0)

**Problem:** Fan-out of N agents = N concurrent `handle_agent_result()` calls = N concurrent Postgres transactions (get_step, get_step_agents, complete_step, advance). With 20 agents completing within seconds, this can saturate a 10-connection pool.

**Mitigation:**
- Use a bounded semaphore on `handle_agent_result()` (e.g., max 5 concurrent DB transactions per workflow).
- Or batch agent results: write to Redis first, process in a single DB transaction per step completion.
- Document minimum pool size: `max_parallel_agents + controller_base_connections + 5 headroom`.

### F6: Stuck workflows (P0)

**Problem:** No mechanism detects a workflow that is `running` but has no `running` or `pending` steps with actionable state. This can happen if a K8s job silently fails (no completion callback).

**Mitigation:**
- Add a `workflow_timeout_seconds` field (default: `sum(step_timeouts) * 1.5`).
- Reconciliation loop marks workflows as `failed` if they exceed this timeout.
- Add K8s job watcher that detects jobs in `Failed` state and calls `handle_agent_result` with error.

### F10: Race condition in advance() (P0)

**Problem:** Two agents from the same fan-out step complete simultaneously. Both call `handle_agent_result()`. Both see "all agents done". Both call `complete_step()` and `advance()`. This can cause duplicate step execution.

**Mitigation:**
- Use Postgres advisory lock or `SELECT ... FOR UPDATE` on the step row before checking completion.
- Or use Redis distributed lock keyed on `step:{step_id}:completing`.
- The spec's existing Redis lock pattern for threads should be extended to step completion.

---

## 2. Observability Gaps

### What the spec has:
- Phase 3 mentions "tracing spans for workflow engine events" -- one line, no detail.

### What an oncall engineer needs at 3am:

| Signal | What to Emit | Missing from Spec |
|--------|-------------|-------------------|
| **Metric** | `workflow.execution.duration_seconds` (histogram, by template_slug) | Yes |
| **Metric** | `workflow.step.duration_seconds` (histogram, by step_type) | Yes |
| **Metric** | `workflow.fan_out.agents_total` (gauge, by execution_id) | Yes |
| **Metric** | `workflow.fan_out.agents_failed` (counter, by template_slug) | Yes |
| **Metric** | `workflow.active_executions` (gauge) | Yes |
| **Metric** | `workflow.advance.calls_total` (counter, distinguish no-op vs productive) | Yes |
| **Log** | Structured log on every state transition with execution_id, step_id, old_status, new_status | Yes |
| **Trace** | Parent span per execution, child span per step, grandchild span per agent | Yes |
| **Alert** | Burn-rate alert on workflow failure rate (SLO: 99% of workflows complete successfully) | Yes |
| **Alert** | Stuck workflow detection (running > timeout, no step progress in 10m) | Yes |
| **Dashboard** | Workflow execution funnel: started -> steps_completed -> delivered | Yes |

**Recommendation:** Do not ship Phase 2 (fan-out) without metrics and structured logging. Tracing can wait for Phase 3, but metrics and logs are day-1 requirements for fan-out.

---

## 3. Resource Management -- Blast Radius Analysis

### Fan-out of 50 agents:

| Resource | Impact | Current Limit | Gap |
|----------|--------|--------------|-----|
| K8s pods | 50 pods * ~1 CPU, ~2GB RAM = 50 CPU, 100GB RAM | `max_parallel=10` per step | No **global** limit across concurrent workflows |
| Redis memory | 50 result payloads * ~100KB each = ~5MB per workflow | 1hr TTL | No per-workflow memory budget; 10 concurrent workflows = 50MB |
| Postgres connections | 50 concurrent result handlers | Pool size (unspecified) | No connection budget documented |
| LLM API | 50 concurrent Claude calls | None mentioned | No rate limiting to LLM provider |
| K8s node autoscaler | 50 pods may trigger scale-up, 10min lag | Cluster-dependent | No pod priority/preemption class specified |

**Specific gaps:**
1. `DF_WORKFLOW_MAX_PARALLEL_AGENTS=20` is per-workflow but there is no **system-wide** agent cap. 5 concurrent workflows = 100 agents.
2. No pod priority class. Workflow agents should be lower priority than the controller itself.
3. No resource requests/limits specified for agent pods in the spec.
4. Redis result TTL of 1hr (`EX 3600`) means results accumulate. If a workflow fails and is retried, old results are still in Redis.

**Recommendations:**
- Add `DF_WORKFLOW_GLOBAL_MAX_AGENTS` (e.g., 50) enforced by a global semaphore or K8s ResourceQuota.
- Specify PriorityClass: controller=system-critical, workflow-agents=low.
- Set Redis result keys with workflow-scoped TTL, not fixed 1hr.
- Add resource requests/limits to agent pod spec: `requests: {cpu: 500m, memory: 1Gi}`, `limits: {cpu: 1, memory: 2Gi}`.

---

## 4. Recovery -- Can Workflows Resume After Crash?

| Scenario | Recoverable? | How? |
|----------|-------------|------|
| Controller restarts | **No** (gap) | Needs reconciliation loop (see F1) |
| Agent pod OOM-killed | **Yes** | K8s job shows Failed; result handler marks agent failed; step retries |
| Redis restart (data loss) | **Partial** | Agent results in transit are lost. Completed results are in Postgres. Pending tasks in Redis Stream are lost. |
| Postgres failover | **Yes** | Standard HA Postgres; engine retries on connection error |
| Agent completes but result callback fails | **No** (gap) | Result is in Redis but never processed. Needs reconciliation. |

### Orphaned Resource Cleanup

The spec's `cancel()` method deletes K8s jobs for running steps, but:

1. **No garbage collector for completed/failed workflow resources.** K8s jobs with `ttlSecondsAfterFinished` should be set but is not mentioned.
2. **No cleanup of Redis keys.** `task:{thread_id}` and `result:{thread_id}` keys for workflow agents are created but never explicitly deleted on workflow completion.
3. **No cleanup of workflow_step_agents records.** These grow unbounded. Need a retention policy.

**Recommendations:**
- Set `ttlSecondsAfterFinished: 300` on all K8s jobs spawned by the workflow engine.
- Add a cleanup step to `complete_execution()` and `fail_execution()` that deletes Redis keys for all agents in the workflow.
- Add a data retention cron: delete `workflow_steps` and `workflow_step_agents` older than 30 days, archive `workflow_executions` older than 90 days.

---

## 5. Rollback Assessment

**Feature flag rollback:** `DF_WORKFLOW_ENGINE_ENABLED=false` is clean. Single-agent path is preserved. This is well-designed.

**Gaps in rollback:**

| Scenario | Issue |
|----------|-------|
| Rollback during active workflows | Running workflows become orphaned. Agent jobs continue running. No drain mechanism. |
| Schema rollback | New tables and columns (`workflow_execution_id` on `jobs`) persist. Column is nullable so no harm, but no down-migration documented. |
| Intent classifier rollback | `DF_INTENT_CLASSIFIER_ENABLED=false` should work independently. Confirmed in spec. |

**Recommendations:**
- Add a drain mode: `DF_WORKFLOW_ENGINE_ENABLED=drain` -- stop accepting new workflows, let running ones complete, then disable.
- Document Alembic down-migration for all schema changes.
- Add a runbook for "rollback workflow engine" that includes: set drain mode, wait for active workflows (query count), set disabled, verify single-agent path works.

---

## 6. Missing Operational Runbooks

| Runbook | Priority | Description |
|---------|----------|-------------|
| **Stuck workflow investigation** | P0 | How to find stuck workflows, diagnose which step/agent is blocked, manually advance or fail |
| **Fan-out cost runaway** | P0 | How to cancel all workflows, reduce max_parallel live, identify cost anomalies |
| **Redis failure during workflow** | P1 | How to recover workflows when Redis loses result data, manual result injection |
| **Agent pod debugging** | P1 | How to get logs from a specific workflow agent pod (pod naming convention, label selectors) |
| **Template rollback** | P1 | How to revert a template to a previous version, impact on running executions |
| **Capacity planning** | P2 | How to size Redis memory, Postgres pool, K8s node pool for N concurrent workflows |
| **Workflow replay** | P2 | How to re-run a failed workflow from a specific step (not from scratch) |

---

## 7. Operational Readiness Checklist

### Before Phase 1 ships:

- [ ] Reconciliation loop for crash recovery (F1)
- [ ] Advisory lock or mutex on step completion (F10)
- [ ] Structured logging on all state transitions
- [ ] Workflow timeout with automatic failure (F6)
- [ ] Alembic down-migrations for all schema changes
- [ ] Feature flag verified: disable -> all traffic goes to single-agent

### Before Phase 2 (fan-out) ships:

- [ ] Global agent cap (`DF_WORKFLOW_GLOBAL_MAX_AGENTS`)
- [ ] Postgres connection pool sizing documented and tested under fan-out load
- [ ] Metrics: execution duration, step duration, active executions, agent failure rate
- [ ] PriorityClass for agent pods
- [ ] K8s job TTL (`ttlSecondsAfterFinished`)
- [ ] Redis key cleanup on workflow completion
- [ ] Load test: 5 concurrent geo-search workflows (20 agents each)
- [ ] Runbook: stuck workflow investigation
- [ ] Runbook: fan-out cost runaway

### Before Phase 3 (intent + production) ships:

- [ ] Distributed tracing (parent span per execution, child per step)
- [ ] SLO defined: workflow completion rate, p99 duration by template
- [ ] Burn-rate alerting on workflow SLO
- [ ] Dashboard: workflow execution funnel
- [ ] Drain mode for rollback
- [ ] Data retention policy for workflow tables
- [ ] Runbook: all 7 runbooks from section 6
- [ ] Chaos test: kill controller mid-workflow, verify recovery
- [ ] Chaos test: Redis restart during fan-out, verify no data loss

---

## 8. SLO Recommendation

```yaml
service: workflow-engine
slos:
  - name: Workflow Completion Rate
    sli: count(status == "completed") / count(status IN ("completed", "failed"))
    target: 99%
    window: 30d
    burn_rate_alerts:
      - severity: critical
        factor: 14.4
        short_window: 5m
        long_window: 1h

  - name: Workflow Duration (p95)
    description: Time from start to completion for geo-search template
    sli: count(duration < 600s) / count(total)
    target: 95%
    window: 30d

  - name: Agent Success Rate
    description: Individual agent task completion within fan-out
    sli: count(agent_status == "completed") / count(total_agents)
    target: 95%
    window: 30d
```

---

## Summary

The spec is architecturally sound. The two-state model, DAG-only constraint, and feature flag rollback are well-thought-out decisions. The main gaps are in **crash recovery** (no reconciliation loop), **concurrency safety** (race condition in `advance()`), **resource limits** (no global agent cap), and **observability** (metrics and logging are afterthoughts in Phase 3 but should be in Phase 1-2).

The 4 P0 items (F1, F4, F6, F10) must be addressed before fan-out goes to production. Phase 1 (single-task wrapper) is low-risk and can ship with just F1 and F10 fixed, since fan-out is not involved.
