# K8s Agent Swarm Scaling Fixes

**Date:** 2026-03-25
**Status:** Proposal
**Addresses:** swarm_wait_for_peers deadlock, sequential spawning, resource over-provisioning, 100+ agent scaling, controller HA

---

## 1. Scheduling Watchdog (CRITICAL: swarm_wait_for_peers Deadlock)

### Problem

If agent Job N cannot be scheduled (insufficient cluster resources), agents 1..N-1 call `swarm_wait_for_peers(min_agents=N)` and block forever, burning compute on idle pods.

### Design: SchedulingWatchdog (runs in the controller)

The watchdog is a periodic async task inside the controller process (not a separate deployment). It runs every 15 seconds per active swarm group.

#### Detection: K8s Event Watch for FailedScheduling

```python
class SchedulingWatchdog:
    """Detects unschedulable swarm Jobs and adjusts peer expectations."""

    def __init__(self, core_api: k8s.CoreV1Api, batch_api: k8s.BatchV1Api,
                 redis: Redis, namespace: str):
        self._core_api = core_api
        self._batch_api = batch_api
        self._redis = redis
        self._namespace = namespace
        self._unschedulable_jobs: dict[str, datetime] = {}  # job_name -> first_seen

    async def check_group(self, group: SwarmGroup) -> None:
        """Called every 15s per active swarm group."""
        for agent in group.agents:
            if agent.status != AgentStatus.PENDING:
                continue
            job_name = agent.k8s_job_name
            if not job_name:
                continue

            # 1. Check Pod status via K8s API
            # List pods by job-name label (Jobs create pods with job-name label)
            pods = self._core_api.list_namespaced_pod(
                namespace=self._namespace,
                label_selector=f"job-name={job_name}",
            )

            for pod in pods.items:
                if pod.status.phase == "Pending":
                    # Check for FailedScheduling condition
                    if pod.status.conditions:
                        for cond in pod.status.conditions:
                            if (cond.type == "PodScheduled"
                                    and cond.status == "False"
                                    and cond.reason == "Unschedulable"):
                                self._mark_unschedulable(job_name, agent)

            # 2. Timeout check: if pending > 120s with no pod at all, treat as stuck
            if not pods.items and agent.k8s_job_name in self._unschedulable_jobs:
                elapsed = (datetime.now(timezone.utc)
                           - self._unschedulable_jobs[job_name])
                if elapsed.total_seconds() > 120:
                    await self._handle_unschedulable(group, agent)

    def _mark_unschedulable(self, job_name: str, agent: SwarmAgent) -> None:
        if job_name not in self._unschedulable_jobs:
            self._unschedulable_jobs[job_name] = datetime.now(timezone.utc)
            logger.warning(f"Job {job_name} (agent {agent.id}) detected as unschedulable")

    async def _handle_unschedulable(self, group: SwarmGroup, agent: SwarmAgent) -> None:
        """After grace period, mark agent failed and adjust peer count."""
        # 1. Update agent status in state backend
        agent.status = AgentStatus.FAILED
        await self._state.update_swarm_agent(
            group.id, agent.id, AgentStatus.FAILED,
            result_summary={"error": "FailedScheduling", "reason": "insufficient_resources"}
        )

        # 2. Update Redis registry so swarm_wait_for_peers sees it
        registry_key = f"swarm:{group.id}:agents"
        agent_data = json.loads(await self._redis.hget(registry_key, agent.id))
        agent_data["status"] = "failed"
        agent_data["error"] = "Could not be scheduled - insufficient cluster resources"
        await self._redis.hset(registry_key, agent.id, json.dumps(agent_data))

        # 3. Publish adjusted peer count to control stream
        # This is how waiting agents learn they should stop waiting
        active_count = sum(
            1 for a in group.agents
            if a.status in (AgentStatus.ACTIVE, AgentStatus.PENDING)
            and a.id != agent.id
        )
        control_msg = {
            "action": "peer_count_adjusted",
            "original_count": len(group.agents),
            "adjusted_count": active_count,
            "failed_agent": agent.id,
            "reason": "insufficient_resources",
        }
        await self._redis.xadd(
            f"swarm:{group.id}:control",
            {"data": json.dumps(control_msg)},
        )

        # 4. Delete the stuck Job to free the API object
        try:
            self._batch_api.delete_namespaced_job(
                name=agent.k8s_job_name,
                namespace=self._namespace,
                body=k8s.V1DeleteOptions(propagation_policy="Background"),
            )
        except k8s.ApiException:
            pass

        # 5. Remove from unschedulable tracking
        self._unschedulable_jobs.pop(agent.k8s_job_name, None)
```

