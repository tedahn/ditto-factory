# Redis Streams Transport Layer Analysis

**Spec:** `docs/superpowers/specs/2026-03-25-agent-communication-protocol-design.md` Section 2
**Date:** 2026-03-25
**Analyst:** Software Architect Agent

---

## 1. Per-Agent Consumer Groups for Broadcast

**Verdict: SOUND (with one concern)**

The fix from shared consumer group to per-agent consumer groups is correct. A single consumer group implements competing-consumers (each message goes to exactly one consumer), which breaks broadcast. Per-agent groups give each agent an independent cursor -- this is the canonical Redis Streams pattern for fan-out.

**Edge case -- CONCERN:** Consumer group creation uses `$` (latest ID). If messages are XADDed to the stream *between* stream creation and consumer group creation, those messages are lost for that agent. The spec creates streams with MKSTREAM on the first XGROUP CREATE, but if agent-1's group is created, then a message arrives, then agent-2's group is created, agent-2 misses it.

**RECOMMEND:** Create ALL consumer groups in a single Redis pipeline before any agent is spawned. The controller already does this sequentially in `create_swarm`, but the code should use `MULTI/EXEC` or pipeline to make group creation atomic relative to message production. Alternatively, use `0` instead of `$` as the start ID (since no messages exist yet at group creation time, `0` and `$` are equivalent if done before any XADD -- but `0` is safer if timing is off).

---

## 2. Memory Pressure (N agents x M messages)

**Verdict: CONCERN**

With per-agent consumer groups, Redis stores:
- **Stream entries:** O(M) -- one copy regardless of consumer group count. The stream data itself is shared.
- **Consumer group metadata:** O(N) groups, each tracking a last-delivered-ID cursor. This is ~200 bytes per group -- negligible.
- **Pending Entries List (PEL):** O(N x M) in the worst case. Each consumer group maintains a PEL of messages delivered but not ACKed. At 100 agents x 10K messages, that is 1M PEL entries at ~150 bytes each = ~150MB in PEL metadata alone.

**The PEL is the real cost**, not the stream data.

**RECOMMEND:**
1. `swarm_read` already auto-ACKs. Good -- this keeps PEL small (only in-flight messages, typically COUNT=10 per agent = 1K entries at 100 agents). Confirm XACK is called immediately after processing in the MCP server.
2. Add XTRIM with MAXLEN as a secondary defense. EXPIREAT only removes the key when it expires -- it does nothing to bound stream length during the swarm's lifetime. With 10 agents sending status updates every few seconds, a 2-hour swarm could accumulate 100K+ messages. Use `XADD ... MAXLEN ~ 50000` (approximate trimming) to cap growth while preserving the audit tail.
3. The `swarm_max_agents_per_group: 10` default is wise. At 10 agents, PEL overhead is manageable. If this grows to 100, revisit.

---

## 3. Pub/Sub Notification Sideband

**Verdict: SOUND (well-known pattern, one race condition to handle)**

This is the **"Pub/Sub as wake-up, Stream as source of truth"** pattern -- well-documented in Redis community literature and used in systems like BullMQ (notification channel + reliable data store). It correctly separates the notification path (lossy but fast) from the data path (durable and ordered).

**Race condition -- CONCERN:** There is a classic missed-notification race:

1. Agent A sends a response and publishes to Pub/Sub
2. Agent B has not yet subscribed to Pub/Sub (it is between creating the subscription and calling `swarm_request`)
3. The Pub/Sub notification is lost (fire-and-forget)
4. Agent B blocks forever waiting for a notification that already fired

**RECOMMEND:**
- After subscribing to the Pub/Sub channel, **immediately do one XREADGROUP poll** (non-blocking, COUNT 100) to catch responses that arrived before the subscription was active. Only then enter the blocking wait loop.
- Add a periodic fallback poll (every 5-10 seconds) even while subscribed, as a safety net against missed notifications. The spec's `timeout_seconds` provides an outer bound, but a stale 60-second wait is bad UX.

---

