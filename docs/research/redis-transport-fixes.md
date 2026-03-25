# Redis Transport Layer — Concrete Fixes for Four Concerns

**Date:** 2026-03-25
**Status:** Proposal
**Applies to:** [Agent Communication Protocol Design](../superpowers/specs/2026-03-25-agent-communication-protocol-design.md)
**Existing code:** `controller/src/controller/state/redis_state.py`

---

## Concern 1: PEL Memory + Unbounded Stream Growth

### Problem

100 agents x 10K messages = 1M PEL entries across all consumer groups (~150MB). Even with auto-XACK on `swarm_read`, PEL entries accumulate if agents are slow readers. And `EXPIREAT` only fires after TTL — the stream grows without bound during the swarm's lifetime.

### Fix: XADD with MAXLEN ~ (approximate trimming)

Use approximate trimming on every XADD. The `~` flag lets Redis trim efficiently (it trims whole radix tree nodes, not exact counts), avoiding per-write overhead.

```python
# In SwarmManager._send_message() or MCP server's swarm_send handler:
#
# MAXLEN ~ 10000 means: keep approximately the last 10,000 entries.
# The ~ makes this O(1) amortized instead of O(N).
await redis.xadd(
    f"swarm:{group_id}:messages",
    fields={"data": serialized_message},
    maxlen=10000,
    approximate=True,
)
```

**Why MAXLEN ~ and not MINID?** MINID is useful when you want time-based trimming (e.g., "keep last 2 hours"), but swarm lifetimes vary. MAXLEN ~ is simpler: it caps memory regardless of swarm duration. For a 10-agent swarm, 10K messages is generous. For 100-agent swarms, scale to `MAXLEN ~ 50000`.

**Configuration:**

```python
# config.py
swarm_stream_maxlen: int = 10000  # Per-stream cap, approximate
```

**Tradeoff:** Old messages get trimmed. If the audit trail needs the full history, the controller must `XRANGE` periodically and persist to PostgreSQL/S3 before entries are evicted. Add a background task:

```python
# swarm/monitor.py — run every 60s per active swarm
async def checkpoint_stream(self, group_id: str):
    """Persist stream entries to durable storage before MAXLEN trims them."""
    last_checkpointed_id = await self._state.get_checkpoint_id(group_id)
    entries = await self._redis.xrange(
        f"swarm:{group_id}:messages",
        min=last_checkpointed_id or "-",
        count=1000,
    )
    if entries:
        await self._state.append_audit_log(group_id, entries)
        await self._state.set_checkpoint_id(group_id, entries[-1][0])
```

### Fix: Aggressive XACK

The MCP server must XACK immediately after delivering messages to the agent, not after the agent processes them. This is safe because the stream itself is the durable store — the consumer group cursor (not the PEL) tracks position.

```python
# In swarm_read handler:
messages = await redis.xreadgroup(
    groupname=f"agent-{agent_id}",
    consumername=agent_id,
    streams={f"swarm:{group_id}:messages": ">"},
    count=10,
    block=1000,
)
# ACK immediately — the stream retains the data regardless
if messages:
    msg_ids = [msg_id for _, entries in messages for msg_id, _ in entries]
    await redis.xack(f"swarm:{group_id}:messages", f"agent-{agent_id}", *msg_ids)
```

### Fix: PEL Garbage Collection

Run periodic XCLAIM + XACK for any PEL entries older than 5 minutes (agent probably crashed):

```python
async def gc_stale_pel(self, group_id: str, agent_id: str):
    """Claim and ACK stale PEL entries from crashed/slow agents."""
    stale = await self._redis.xautoclaim(
        name=f"swarm:{group_id}:messages",
        groupname=f"agent-{agent_id}",
        consumername="gc-worker",
        min_idle_time=300000,  # 5 minutes in ms
        start_id="0-0",
        count=100,
    )
    # stale returns (next_start_id, claimed_messages, deleted_ids)
    if stale[1]:
        claimed_ids = [msg_id for msg_id, _ in stale[1]]
        await self._redis.xack(
            f"swarm:{group_id}:messages",
            f"agent-{agent_id}",
            *claimed_ids,
        )
```