#### MCP-Side: swarm_wait_for_peers Must Listen to Control Stream

The `swarm_wait_for_peers` tool must be updated to read the control stream in addition to polling the registry hash:

```javascript
// In df-swarm-comms MCP server
async function swarmWaitForPeers({ min_agents = 2, timeout_seconds = 120 }) {
    const deadline = Date.now() + timeout_seconds * 1000;
    let adjustedMinAgents = min_agents;

    while (Date.now() < deadline) {
        // 1. Check control stream for peer_count_adjusted messages
        const controlMsgs = await redis.xread(
            'COUNT', 10, 'BLOCK', 2000,
            'STREAMS', `swarm:${groupId}:control`, lastControlId
        );
        for (const msg of controlMsgs) {
            if (msg.action === 'peer_count_adjusted') {
                adjustedMinAgents = Math.min(adjustedMinAgents, msg.adjusted_count);
                // Log so the agent knows the situation changed
                console.log(`Peer count adjusted from ${min_agents} to ${adjustedMinAgents}`);
            }
        }

        // 2. Check registry for active agents
        const agents = await redis.hgetall(`swarm:${groupId}:agents`);
        const activeAgents = Object.entries(agents)
            .filter(([_, data]) => JSON.parse(data).status === 'active');

        if (activeAgents.length >= adjustedMinAgents) {
            return { agents: activeAgents, adjusted: adjustedMinAgents !== min_agents };
        }

        // 3. If ALL non-self agents are failed, exit immediately
        const failedCount = Object.entries(agents)
            .filter(([_, data]) => JSON.parse(data).status === 'failed').length;
        const totalOthers = Object.keys(agents).length - 1; // exclude self
        if (failedCount >= totalOthers) {
            throw new Error('All peers failed to schedule');
        }

        await sleep(2000);
    }

    throw new Error(`Timed out waiting for ${adjustedMinAgents} peers`);
}
```

#### Configuration

```python
# New settings in controller/src/controller/config.py
scheduling_watchdog_interval_seconds: int = 15     # How often to check each group
scheduling_unschedulable_grace_seconds: int = 120  # Time before marking agent failed
```

#### Integration Point

The watchdog runs as an asyncio task inside the controller's existing event loop:

```python
# In controller startup (e.g., orchestrator.py or main.py)
watchdog = SchedulingWatchdog(core_api, batch_api, redis, namespace)

async def watchdog_loop():
    while True:
        active_groups = await state.list_active_swarm_groups()
        for group in active_groups:
            await watchdog.check_group(group)
        await asyncio.sleep(settings.scheduling_watchdog_interval_seconds)

asyncio.create_task(watchdog_loop())
```

---

## 2. Parallel Job Spawning

### Problem

The current `create_swarm` loop calls `self._spawner.spawn()` sequentially. Each K8s API call takes 50-200ms. At 100 agents, that is 5-20 seconds of blocking.

### Tiered Approach

| Scale | Strategy | Implementation |
|-------|----------|----------------|
| 1-10 agents | `asyncio.gather()` with concurrent API calls | Simple, direct |
| 10-50 agents | `asyncio.gather()` with semaphore (max 20 concurrent) | Prevents K8s API overload |
| 50-200 agents | Batched `asyncio.gather()` + K8s Indexed Jobs | Fewer API objects |
| 200+ agents | Two-tier coordinator pattern (see Section 4) | Distributes API pressure |

### Implementation: Async Spawner

The existing `JobSpawner.spawn()` is synchronous. We need an async wrapper:

```python
class AsyncJobSpawner:
    """Wraps JobSpawner with async K8s API calls and concurrency control."""

    def __init__(self, spawner: JobSpawner, max_concurrent: int = 20):
        self._spawner = spawner
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def spawn_one(self, **kwargs) -> str:
        """Spawn a single Job with concurrency control."""
        async with self._semaphore:
            job = self._spawner.build_job_spec(**kwargs)
            # Run the blocking K8s API call in a thread pool
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self._spawner._batch_api.create_namespaced_job,
                self._spawner._namespace,
                job,
            )
            return job.metadata.name

    async def spawn_batch(self, agent_specs: list[dict]) -> list[str]:
        """Spawn all agents concurrently, respecting semaphore."""
        tasks = [self.spawn_one(**spec) for spec in agent_specs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        succeeded = []
        failed = []
        for spec, result in zip(agent_specs, results):
            if isinstance(result, Exception):
                failed.append((spec, result))
                logger.error(f"Failed to spawn agent: {result}")
            else:
                succeeded.append(result)

        if failed:
            logger.warning(f"{len(failed)}/{len(agent_specs)} agents failed to submit")

        return succeeded
```

### Updated create_swarm

```python
async def create_swarm(self, thread_id: str, agents: list[SwarmAgent], config: dict) -> SwarmGroup:
    group = SwarmGroup(...)
    await self._state.create_swarm_group(group)
    await self._create_redis_streams(group.id)
    await self._create_agent_registry(group)

    # Build spawn specs
    agent_specs = [
        {
            "thread_id": agent.id,
            "github_token": "",
            "redis_url": self._settings.redis_url,
            "agent_image": self._resolve_agent_image(agent),
            "extra_env": {
                "SWARM_GROUP_ID": group.id,
                "AGENT_ID": agent.id,
                "AGENT_ROLE": agent.role,
            },
        }
        for agent in agents
    ]

    # Parallel spawn with error handling
    job_names = await self._async_spawner.spawn_batch(agent_specs)

    # Map results back to agents
    for agent, job_name in zip(agents, job_names):
        agent.k8s_job_name = job_name

    group.status = SwarmStatus.ACTIVE
    await self._state.update_swarm_status(group.id, SwarmStatus.ACTIVE)
    return group
```

### Why NOT K8s Indexed Jobs

K8s Indexed Jobs (`.spec.completionMode: Indexed`) assign each pod a `JOB_COMPLETION_INDEX` and are designed for homogeneous workloads. Our agents are heterogeneous (different roles, images, task assignments, env vars). Indexed Jobs would require:

- A single container image for all roles
- A sidecar or init container to fetch role-specific config from Redis based on the index
- Loss of per-agent resource profiles (Section 3)

The complexity is not justified until 500+ homogeneous agents. For heterogeneous swarms, `asyncio.gather()` with a semaphore is the correct approach.

---

## 3. Per-Role Resource Profiles

### Problem

The current spawner applies a single resource profile (`agent_cpu_request`/`agent_memory_request` from Settings) to all agents. The review noted 500m CPU / 2Gi RAM is over-provisioned for research agents.

### Resource Profile Table

| Role | CPU Request | CPU Limit | Memory Request | Memory Limit | Rationale |
|------|------------|-----------|----------------|--------------|-----------|
| `researcher` | 100m | 250m | 256Mi | 512Mi | Mostly waiting on HTTP API calls. CPU-idle 95% of the time. Memory for Node.js MCP servers + response buffering. |
| `coder` | 500m | 1000m | 1Gi | 2Gi | Claude Code runs git operations, file I/O, linting. Needs burst CPU for compilation. Memory for large repo clones. |
| `aggregator` | 250m | 500m | 512Mi | 1Gi | JSON processing, deduplication, merging large datasets. Moderate CPU for data manipulation. |
| `planner` | 100m | 250m | 256Mi | 512Mi | Similar to researcher - mostly LLM API calls with minimal local compute. |
| `default` | 250m | 500m | 512Mi | 1Gi | Fallback for unknown roles. |

### Cost Impact at 100 Agents (80 researchers, 15 coders, 5 aggregators)

| Profile | Current (uniform) | Right-sized | Savings |
|---------|-------------------|-------------|---------|
| CPU requests | 50,000m (50 cores) | 80*100 + 15*500 + 5*250 = 16,750m (~17 cores) | **66% reduction** |
| Memory requests | 200Gi | 80*256 + 15*1024 + 5*512 = 38,560Mi (~38Gi) | **81% reduction** |

