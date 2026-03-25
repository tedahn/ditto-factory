# K8s Integration Analysis: Agent Communication Protocol

**Date:** 2026-03-25
**Reviewed spec:** `2026-03-25-agent-communication-protocol-design.md`
**Reviewed code:** `controller/src/controller/jobs/spawner.py`

---

## 1. Agent Spawning: Sequential Job Creation

**CONCERN**

The spec spawns N agents in a `for` loop calling `self._spawner.spawn()` sequentially (line 348-359 of spec). Each `create_namespaced_job` is a synchronous K8s API call (~50-200ms). At 10 agents that is 0.5-2s -- acceptable. At 100+ agents ("do it for the entire world"), that is 5-20s of serial API calls, plus scheduler queuing.

**RECOMMEND**
- Immediate: Use `asyncio.gather()` to parallelize the `create_namespaced_job` calls. The K8s Python client supports async or you can use `run_in_executor` for the sync client.
- At 50+ agents: Switch to a K8s **Job with `completions` + `completionMode: Indexed`** (Indexed Jobs, GA since 1.24). Each pod gets a `JOB_COMPLETION_INDEX` env var. One API call creates all pods. The controller maps index to AGENT_ID.
- Alternative: Use a custom CRD + operator pattern if swarm lifecycle management becomes complex enough to warrant it.

---

## 2. Resource Contention: 500m CPU / 2Gi RAM per Agent

**CONCERN**

The spawner reads `agent_cpu_request` and `agent_memory_request` from Settings. If defaults are 500m/2Gi, then 10 agents = 5 CPU / 20Gi committed. Research agents that mostly make API calls and wait for responses are heavily over-provisioned. This blocks scheduling of other workloads and artificially limits swarm size.

**RECOMMEND**
- Set swarm agent defaults lower: **100m CPU / 512Mi RAM requests**, with limits at 500m/1Gi. The Claude Code process + MCP server + Redis client does not need 2Gi while waiting on HTTP responses.
- Add `SwarmAgent`-specific resource overrides in `SwarmGroup.config` so the orchestration layer (Subsystem 2) can tune per-role. An aggregator doing JSON merges needs different resources than a researcher doing API calls.
- Add a swarm-level resource budget: `max_cpu_total` / `max_memory_total` in config, checked before spawning.

---

## 3. Agent Crash + Respawn: Job Name Uniqueness

**CONCERN**

K8s Job names must be unique within a namespace. The current naming scheme is `df-{short_id}-{ts}` (spawner.py line 28). The spec says "respawn with same AGENT_ID so it resumes from stream cursor." But the K8s Job name != AGENT_ID. The respawned Job will get a new name (different timestamp). This is fine for K8s, but the code must:

1. Delete the old failed Job first (or let `ttlSecondsAfterFinished` handle it)
2. Create a new Job with a new name but the same `AGENT_ID` env var
3. Update `SwarmAgent.k8s_job_name` in the state backend

**SOUND** -- The design works as long as the implementation updates the job name mapping. The stream cursor resumes correctly because the consumer group (`agent-{agent_id}`) persists in Redis independently of the K8s Job.

**RECOMMEND**
- Add a `df/agent-id` label to the Job metadata so the controller can query Jobs by agent ID for monitoring.
- Set `backoff_limit=0` (not 1) for swarm agents -- let the SwarmMonitor handle respawn logic rather than K8s built-in retry, which would create a second pod with no coordination.

---

## 4. Networking: Redis-Only vs Direct Pod-to-Pod

**SOUND**