---

## Concern 2: Pub/Sub Sideband Race Condition

### Problem

In `swarm_request`, the sequence is:
1. Agent sends a `request` message via XADD
2. Agent subscribes to `swarm:{group_id}:notify`
3. Responder sends `response` via XADD + PUBLISH

If step 3 happens before step 2 completes, the PUBLISH notification is lost (Pub/Sub is fire-and-forget). The requesting agent then blocks until timeout.

### Fix Option A: Subscribe-Before-Send (Recommended)

Reverse the order — subscribe to the notification channel BEFORE sending the request:

```javascript
// MCP server: swarm_request implementation (Node.js)
async function swarmRequest(recipientId, payload, timeoutSeconds = 60) {
    const correlationId = crypto.randomUUID();
    const channel = `swarm:${groupId}:notify`;

    // 1. Subscribe FIRST — before the request is even sent
    const subscriber = redis.duplicate();
    await subscriber.connect();

    const responsePromise = new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
            subscriber.unsubscribe(channel);
            subscriber.quit();
            reject(new Error(`swarm_request timed out after ${timeoutSeconds}s`));
        }, timeoutSeconds * 1000);

        subscriber.subscribe(channel, async (notification) => {
            // Check stream for correlated response
            const response = await checkForCorrelatedResponse(correlationId);
            if (response) {
                clearTimeout(timer);
                await subscriber.unsubscribe(channel);
                await subscriber.quit();
                resolve(response);
            }
        });
    });

    // 2. NOW send the request — subscription is guaranteed active
    await xaddMessage({
        recipientId,
        messageType: "request",
        correlationId,
        payload,
    });

    // 3. But ALSO check the stream immediately — response might already exist
    //    (handles case where responder was faster than our event loop)
    const existing = await checkForCorrelatedResponse(correlationId);
    if (existing) {
        await subscriber.unsubscribe(channel);
        await subscriber.quit();
        return existing;
    }

    return responsePromise;
}
```

**Key detail:** Step 3 is critical. Even with subscribe-before-send, there is a window between `subscriber.subscribe()` returning and the internal listener being fully active. The immediate stream check after sending closes this gap.

### Fix Option B: Lua Atomic XADD + PUBLISH (for the responder side)

Ensure the responder's XADD and PUBLISH are atomic so the message is guaranteed to be in the stream when the notification fires:

```lua
-- scripts/xadd_and_notify.lua
-- KEYS[1] = stream key, KEYS[2] = notify channel
-- ARGV[1] = message field key, ARGV[2] = message data, ARGV[3] = notification payload
local id = redis.call('XADD', KEYS[1], '*', ARGV[1], ARGV[2])
redis.call('PUBLISH', KEYS[2], ARGV[3])
return id
```

```python
# Python usage:
XADD_NOTIFY_SCRIPT = """
local id = redis.call('XADD', KEYS[1], '*', ARGV[1], ARGV[2])
redis.call('PUBLISH', KEYS[2], ARGV[3])
return id
"""

# Register once at startup
xadd_notify = redis.register_script(XADD_NOTIFY_SCRIPT)

# Use in swarm_send when sending responses:
await xadd_notify(
    keys=[
        f"swarm:{group_id}:messages",
        f"swarm:{group_id}:notify",
    ],
    args=[
        "data",
        serialized_message,
        json.dumps({"correlation_id": correlation_id, "sender_id": agent_id}),
    ],
)
```

### Recommendation: Use BOTH A and B together

Option A (subscribe-before-send + immediate check) eliminates the race on the subscriber side.
Option B (Lua atomic XADD+PUBLISH) eliminates the race on the publisher side where a subscriber could get the notification but the XADD hasn't committed yet.

Together, there is no window for message loss.

### Alternative Considered: Redis Keyspace Notifications

You could use `CONFIG SET notify-keyspace-events Kx` and subscribe to `__keyevent@0__:xadd` events. However:
- Keyspace notifications are also Pub/Sub-based (same fire-and-forget problem)
- They are per-key, not per-correlation-id — noisier
- They require Redis server config changes (not always possible in managed Redis)
- **Verdict: Not recommended.** The Lua script approach is cleaner.