This directly translates to fitting more agents per node and reducing FailedScheduling events.

### Implementation

```python
# New file: controller/src/controller/jobs/resource_profiles.py

from dataclasses import dataclass

@dataclass(frozen=True)
class ResourceProfile:
    cpu_request: str
    cpu_limit: str
    memory_request: str
    memory_limit: str

# Profiles are intentionally conservative. Tune based on Prometheus metrics
# after running production swarms (see monitoring section below).
ROLE_PROFILES: dict[str, ResourceProfile] = {
    "researcher": ResourceProfile("100m", "250m", "256Mi", "512Mi"),
    "coder":      ResourceProfile("500m", "1000m", "1Gi", "2Gi"),
    "aggregator": ResourceProfile("250m", "500m", "512Mi", "1Gi"),
    "planner":    ResourceProfile("100m", "250m", "256Mi", "512Mi"),
    "default":    ResourceProfile("250m", "500m", "512Mi", "1Gi"),
}

def get_profile(role: str) -> ResourceProfile:
    return ROLE_PROFILES.get(role, ROLE_PROFILES["default"])
```

### Integration with Spawner

```python
# In JobSpawner.build_job_spec(), replace the static resource block:

from controller.jobs.resource_profiles import get_profile

def build_job_spec(self, ..., agent_role: str | None = None) -> k8s.V1Job:
    # Use role-based profile if provided, fall back to settings
    if agent_role:
        profile = get_profile(agent_role)
        resources = k8s.V1ResourceRequirements(
            requests={"cpu": profile.cpu_request, "memory": profile.memory_request},
            limits={"cpu": profile.cpu_limit, "memory": profile.memory_limit},
        )
    else:
        resources = k8s.V1ResourceRequirements(
            requests={"cpu": self._settings.agent_cpu_request,
                      "memory": self._settings.agent_memory_request},
            limits={"cpu": self._settings.agent_cpu_limit,
                    "memory": self._settings.agent_memory_limit},
        )

    container = k8s.V1Container(
        ...
        resources=resources,
        ...
    )
```

### Monitoring for Profile Tuning

Deploy Prometheus queries to validate profiles after launch:

```promql
# Agents consistently hitting CPU limits (need higher limit)
rate(container_cpu_cfs_throttled_seconds_total{pod=~"df-.*"}[5m]) > 0.1

# Memory usage vs request ratio (if < 0.5, request is too high)
container_memory_working_set_bytes{pod=~"df-.*"}
  / on(pod) kube_pod_container_resource_requests{resource="memory", pod=~"df-.*"}

# Agents OOMKilled (need higher memory limit)
kube_pod_container_status_last_terminated_reason{reason="OOMKilled", pod=~"df-.*"}
```

---

## 4. Two-Tier Architecture for 100+ Agents

### Problem

At 100+ agents, three pressure points emerge:

1. **K8s API rate limiting**: The default kube-apiserver rate limit is 400 qps (GKE). Creating 100 Jobs generates ~300 API calls (create job + watch events + pod scheduling). Combined with the watchdog polling, this approaches the limit.
2. **etcd object accumulation**: Each Job creates 3-5 etcd objects (Job, Pod, Events). 100 agents = 300-500 objects. etcd performance degrades above ~8,000 objects in a single namespace.
3. **Redis fan-out**: With per-agent consumer groups, each message in the swarm stream creates N delivery entries. At 100 agents, a single broadcast message generates 100 pending entries.

### Design: Regional Coordinators

```
Controller
    |
    +-- Coordinator-A (manages agents 1-25)    -- K8s Job, role: coordinator
    |       +-- researcher-1
    |       +-- researcher-2
    |       +-- ... (up to 25)
    |
    +-- Coordinator-B (manages agents 26-50)   -- K8s Job, role: coordinator
    |       +-- researcher-26
    |       +-- ... (up to 25)
    |
    +-- Coordinator-C (manages agents 51-75)
    +-- Coordinator-D (manages agents 76-100)
    +-- Aggregator (reads coordinator summaries)
```

#### How It Works