## 4. Stream Cleanup (EXPIREAT vs XTRIM)

**Verdict: CONCERN**

EXPIREAT is necessary but not sufficient:

| Mechanism | What it does | Gap |
|-----------|-------------|-----|
| EXPIREAT | Deletes the entire key after TTL | Does nothing during the swarm lifetime. A 2-hour swarm with chatty agents can grow unbounded. |
| Explicit delete on teardown | Belt-and-suspenders | Good, but if controller crashes before teardown, orphaned streams persist until EXPIREAT. |
| XTRIM MAXLEN | Caps entries during lifetime | Not in the spec. |
| XTRIM MINID | Removes entries older than a timestamp-based ID | Not in the spec. More useful for long-lived streams. |

**RECOMMEND:**
1. Use `XADD swarm:{group_id}:messages MAXLEN ~ 50000 * field value` -- the approximate (`~`) flag lets Redis trim efficiently in batches rather than exactly, which is O(1) amortized.
2. Do NOT use MINID for ephemeral swarms -- MAXLEN is simpler and the swarm lifetime is bounded.
3. Keep EXPIREAT as the safety net for orphaned streams.
4. Consider setting a `maxmemory-policy` of `noeviction` on the Redis instance and monitoring `used_memory` -- if swarm streams cause memory pressure, you want an OOM error rather than silent eviction of task/result keys from the existing `RedisState` class.

---

## 5. Failure Modes: Redis Restart Mid-Swarm

**Verdict: CONCERN**

The spec addresses agent crash recovery (respawn with same AGENT_ID, resume from consumer group cursor) but does not address Redis failure.

**If Redis restarts with AOF/RDB persistence:**
- Stream data survives. Consumer group state survives. Agents reconnect and resume. **SOUND.**

**If Redis restarts WITHOUT persistence (or data is lost):**
- All streams, consumer groups, PELs, and the agent registry hash are gone.
- Agents will get errors on XREADGROUP (group does not exist). The MCP server needs error handling for this.
- The existing `RedisState` (task/result keys) also loses data -- this is a pre-existing risk, not new.

**If an agent reconnects after Redis restart:**
- Consumer group is gone. XREADGROUP returns NOGROUP error.
- The MCP server must catch this and either: (a) recreate the consumer group at `0` to replay all surviving messages, or (b) signal the controller that the swarm is unrecoverable.

**If the controller crashes mid-swarm:**
- EXPIREAT ensures streams are cleaned up eventually.
- But no completion detection runs. Agents may run to their K8s job timeout, wasting compute.
- The controller should persist swarm state to Postgres (already in the spec via `swarm_groups` table) and implement recovery on startup: scan for `ACTIVE` swarms and resume monitoring.

**RECOMMEND:**
1. The MCP server (`df-swarm-comms`) MUST handle `NOGROUP` errors from XREADGROUP gracefully -- either recreate the group or terminate the agent with a clear error.
2. Ensure Redis persistence (AOF with `appendfsync everysec` minimum) is a documented prerequisite in deployment docs.
3. Add controller startup recovery: query `swarm_groups WHERE status = 'active'` and resume monitoring.
4. The existing `redis_state.py` uses plain `redis.asyncio.Redis` with no connection pooling or retry logic. The swarm extensions should use `redis.asyncio.ConnectionPool` with `retry_on_timeout=True` and exponential backoff. This is a good time to upgrade the base class.

---

## Summary Table

| Area | Verdict | Key Action |
|------|---------|------------|
| Per-agent consumer groups | SOUND | Use pipeline for atomic group creation; consider start ID `0` |
| Memory pressure | CONCERN | Add `MAXLEN ~` to XADD; PEL is the real cost, XACK promptly |
| Pub/Sub sideband | SOUND | Poll once after subscribe; add periodic fallback poll |
| Stream cleanup | CONCERN | Add XTRIM MAXLEN during lifetime; EXPIREAT alone is insufficient |
| Redis failure modes | CONCERN | Handle NOGROUP errors; require AOF persistence; add controller recovery |