---

## Concern 3: NOGROUP Error Handling + Redis Restart Recovery

### Problem

If Redis restarts (or a failover occurs in Redis Sentinel/Cluster), all streams and consumer groups are lost (unless persistence is configured with AOF). The current `redis_state.py` has:
- No connection pooling
- No retry logic
- No error handling for `NOGROUP`, `BUSYGROUP`, or connection errors
- No way to reconstruct swarm state

### Fix: Resilient Redis Client Wrapper

```python
# controller/src/controller/state/redis_state.py — enhanced

from __future__ import annotations
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from redis.asyncio import Redis, ConnectionPool
from redis.exceptions import (
    ConnectionError,
    ResponseError,
    TimeoutError,
    BusyLoadingError,
)

logger = logging.getLogger(__name__)

TASK_TTL = 3600
RESULT_TTL = 3600

# Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 0.5  # seconds


def _is_nogroup_error(exc: ResponseError) -> bool:
    """Check if a Redis ResponseError is a NOGROUP error."""
    return "NOGROUP" in str(exc)


def _is_busygroup_error(exc: ResponseError) -> bool:
    """Check if error means consumer group already exists."""
    return "BUSYGROUP" in str(exc)


def with_retry(func):
    """Decorator that retries Redis operations with exponential backoff."""
    async def wrapper(self, *args, **kwargs):
        last_exc = None
        for attempt in range(MAX_RETRIES):
            try:
                return await func(self, *args, **kwargs)
            except (ConnectionError, TimeoutError, BusyLoadingError) as exc:
                last_exc = exc
                wait = RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.warning(
                    "Redis connection error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, MAX_RETRIES, wait, exc,
                )
                await asyncio.sleep(wait)
            except ResponseError as exc:
                if _is_nogroup_error(exc):
                    # Consumer group was lost — recreate it
                    logger.warning("NOGROUP error, attempting group recreation: %s", exc)
                    await self._recreate_consumer_group_from_context(args, kwargs)
                    # Retry the original operation once
                    return await func(self, *args, **kwargs)
                raise
        raise last_exc
    return wrapper


class RedisState:
    def __init__(self, redis_url: str, max_connections: int = 20):
        self._pool = ConnectionPool.from_url(
            redis_url,
            max_connections=max_connections,
            decode_responses=False,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        self._redis = Redis(connection_pool=self._pool)

    async def close(self):
        await self._redis.close()
        await self._pool.disconnect()

    async def _recreate_consumer_group_from_context(self, args, kwargs):
        """
        Attempt to recreate a consumer group after NOGROUP error.
        This is a best-effort recovery — the group starts at '0' to
        replay any existing messages in the stream.
        """
        # This will be overridden by SwarmRedisState which has group context
        logger.error("NOGROUP recovery not available in base RedisState")

    # --- Existing methods, now with retry ---

    @with_retry
    async def push_task(self, thread_id: str, task_context: dict) -> None:
        await self._redis.set(
            f"task:{thread_id}", json.dumps(task_context), ex=TASK_TTL
        )

    @with_retry
    async def get_task(self, thread_id: str) -> dict | None:
        raw = await self._redis.get(f"task:{thread_id}")
        return json.loads(raw) if raw else None

    @with_retry
    async def push_result(self, thread_id: str, result: dict) -> None:
        await self._redis.set(
            f"result:{thread_id}", json.dumps(result), ex=RESULT_TTL
        )

    @with_retry
    async def get_result(self, thread_id: str) -> dict | None:
        raw = await self._redis.get(f"result:{thread_id}")
        return json.loads(raw) if raw else None

    @with_retry
    async def queue_message(self, thread_id: str, message: str) -> None:
        await self._redis.rpush(f"queue:{thread_id}", message)

    @with_retry
    async def drain_messages(self, thread_id: str) -> list[str]:
        key = f"queue:{thread_id}"
        pipe = self._redis.pipeline()
        pipe.lrange(key, 0, -1)
        pipe.delete(key)
        results = await pipe.execute()
        return [m.decode() if isinstance(m, bytes) else m for m in results[0]]

    @with_retry
    async def append_stream_event(self, thread_id: str, event: str) -> None:
        await self._redis.xadd(f"agent:{thread_id}", {"event": event})

    @with_retry
    async def read_stream(
        self, thread_id: str, last_id: str = "0"
    ) -> list[tuple[str, dict]]:
        entries = await self._redis.xrange(f"agent:{thread_id}", min=last_id)
        return [
            (
                eid.decode() if isinstance(eid, bytes) else eid,
                {
                    k.decode() if isinstance(k, bytes) else k:
                    v.decode() if isinstance(v, bytes) else v
                    for k, v in data.items()
                },
            )
            for eid, data in entries
        ]
```