1. **Controller decomposes** the swarm into coordinator groups (max 25 agents per coordinator, configurable).
2. **Each coordinator is a K8s Job** that receives a sub-task list and spawns its own worker agents using a lightweight spawner (or the controller does it on behalf).
3. **Coordinators aggregate locally** -- each coordinator collects results from its 25 workers and produces a summary.
4. **Top-level aggregator** merges coordinator summaries instead of reading 100 individual results.

#### Communication Topology

```
Level 0 (Controller):  swarm:{group_id}:control         -- directives to coordinators
Level 1 (Coordinators): swarm:{group_id}:coord:{coord_id}:messages  -- coordinator <-> workers
Level 2 (Workers):      Read/write to their coordinator's stream only
```

This limits Redis fan-out to max 25 consumers per stream instead of 100.

#### Integration with Existing Spawner

The spawner does not change. The coordinator is just another agent type:

```python
# In swarm creation, if agent count > COORDINATOR_THRESHOLD:
COORDINATOR_THRESHOLD = 30  # Use two-tier above this

async def create_tiered_swarm(self, agents: list[SwarmAgent], ...) -> SwarmGroup:
    if len(agents) <= COORDINATOR_THRESHOLD:
        return await self.create_swarm(...)  # existing path

    # Partition agents into groups of max 25
    chunks = [agents[i:i+25] for i in range(0, len(agents), 25)]
    coordinators = []

    for i, chunk in enumerate(chunks):
        coord = SwarmAgent(
            id=f"coord-{i}",
            group_id=group.id,
            role="coordinator",
            agent_type="coordinator",
            task_assignment=json.dumps({
                "sub_agents": [a.id for a in chunk],
                "sub_tasks": [a.task_assignment for a in chunk],
            }),
        )
        coordinators.append(coord)

    # Spawn coordinators (they spawn workers via controller API callback)
    # Or: controller spawns all Jobs but organizes streams hierarchically
    ...
```

#### Trade-offs

| Aspect | Flat (current) | Two-Tier |
|--------|---------------|----------|
| Simplicity | Simple | More complex |
| Redis fan-out | N consumers per message | max 25 per stream |
| K8s API pressure | N Jobs at once | N Jobs but staggered (coordinators spawn workers) |
| Latency to first result | Lower (direct) | Higher (extra hop through coordinator) |
| When to use | <= 30 agents | > 30 agents |

**Recommendation**: Implement the flat model first (with asyncio.gather + semaphore + resource profiles). Add two-tier only when production swarms regularly exceed 30 agents. The watchdog and resource profiles solve the immediate scaling bottleneck.

---

## 5. Controller High Availability

### Problem

The controller is currently a single process. If it crashes during swarm operation, the watchdog stops, teardown never happens, and orphaned Jobs burn resources.

### Design: Controller as a K8s Deployment with Leader Election

```yaml
# charts/ditto-factory/templates/controller-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ditto-factory-controller
spec:
  replicas: 2
  selector:
    matchLabels:
      app: ditto-factory-controller
  template:
    spec:
      containers:
        - name: controller
          image: {{ .Values.controller.image }}
          env:
            - name: CONTROLLER_REPLICA_COUNT
              value: "2"
          ports:
            - containerPort: 8080
```

#### Leader Election via K8s Lease Objects

```python
# controller/src/controller/leader.py

from kubernetes import client as k8s
from kubernetes.leaderelection import leaderelection
from kubernetes.leaderelection.resourcelock.configmaplock import ConfigMapLock
import threading

class ControllerLeaderElection:
    """K8s-native leader election using Lease objects."""

    def __init__(self, namespace: str, identity: str):
        self._namespace = namespace
        self._identity = identity  # Pod name from HOSTNAME env var
        self._is_leader = False

    def start(self, on_started_leading: callable, on_stopped_leading: callable):
        """Start leader election in a background thread."""
        # Use Coordination API Lease (preferred over ConfigMap)
        lock = k8s.CoordinationV1Api().read_namespaced_lease  # simplified

        config = leaderelection.Config(
            lock=ConfigMapLock(
                name="ditto-factory-controller-leader",
                namespace=self._namespace,
                identity=self._identity,
            ),
            lease_duration=15,
            renew_deadline=10,
            retry_period=2,
            onstarted_leading=on_started_leading,
            onstopped_leading=on_stopped_leading,
        )

        # Run in background thread
        thread = threading.Thread(
            target=leaderelection.LeaderElection(config).run,
            daemon=True,
        )
        thread.start()
```

