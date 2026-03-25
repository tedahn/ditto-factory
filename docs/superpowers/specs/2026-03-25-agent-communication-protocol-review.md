# Architecture Review: Agent Communication Protocol Design

**Reviewer:** Software Architect Agent
**Date:** 2026-03-25
**Verdict:** Issues Found (3 blocking, 5 non-blocking suggestions)

---

## Issues Found

### ISSUE-1 (Blocking): Consumer Group Semantics Conflict with Broadcast

The spec uses `XREADGROUP` with consumer groups for the messages stream. In Redis consumer groups, each message is delivered to exactly ONE consumer in the group. This means broadcast messages (`recipient_id = null`) will only be delivered to a single agent, not all agents.

**The spec says:** "All consumers in the group read it via XREADGROUP"
**What actually happens:** Redis delivers each stream entry to exactly one consumer in the group.

**Fix options:**
- (A) Use `XREAD` (without consumer groups) for the messages stream, with each agent tracking its own last-seen ID. You lose automatic cursor management but gain true broadcast. Use consumer groups only for the control stream where per-agent delivery matters.
- (B) Create per-agent consumer groups (one group per agent) so each agent gets every message. This is the simplest fix but creates N consumer groups per stream.
- (C) Keep consumer groups for directed messages only; use a separate fan-out mechanism (Pub/Sub notification + XRANGE read) for broadcasts.

**Recommendation:** Option B. One consumer group per agent (`agent-{agent_id}`) on the messages stream. Each agent is the sole consumer in its own group, so every agent sees every message. Client-side filtering for `recipient_id` still works as designed.

### ISSUE-2 (Blocking): `swarm_request` Polling Under Load Creates Hot Loop

`swarm_request` polls `swarm_read` in a loop waiting for a correlated response. At 100 agents with concurrent request/response pairs, this creates significant Redis load (10+ XREADGROUP calls/second per waiting agent). The timeout is 60s by default.

**Fix:** Use Redis Pub/Sub as a notification sideband. When a response is published to the stream, also `PUBLISH` to `swarm:{group_id}:notify:{correlation_id}`. The requesting agent `SUBSCRIBE`s to that channel. This converts polling to event-driven wake-up. Fall back to polling only if the notification is missed (belt and suspenders).

### ISSUE-3 (Blocking): Sequential Agent Spawn Creates Thundering Herd

The creation loop spawns agents sequentially and marks them all ACTIVE immediately. With 10+ agents, this means:
- The first agent starts reading an empty registry while later agents are still spawning
- All agents hit the Redis registry simultaneously on startup
- No staggered initialization

**Fix:** Add a `PENDING -> ACTIVE` transition that each agent triggers for itself after registering in the discovery hash. The controller should spawn in batches (e.g., 5 at a time) and wait for registration signals before spawning the next batch. Alternatively, have agents wait for a `{"action": "start"}` control message before beginning work.

---

## Non-Blocking Suggestions

### S1: Missing `swarm_report` Tool

Agents need a way to submit their final result to the controller. Currently, result submission is handled via the `result:{thread_id}` Redis key pattern. Swarm agents need an equivalent: `swarm_report` that writes to the agent's registry entry AND posts a `data` message with `{"type": "final_result"}`. Without this, completion detection for `all_complete` strategy has no structured result to collect.

### S2: No Dead Letter / Poison Message Handling

If a message payload is malformed or causes an agent to crash repeatedly, there is no mechanism to skip it. Add a max-delivery-count check: if a message has been read but not ACK'd N times (tracked via `XPENDING`), move it to a dead-letter stream `swarm:{group_id}:dead` and ACK it.

### S3: `redis_state.py` Stream Methods Should Be Consolidated

The existing `RedisState` already has `append_stream_event` and `read_stream` for per-agent tracing. The new swarm streams should extend this class (or a shared base) rather than living only in `SwarmManager`. This keeps all Redis operations in one place and avoids two separate Redis client lifecycle paths.

### S4: Message Ordering Guarantee Across Streams

The spec uses two streams (messages + control). An agent reading both has no guarantee of relative ordering between them. If the controller sends `{"action": "shutdown"}` on control while data is still flowing on messages, the agent might process more data messages after receiving shutdown. Document the expected behavior: control messages take priority, and `swarm_read` should check the control stream first on each call.

### S5: Add `swarm_wait_for_peers` Tool

Agents starting up need to know when their peers are ready. Currently they would poll `swarm_peers` in a loop. Add a `swarm_wait_for_peers(min_count, timeout_seconds)` tool that blocks until the registry shows `min_count` active agents or times out. This pairs well with the ISSUE-3 fix.

---

## Scalability Assessment

| Scale | Assessment |
|-------|-----------|
| 10 agents | Sound. Redis handles this trivially. Fix ISSUE-1 and it works. |
| 100 agents | Needs ISSUE-2 fix. 100 agents polling creates ~1000 XREADGROUP/s. Per-agent consumer groups (ISSUE-1 fix B) means 100 consumer groups per stream, which Redis handles but monitor memory. |
| 1000 agents | Architectural concern. Single Redis instance becomes bottleneck. Need Redis Cluster with hash-tag routing (`{group_id}` in key names already enables this). Also need to shard swarms across multiple controller instances. The `swarm_max_agents_per_group` default of 10 is wise -- 1000 agents should be 100 groups of 10, not 1 group of 1000. Document this as a hard constraint. |

---

## Integration Assessment

- **JobSpawner:** Clean integration. `extra_env` parameter already exists and handles `SWARM_GROUP_ID`, `AGENT_ID`, `AGENT_ROLE` injection. No changes needed to spawner itself.
- **Orchestrator:** The `create_swarm` method should live in a new `SwarmManager` (as proposed), not in the Orchestrator directly. The Orchestrator calls `SwarmManager.create_swarm()` similar to how it calls `self._spawner.spawn()`. This is correctly designed in the file structure table.
- **MCP server pattern:** The `df-swarm-comms` server follows the exact same pattern as `df-message-queue` (env vars at startup, Redis client, stdio transport). Clean.
- **State backend:** The new protocol methods extend cleanly. The SQL schema is sound. Consider adding `ON DELETE CASCADE` to the `swarm_agents.group_id` foreign key.
- **Models:** `SwarmStatus` and `AgentStatus` enums integrate well with existing enum patterns in `models.py`.

---

## Open Questions Assessment

1. **Should `swarm_request` block?** Yes, the blocking/polling approach is correct for agent ergonomics. But fix the polling issue per ISSUE-2.
2. **Message size for data type.** The 64KB limit with natural batching is correct. Add guidance in the tool description: "If your data exceeds 64KB, send multiple messages with a shared `batch_id` in the payload."
3. **Agent respawn on crash.** Yes, with same AGENT_ID. But note: with ISSUE-1 fix (option B), the respawned agent's consumer group cursor is preserved, so it resumes correctly. Document that the respawned agent MUST NOT create a new consumer group -- it reuses the existing one.