### Fix: Swarm-Aware NOGROUP Recovery

```python
# controller/src/controller/swarm/redis_streams.py

class SwarmRedisStreams:
    """Manages Redis Streams for swarm communication with NOGROUP recovery."""

    def __init__(self, redis: Redis):
        self._redis = redis

    async def ensure_consumer_group(
        self, stream_key: str, group_name: str, start_id: str = "0"
    ):
        """Create consumer group, handling both new and existing cases."""
        try:
            await self._redis.xgroup_create(
                stream_key, group_name, id=start_id, mkstream=True
            )
        except ResponseError as exc:
            if _is_busygroup_error(exc):
                pass  # Already exists — fine
            else:
                raise

    async def safe_xreadgroup(
        self,
        group_name: str,
        consumer_name: str,
        stream_key: str,
        count: int = 10,
        block: int = 1000,
    ):
        """XREADGROUP with automatic NOGROUP recovery."""
        try:
            return await self._redis.xreadgroup(
                groupname=group_name,
                consumername=consumer_name,
                streams={stream_key: ">"},
                count=count,
                block=block,
            )
        except ResponseError as exc:
            if _is_nogroup_error(exc):
                logger.warning(
                    "Consumer group '%s' missing on stream '%s', recreating from '0'",
                    group_name, stream_key,
                )
                # Recreate from '0' so we replay all existing messages
                await self.ensure_consumer_group(stream_key, group_name, start_id="0")
                # Retry — this time reading from '0' means we get everything
                return await self._redis.xreadgroup(
                    groupname=group_name,
                    consumername=consumer_name,
                    streams={stream_key: ">"},
                    count=count,
                    block=block,
                )
            raise
```

### Fix: Controller Checkpoint for Redis Recovery

The controller must be able to reconstruct Redis state from its durable store (PostgreSQL/SQLite) after a Redis restart:

```python
# controller/src/controller/swarm/manager.py

async def recover_redis_state(self):
    """
    Called on controller startup. Reconstructs Redis streams and
    consumer groups for any active swarms from the durable state backend.
    """
    active_groups = await self._state.list_swarm_groups(
        status_in=[SwarmStatus.ACTIVE, SwarmStatus.PENDING]
    )
    for group in active_groups:
        logger.info("Recovering Redis state for swarm %s", group.id)

        # 1. Recreate streams (MKSTREAM flag handles idempotency)
        msg_stream = f"swarm:{group.id}:messages"
        ctl_stream = f"swarm:{group.id}:control"

        # 2. Recreate consumer groups for each known agent
        agents = await self._state.list_swarm_agents(group.id)
        for agent in agents:
            group_name = f"agent-{agent.id}"
            # Start from '0' to replay — agents track their own cursor
            await self._streams.ensure_consumer_group(msg_stream, group_name, "0")
            await self._streams.ensure_consumer_group(ctl_stream, group_name, "0")

        # 3. Restore agent registry hash
        registry_key = f"swarm:{group.id}:agents"
        for agent in agents:
            await self._redis.hset(
                registry_key,
                agent.id,
                json.dumps({
                    "role": agent.role,
                    "status": agent.status.value,
                    "task_assignment": agent.task_assignment,
                }),
            )

        # 4. Re-apply TTL
        ttl_seconds = group.config.get("timeout_seconds", 7200) + 3600
        expiry = int(group.created_at.timestamp()) + ttl_seconds
        for key in [msg_stream, ctl_stream, registry_key]:
            await self._redis.expireat(key, expiry)

    logger.info("Recovered Redis state for %d active swarms", len(active_groups))
```