#### What the Leader Does vs Replicas

| Responsibility | Leader | Replica |
|---------------|--------|---------|
| API server (handle requests) | Yes | Yes (behind Service) |
| Scheduling watchdog | Yes | No (standby) |
| Swarm completion detection | Yes | No (standby) |
| Job spawning | Yes | No (standby) |
| Redis stream creation | Yes | No (standby) |
| Health check endpoint | Yes | Yes |

Both replicas serve the API (webhook receiver, status queries), but only the leader runs the watchdog and spawns Jobs. On leader failure, the replica acquires the lease within ~15 seconds and resumes.

#### Orphan Detection on Leader Takeover

When a new leader starts, it must reconcile state:

```python
async def on_became_leader(self):
    """Reconcile any orphaned swarms from previous leader."""
    active_groups = await self._state.list_active_swarm_groups()

    for group in active_groups:
        # Check each agent's K8s Job still exists
        for agent in group.agents:
            if agent.status in (AgentStatus.ACTIVE, AgentStatus.PENDING):
                try:
                    job = self._batch_api.read_namespaced_job(
                        name=agent.k8s_job_name,
                        namespace=self._namespace,
                    )
                    if job.status.succeeded:
                        agent.status = AgentStatus.COMPLETED
                    elif job.status.failed:
                        agent.status = AgentStatus.FAILED
                except k8s.ApiException as e:
                    if e.status == 404:
                        # Job was deleted or TTL'd
                        agent.status = AgentStatus.LOST

        # Check if group should be completed
        all_done = all(
            a.status in (AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.LOST)
            for a in group.agents
        )
        if all_done:
            await self.teardown_swarm(group.id)

    logger.info(f"Leader reconciliation complete: {len(active_groups)} groups checked")
```

#### Recommended Rollout Order

1. **Phase 1 (immediate)**: Keep single replica, add the watchdog + resource profiles. These are the highest-impact changes with lowest risk.
2. **Phase 2 (after swarm feature stabilizes)**: Convert to Deployment with 2 replicas + leader election. Requires testing the reconciliation logic thoroughly.
3. **Phase 3 (if needed)**: Two-tier coordinator pattern, only when production evidence shows 30+ agent swarms are common.

---

## 6. Summary of Changes

| File | Change | Priority |
|------|--------|----------|
| `controller/src/controller/jobs/resource_profiles.py` | NEW: Per-role resource profile table | P0 |
| `controller/src/controller/jobs/spawner.py` | Add `agent_role` param, use resource profiles | P0 |
| `controller/src/controller/jobs/async_spawner.py` | NEW: AsyncJobSpawner with semaphore | P0 |
| `controller/src/controller/swarm/watchdog.py` | NEW: SchedulingWatchdog | P0 |
| `controller/src/controller/config.py` | Add watchdog config settings | P0 |
| `src/mcp/swarm_comms/server.js` | Update `swarm_wait_for_peers` to read control stream | P0 |
| `controller/src/controller/swarm/manager.py` | Use AsyncJobSpawner in `create_swarm` | P1 |
| `controller/src/controller/leader.py` | NEW: Leader election | P2 |
| `charts/ditto-factory/templates/controller-deployment.yaml` | Convert to Deployment with 2 replicas | P2 |
| `controller/src/controller/swarm/tiered.py` | NEW: Two-tier coordinator logic | P3 |

---

## 7. Open Questions

1. **Watchdog polling vs K8s Watch**: Polling every 15s is simpler but adds API load. A K8s Watch on Pod events for `label_selector=app=ditto-factory-agent` would be more efficient but requires managing the watch connection lifecycle. Recommend starting with polling, switch to Watch if API rate limiting becomes an issue.

2. **Coordinator agent image**: Should coordinators use the same agent image or a lighter image without Claude Code? A lighter image reduces startup time and resource usage. Recommend a separate `coordinator` image.

3. **Graceful degradation threshold**: When the watchdog reduces peer count, should there be a minimum viable swarm size? For example, if a 10-agent research swarm can only schedule 3, should it proceed or abort? Recommend a configurable `min_viable_ratio` (default 0.5) -- abort if less than half the agents can schedule.