Redis Streams is the correct choice. Direct pod-to-pod communication would require:
- Service discovery (agents need each other's pod IPs)
- Connection management (what if a peer crashes mid-transfer?)
- A custom protocol (gRPC, WebSocket) adding implementation complexity

Redis provides durability, ordering, and crash recovery for free. The only scenario where direct communication might help is streaming large binary data between agents (e.g., files, images), which is not in scope.

**RECOMMEND**
- Add a `NetworkPolicy` per swarm group that restricts agent pod egress to: (a) Redis, (b) external APIs (for research), (c) the K8s API server (not needed -- deny it). This limits blast radius if an agent is compromised.
- Label swarm pods with `df/swarm-group: {group_id}` to enable per-group NetworkPolicy selectors.

```yaml
# Example NetworkPolicy (add to Helm chart)
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: swarm-{{ group_id }}-egress
spec:
  podSelector:
    matchLabels:
      df/swarm-group: "{{ group_id }}"
  policyTypes: [Egress]
  egress:
    - to:
        - podSelector:
            matchLabels:
              app: redis
      ports:
        - port: 6379
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8    # Block intra-cluster except Redis
              - 172.16.0.0/12
              - 192.168.0.0/16
      ports:
        - port: 443
```

---

## 5. Scaling to 100+ Agents

**CONCERN**

At 100+ agents, several K8s-level limits become relevant:

| Concern | Threshold | Impact |
|---------|-----------|--------|
| API rate limiting | ~100 QPS default per client | 100 serial Job creates + monitoring polls can hit throttling |
| Scheduler pressure | Depends on cluster size | 100 pending pods compete for scheduling; if resources are tight, agents queue for minutes |
| etcd object count | ~8K objects per resource type is comfortable | 100 Jobs + 100 Pods + labels/status = fine, but watch for accumulation across swarms if cleanup is slow |
| Redis consumer groups | 100 consumer groups per stream | Redis handles this, but XREADGROUP on 100 groups creates O(N) memory per stream entry |
| Controller monitoring | Polling 100 agent heartbeats every 30s | Must be batched, not per-agent API calls |

**RECOMMEND**
- Use Indexed Jobs (see point 1) to reduce API calls from 100 to 1.
- Set `ttlSecondsAfterFinished: 60` (not 300) for swarm agents -- faster cleanup reduces etcd pressure.
- Implement controller-side heartbeat monitoring with a single `HGETALL` on the agents hash (O(1) Redis call for all agents) rather than per-agent checks.
- Add a cluster-level swarm quota: max total concurrent swarm agents across all groups. Check available cluster capacity (via Metrics API or resource quotas) before spawning.
- For the "entire world" scenario (100+ agents), consider a two-tier architecture: a small number of "regional coordinator" agents that each spawn sub-swarms, keeping any single swarm under 20 agents.

---

## 6. `swarm_wait_for_peers` Deadlock on Scheduling Failure

**CONCERN -- CRITICAL**

The spec's `swarm_wait_for_peers(min_agents=N, timeout_seconds=120)` blocks until N agents self-register as ACTIVE. If even one Job fails to schedule (insufficient CPU/memory, image pull failure, node pressure), the remaining agents wait until timeout. At 120s timeout, this wastes agent compute time (and API credits for Claude).

Worse: the controller sets `agent.status = AgentStatus.ACTIVE` immediately after calling `spawn()` (spec line 361), but this is the *controller's* state -- the agent registry in Redis still shows `pending` until the MCP server boots. There is a mismatch between controller state and Redis state.

**RECOMMEND**
- The controller should NOT set status to ACTIVE on spawn. It should remain PENDING until the agent self-reports. Fix spec line 361.
- Add a **scheduling watchdog** in SwarmMonitor: if a Job's pod stays in `Pending` phase for > 60s, the controller should:
  1. Check pod events (`kubectl describe pod` equivalent via K8s API) for `FailedScheduling` / `ImagePullBackOff`
  2. Publish a control message to the swarm: `{"action": "peer_failed", "agent_id": "...", "reason": "scheduling_failed"}`
  3. Reduce the expected agent count so `swarm_wait_for_peers` can unblock with fewer agents
  4. Mark the agent as `failed` in the registry
- Add a `swarm_wait_for_peers` parameter: `required_agents` vs `min_agents`. Required agents failing should unblock with an error. Optional agents failing should unblock with a degraded roster.
- The MCP server should expose the timeout reason to the agent so it can adapt its strategy (e.g., "only 3 of 5 researchers started, I'll cover more sources myself").

---

## Summary Table

| # | Topic | Verdict | Priority |
|---|-------|---------|----------|
| 1 | Sequential spawning | CONCERN | Medium -- fine at 10, blocks at 100 |
| 2 | Resource requests | CONCERN | High -- over-provisioning limits scale |
| 3 | Crash + respawn naming | SOUND (with caveats) | Low -- needs label + backoff_limit fix |
| 4 | Redis-only networking | SOUND | Low -- add NetworkPolicy for defense-in-depth |
| 5 | 100+ agent scaling | CONCERN | Medium -- plan for it now, implement later |
| 6 | wait_for_peers deadlock | CONCERN (CRITICAL) | High -- can waste all agent compute on timeout |