**What data must be checkpointed:**

| Data | Stored In | Recoverable? |
|------|-----------|-------------|
| Swarm group metadata | PostgreSQL `swarm_groups` | Yes |
| Agent roster | PostgreSQL `swarm_agents` | Yes |
| Stream messages | Lost if Redis has no AOF | Partially (from audit log checkpoint) |
| Consumer group cursors | Lost | Agents restart from '0' and skip already-processed messages |
| Agent registry hash | Reconstructed from PostgreSQL | Yes |

**Recommendation:** Enable Redis AOF persistence (`appendonly yes`, `appendfsync everysec`) for production. This makes Redis restart recovery nearly lossless at the cost of ~2% write latency.

---

## Concern 4: EXPIREAT Doesn't Bound Growth During Swarm Lifetime

### Problem

`EXPIREAT` only deletes the entire key after the TTL. A 2-hour swarm with 100 chatty agents can accumulate 500K+ messages before the TTL fires.

### Fix: Layered Trimming Strategy

Three complementary mechanisms:

#### Layer 1: XADD MAXLEN ~ (per-write cap)

Already covered in Concern 1. Every XADD uses `MAXLEN ~ 10000`. This is the primary growth bound.

#### Layer 2: Periodic XTRIM by Controller Monitor

The controller's swarm monitor runs a trim pass every 60 seconds for each active swarm:

```python
# swarm/monitor.py

async def trim_streams(self, group_id: str):
    """
    Trim streams that have grown beyond the target size.
    Uses MAXLEN ~ for efficiency.
    """
    streams = [
        f"swarm:{group_id}:messages",
        f"swarm:{group_id}:control",
    ]
    maxlen = self._config.swarm_stream_maxlen  # default 10000

    for stream in streams:
        stream_len = await self._redis.xlen(stream)
        if stream_len > maxlen * 1.5:  # Only trim if 50% over target
            await self._redis.xtrim(stream, maxlen=maxlen, approximate=True)
            logger.info(
                "Trimmed stream %s from %d to ~%d entries",
                stream, stream_len, maxlen,
            )
```

#### Layer 3: MINID-Based Trim for Time-Bounded Retention

For swarms that need time-based retention (e.g., "keep last 30 minutes"):

```python
async def trim_by_age(self, group_id: str, max_age_seconds: int = 1800):
    """Trim messages older than max_age_seconds using MINID."""
    # Redis stream IDs are millisecond timestamps
    min_id = str(int((time.time() - max_age_seconds) * 1000)) + "-0"
    await self._redis.xtrim(
        f"swarm:{group_id}:messages",
        minid=min_id,
        approximate=True,
    )
```

**Which layer to use when:**

| Scenario | Primary Layer | Why |
|----------|--------------|-----|
| Normal swarm (10 agents, <2hr) | Layer 1 only | MAXLEN ~ on XADD is sufficient |
| Large swarm (50+ agents) | Layers 1 + 2 | Periodic XTRIM catches bursts between XADDs |
| Long-running swarm (>4hr) | Layers 1 + 2 + 3 | MINID-based trim prevents stale message accumulation |

#### Memory Budget Calculation

With `MAXLEN ~ 10000` and average message size of 1KB:
- Messages stream: ~10MB per swarm
- Control stream: ~1MB per swarm (much fewer messages)
- Agent registry hash: negligible
- PEL (with aggressive XACK): negligible
- **Total per swarm: ~11MB**
- **100 concurrent swarms: ~1.1GB** — well within a typical Redis instance

---

## Bonus: Should We Consider Redis Cluster?

### For "Scale to the World" Use Case

**Short answer: Not yet, but design for it now.**

Redis Cluster shards data by key hash slots. Since all swarm keys for a group share the prefix `swarm:{group_id}:`, they would land in different hash slots — meaning multi-key operations (pipelines, Lua scripts across streams + hashes) would fail.

### Fix: Use Hash Tags for Cluster Compatibility

Redis Cluster hashes only the portion of the key inside `{...}`. Redesign keys:

```
# Current (Cluster-incompatible):
swarm:{group_id}:messages
swarm:{group_id}:control
swarm:{group_id}:agents
swarm:{group_id}:notify

# Cluster-compatible (all route to same slot via {group_id}):
swarm:{group_id}:messages     ← Already works! The {group_id} is the hash tag
swarm:{group_id}:control      ← Same slot
swarm:{group_id}:agents       ← Same slot
swarm:{group_id}:notify       ← Same slot (Pub/Sub in Cluster is broadcast anyway)
```

**Wait — the current design already uses `{group_id}` as a natural hash tag.** Redis Cluster extracts the first `{...}` substring. Since all keys for a swarm contain `{group_id}` in the same position, they already land in the same hash slot. The Lua script from Concern 2 will work.

### When to Move to Cluster

| Metric | Single Instance | Cluster Needed |
|--------|----------------|----------------|
| Memory | < 25GB | > 25GB |
| Concurrent swarms | < 500 | > 500 |
| Write throughput | < 100K ops/sec | > 100K ops/sec |
| Availability requirement | 99.9% | 99.99% |

**Action for now:** Validate that all key patterns use `swarm:{group_id}:*` consistently (they do). Add a comment in the code documenting the hash tag strategy. This makes the Cluster migration a config change rather than a code change.

### Redis Sentinel vs Cluster

For the near term, **Redis Sentinel** (automatic failover with a single primary + replicas) is simpler and sufficient:

```python
# Connection configuration for Sentinel
from redis.asyncio.sentinel import Sentinel

sentinel = Sentinel(
    [("sentinel-1", 26379), ("sentinel-2", 26379), ("sentinel-3", 26379)],
    socket_timeout=5.0,
)
redis = sentinel.master_for("ditto-primary", socket_timeout=5.0)
```

---

## Summary of Changes

| Concern | Fix | Redis Commands | Tradeoff |
|---------|-----|---------------|----------|
| 1. PEL/Stream growth | MAXLEN ~, aggressive XACK, XAUTOCLAIM GC | `XADD ... MAXLEN ~ 10000`, `XACK`, `XAUTOCLAIM` | Old messages trimmed — checkpoint to PG first |
| 2. Pub/Sub race | Subscribe-before-send + Lua atomic XADD+PUBLISH | Lua: `XADD` + `PUBLISH` | Extra subscriber connection per request |
| 3. NOGROUP recovery | Retry wrapper, NOGROUP catch + recreate, controller checkpoint | `XGROUP CREATE ... MKSTREAM`, reconstruct from PG | Agents replay from '0' after recovery — must be idempotent |
| 4. Growth during lifetime | MAXLEN ~ on XADD, periodic XTRIM, optional MINID | `XTRIM ... MAXLEN ~`, `XTRIM ... MINID ~` | Audit trail needs periodic PG checkpoint |

### New Config Settings

```python
# Add to config.py
swarm_stream_maxlen: int = 10000           # Approximate max entries per stream
swarm_pel_gc_interval_seconds: int = 60    # How often to XAUTOCLAIM stale PEL entries
swarm_stream_checkpoint_interval: int = 60 # How often to persist stream to PG
swarm_redis_max_connections: int = 20      # Connection pool size
swarm_redis_socket_timeout: float = 5.0    # Per-operation timeout
```

### Files to Modify

| File | Change |
|------|--------|
| `controller/src/controller/state/redis_state.py` | Add connection pooling, retry decorator, NOGROUP handling |
| `controller/src/controller/swarm/redis_streams.py` (new) | SwarmRedisStreams class with safe_xreadgroup, ensure_consumer_group |
| `controller/src/controller/swarm/manager.py` (new) | Add recover_redis_state(), checkpoint_stream() |
| `controller/src/controller/swarm/monitor.py` (new) | Add trim_streams(), gc_stale_pel() |
| `controller/src/controller/config.py` | Add new swarm Redis config settings |
| `src/mcp/swarm_comms/server.js` | Subscribe-before-send in swarm_request, Lua script for XADD+PUBLISH |
| `src/mcp/swarm_comms/scripts/xadd_and_notify.lua` (new) | Atomic XADD + PUBLISH script |
