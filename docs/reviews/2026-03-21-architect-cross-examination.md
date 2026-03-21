# Technical Architecture Review: Traceability Approaches for Ditto Factory

**Date:** 2026-03-21
**Author:** Software Architect (cross-examination)
**Status:** Review Complete
**Scope:** Four competing traceability approaches evaluated against 10 technical dimensions

---

## Executive Summary

This review evaluates four traceability approaches for Ditto Factory's agent orchestration pipeline. The critical constraint is the **cross-process boundary**: the controller (Python/FastAPI) communicates with agent pods (bash + `claude -p`) exclusively through Redis key-value pairs. The agent entrypoint is a bash script that has no Python runtime and publishes results via `redis-cli`. Any tracing solution must work within this constraint.

**Verdict:** Approach B (Structured Logs + SQLite) for immediate implementation, with a clear migration path to Approach A (OpenTelemetry) when scale demands it. Approaches C and D are rejected for reasons detailed below.

---

## 1. Architectural Fit

### How each approach integrates with the existing codebase

The current system has these instrumentation-relevant touchpoints:

| Component | File | Current Instrumentation |
|-----------|------|------------------------|
| Orchestrator | `controller/src/controller/orchestrator.py` | `logger.info/exception` only |
| TaskClassifier | `controller/src/controller/skills/classifier.py` | `logger.warning` on fallback |
| SkillInjector | `controller/src/controller/skills/injector.py` | `logger.warning` on budget drop |
| JobSpawner | `controller/src/controller/jobs/spawner.py` | None |
| JobMonitor | `controller/src/controller/jobs/monitor.py` | `logger.error` on timeout |
| PerformanceTracker | `controller/src/controller/skills/tracker.py` | `logger.exception` on failures |
| SafetyPipeline | `controller/src/controller/jobs/safety.py` | `logger.info/exception` |
| Agent Entrypoint | `images/agent/entrypoint.sh` | `echo` statements only |
| Redis State | `controller/src/controller/state/redis_state.py` | None |

**Key observation:** The agent pod is a bash script running in a Node.js-based Docker image (`node:22-slim`). It has `jq`, `redis-cli`, and `claude` CLI available. It does NOT have Python. Any agent-side tracing must work in bash or be injected via the Claude process itself.

#### Approach A: OpenTelemetry-Native

**Controller side (Python):** Good fit. The `opentelemetry-api` and `opentelemetry-sdk` packages integrate cleanly with FastAPI. Auto-instrumentation for `redis`, `httpx`, and `asyncio` is mature. Each orchestrator method becomes a span:

```python
# orchestrator.py — what instrumentation looks like
from opentelemetry import trace
tracer = trace.get_tracer("ditto-factory.orchestrator")

async def _spawn_job(self, thread, task_request, ...):
    with tracer.start_as_current_span("orchestrator.spawn_job",
        attributes={"thread.id": thread.id, "source": task_request.source}
    ) as span:
        # classify
        with tracer.start_as_current_span("classifier.classify"):
            classification = await self._classifier.classify(...)
        # inject
        with tracer.start_as_current_span("injector.format"):
            skills_payload = self._injector.format_for_redis(matched_skills)
        # ... propagate trace context through Redis ...
```

**Agent side (bash):** Poor fit. OTel has no bash SDK. You would need to either:
1. Shell out to a sidecar/init container that speaks OTLP (adds infra complexity)
2. Use `curl` to POST spans to the collector (fragile, adds latency)
3. Write trace events to a file and have a sidecar ship them (delays, complexity)

**Changes required:**
- `pyproject.toml`: Add `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp`, `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-redis`
- `orchestrator.py`: Wrap each phase in spans (~40 lines changed)
- `classifier.py`: Wrap classify/embed/search in spans (~20 lines)
- `spawner.py`: Wrap K8s API calls in spans (~10 lines)
- `monitor.py`: Wrap polling loop in spans (~10 lines)
- `redis_state.py`: Inject W3C traceparent into task payload (~5 lines)
- `entrypoint.sh`: Extract traceparent, emit spans via curl or file (~30 lines, fragile)
- **New infra:** OTel Collector deployment, Jaeger/Tempo backend

**Integration score: 6/10** — Excellent on controller side, poor on agent side.

#### Approach B: Lightweight Structured Logs + SQLite

**Controller side (Python):** Excellent fit. The codebase already uses `logging.getLogger(__name__)` uniformly. Adding structured fields is a minimal change:

```python
# A thin wrapper that writes JSON events to SQLite
class TraceLogger:
    def __init__(self, db_path: str):
        self._db_path = db_path

    async def event(self, trace_id: str, span_name: str, **attrs):
        await self._insert(trace_id, span_name, attrs)
```

This pattern mirrors the existing `PerformanceTracker` which already uses `aiosqlite` and follows the same record/query pattern.

**Agent side (bash):** Good fit. The entrypoint already uses `redis-cli` and `jq`. Emitting trace events is natural:

```bash
# entrypoint.sh — structured trace event via Redis
emit_trace() {
    local span="$1" status="$2"
    redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" RPUSH "trace:$THREAD_ID" \
        "$(jq -n --arg span "$span" --arg status "$status" --arg ts "$(date -u +%FT%TZ)" \
        '{span: $span, status: $status, timestamp: $ts}')"
}
emit_trace "agent.start" "ok"
# ... after claude runs ...
emit_trace "agent.claude_complete" "exit_$EXIT_CODE"
```

**Changes required:**
- `pyproject.toml`: No new dependencies (aiosqlite already present)
- `orchestrator.py`: Add `trace_id` generation, pass to logger (~15 lines)
- `redis_state.py`: Add `trace_id` to task payload, add trace event methods (~20 lines)
- `entrypoint.sh`: Add `emit_trace` function, ~10 calls (~25 lines)
- `tracker.py`: Extend SQLite schema with trace table (~30 lines)
- **New infra:** None

**Integration score: 9/10** — Follows existing patterns, zero new dependencies.

#### Approach C: Event Sourcing with Redis Streams

**Controller side (Python):** Moderate fit. `RedisState` already has `append_stream_event` and `read_stream` methods using `XADD`/`XRANGE`. However, the existing usage is for simple string events, not structured trace data. Event sourcing requires:
- Strict event schemas (versioned)
- A materializer process to build read models
- Consumer groups for reliable processing

```python
# redis_state.py already has this foundation:
async def append_stream_event(self, thread_id: str, event: str) -> None:
    await self._redis.xadd(f"agent:{thread_id}", {"event": event})
```

But event sourcing requires evolving this to typed events with guaranteed ordering, idempotent consumers, and a separate materialization pipeline.

**Agent side (bash):** Good fit. Same as Approach B — `redis-cli XADD` is straightforward:

```bash
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" XADD "traces:$THREAD_ID" '*' \
    span "agent.start" status "ok" ts "$(date -u +%FT%TZ)"
```

**Changes required:**
- `redis_state.py`: Major refactor to support typed events, consumer groups (~100 lines)
- `orchestrator.py`: Emit domain events instead of direct state mutations (~60 lines)
- **New:** Materializer service that consumes streams and writes SQLite read models (~200 lines)
- **New:** Event schema definitions with versioning (~50 lines)
- `entrypoint.sh`: Add XADD calls (~15 lines)
- **New infra:** None (Redis already present), but need materializer process

**Integration score: 5/10** — Significant refactoring. The materializer is a new process to operate.

#### Approach D: Langfuse-Integrated

**Controller side (Python):** Good fit for LLM-specific tracing. The `@observe` decorator works well:

```python
from langfuse.decorators import observe, langfuse_context

@observe(name="orchestrator.spawn_job")
async def _spawn_job(self, thread, task_request, ...):
    langfuse_context.update_current_trace(
        session_id=thread.id,
        metadata={"source": task_request.source}
    )
```

**Agent side (bash):** Poor fit. Langfuse's SDK is Python-only. The agent entrypoint is bash. Options:
1. Add Python to the Docker image (increases image size from ~300MB to ~500MB+)
2. Use Langfuse's REST API via curl (complex, auth management)
3. Only trace controller-side (loses the most valuable data)

**Changes required:**
- `pyproject.toml`: Add `langfuse` (~5MB dependency)
- `orchestrator.py`: Add `@observe` decorators (~20 lines)
- `classifier.py`: Add `@observe` decorators (~10 lines)
- `Dockerfile`: Add Python + langfuse SDK (significant image change) OR accept no agent-side tracing
- **New infra:** Self-hosted Langfuse (Docker Compose: PostgreSQL + Langfuse server + ClickHouse)

**Integration score: 5/10** — Good for LLM tracing, but the bash entrypoint and infra requirements hurt.

---

## 2. Cross-Process Tracing

**The core problem:** The orchestrator writes a JSON payload to `task:{thread_id}` in Redis. The agent pod reads it, runs `claude -p`, and writes results to `result:{thread_id}`. There is no shared memory, no direct RPC, no sidecar. The Redis key-value boundary is the only communication channel.

### Current Redis payload structure

```python
# orchestrator.py, line ~85
await self._redis.push_task(thread_id, {
    "task": task_request.task,
    "system_prompt": system_prompt,
    "repo_url": f"https://github.com/{thread.repo_owner}/{thread.repo_name}.git",
    "branch": branch,
    "skills": skills_payload,  # list of {name, content} dicts
})
```

### How each approach propagates context

#### Approach A: W3C Trace Context in Redis

```python
# Controller side
from opentelemetry.context.propagation import inject
carrier = {}
inject(carrier)  # Writes traceparent header
task_payload["traceparent"] = carrier.get("traceparent")

# Agent side (bash) — must parse traceparent
TRACEPARENT=$(echo "$TASK_JSON" | jq -r '.traceparent // empty')
# Format: 00-{trace_id}-{span_id}-{flags}
# Must include in any OTel span creation... but how?
# curl -X POST http://otel-collector:4318/v1/traces -d '...' — fragile
```

**Reliability:** Medium. The controller side is solid (OTel SDK handles propagation). The agent side is fragile — constructing valid OTLP JSON in bash is error-prone, and the collector endpoint must be reachable from agent pods.

#### Approach B: trace_id in Redis payload

```python
# Controller side
import uuid
trace_id = uuid.uuid4().hex
task_payload["trace_id"] = trace_id

# Agent side (bash)
TRACE_ID=$(echo "$TASK_JSON" | jq -r '.trace_id')
# Use trace_id in all Redis trace events
redis-cli RPUSH "trace:$TRACE_ID" '{"span": "agent.start", ...}'
```

**Reliability:** High. A UUID string is trivially serialized/deserialized. No parsing ambiguity. The agent writes trace events to a Redis list keyed by `trace_id`, which the controller can read after job completion.

#### Approach C: Redis Stream key contains trace_id

```bash
# Natural — the stream key IS the trace context
redis-cli XADD "traces:$THREAD_ID" '*' span "agent.start" ...
```

**Reliability:** High. Redis Streams are the communication medium AND the trace store. No separate propagation needed. However, `THREAD_ID` is not the same as a trace_id (one thread can have multiple job runs). Need `traces:{thread_id}:{job_id}` or similar.

#### Approach D: Langfuse trace_id in Redis payload

```python
# Controller side
from langfuse import Langfuse
trace = langfuse.trace(name="task", session_id=thread_id)
task_payload["langfuse_trace_id"] = trace.id

# Agent side — needs Langfuse SDK (Python) to create child spans
# Without Python in the container, this is a dead end
```

**Reliability:** Low for agent-side. The Langfuse SDK requires Python. Without it, you can only trace the controller half.

### Cross-process verdict

| Approach | Controller→Agent | Agent→Controller | Overall |
|----------|-----------------|-----------------|---------|
| A (OTel) | Solid (W3C standard) | Fragile (bash→OTLP) | Medium |
| B (Logs+SQLite) | Simple (UUID in JSON) | Simple (Redis list) | **High** |
| C (Event Sourcing) | Natural (shared stream) | Natural (shared stream) | **High** |
| D (Langfuse) | Simple (trace_id) | Broken (no Python) | **Low** |

---

## 3. Agent-Side Instrumentation

**The critical challenge.** The agent pod runs:

```bash
claude "${CLAUDE_ARGS[@]}" 2>"$STDERR_FILE"
EXIT_CODE=$?
```

Claude Code is a black box from the entrypoint's perspective. It reads the task, reasons about it, invokes tools (Read, Edit, Bash, etc.), makes commits, and exits. The entrypoint captures:
- `EXIT_CODE` (0 or non-zero)
- `STDERR` (captured to file)
- `COMMIT_COUNT` (counted via `git rev-list`)
- `BRANCH` (known from task payload)

**What we WANT to capture but currently cannot:**
1. Claude's reasoning steps (thinking tokens)
2. Tool invocations (which files read, which commands run)
3. Time spent per tool invocation
4. Token usage (input/output/cache)
5. Intermediate failures and retries within Claude

### What each approach can actually capture

#### Approach A (OTel)
- **Controller side:** Full span hierarchy for orchestration
- **Agent side:** Only pre/post Claude execution. Cannot instrument inside `claude -p`
- **Claude internals:** No. The Claude CLI does not emit OTel spans. Would need Anthropic to add `--otel-endpoint` flag or similar
- **Workaround:** Parse stderr for tool usage patterns after execution (post-hoc, not real-time)

#### Approach B (Structured Logs + SQLite)
- **Controller side:** Full structured event log
- **Agent side:** Pre/post Claude, plus stderr parsing
- **Claude internals:** Same limitation — cannot instrument inside the CLI
- **Advantage:** Can parse Claude's stderr output for structured data. The `2>"$STDERR_FILE"` capture already exists. Post-execution parsing:

```bash
# Parse tool invocations from Claude's stderr (if available)
TOOL_COUNT=$(grep -c "Tool:" "$STDERR_FILE" 2>/dev/null || echo 0)
emit_trace "agent.claude_complete" "exit_$EXIT_CODE" "tools=$TOOL_COUNT"
```

#### Approach C (Event Sourcing)
- Same agent-side limitations as B
- **Advantage:** Could enable real-time streaming if Claude's output were piped:

```bash
# Hypothetical: pipe Claude output to both file and Redis stream
claude "${CLAUDE_ARGS[@]}" 2>&1 | tee >(while read line; do
    redis-cli XADD "traces:$THREAD_ID" '*' line "$line"
done) > "$OUTPUT_FILE"
```

This is theoretically possible but fragile (buffering, partial lines, Redis connection failures mid-stream).

#### Approach D (Langfuse)
- Best-in-class for LLM tracing IF you have SDK access
- Cannot use from bash entrypoint
- Would require restructuring the agent to be a Python wrapper around `claude -p`

### The honest truth about agent-side instrumentation

**None of these approaches can instrument inside `claude -p`.** The Claude CLI is a closed binary. All approaches are limited to:

1. **Pre-execution:** task received, skills injected, repo cloned
2. **Post-execution:** exit code, stderr, commit count, branch pushed
3. **Stderr parsing:** Potentially extract tool names, error messages (format not guaranteed)

The only way to get deeper Claude instrumentation would be:
- Use the Anthropic API directly instead of the CLI (major architecture change)
- Wait for Claude CLI to add structured output/tracing flags
- Use Claude's `--output-format json` if available (not currently used)

**Agent-side verdict:** All approaches are roughly equivalent. The differentiator is controller-side richness and how easily you can correlate controller + agent data.

---

## 4. Data Model Quality

### The hierarchy we need to represent

```
Trace (thread_id + job_id)
├── orchestrator.handle_task
│   ├── state.get_thread
│   ├── state.try_acquire_lock
│   └── orchestrator.spawn_job
│       ├── prompt.build_system_prompt
│       ├── classifier.classify
│       │   ├── embedding.embed (Voyage-3 API call)
│       │   ├── registry.search_by_embedding
│       │   ├── tracker.compute_boost (per skill)
│       │   └── [fallback] registry.search_by_tags
│       ├── resolver.resolve
│       ├── injector.format_for_redis
│       ├── redis.push_task
│       ├── spawner.spawn (K8s API)
│       ├── state.create_job
│       └── tracker.record_injection
├── [CROSS-PROCESS BOUNDARY — Redis]
├── agent.start
│   ├── agent.fetch_task
│   ├── agent.clone_repo
│   ├── agent.inject_skills
│   ├── agent.run_claude
│   │   └── [opaque — exit_code + stderr only]
│   ├── agent.push_branch
│   └── agent.publish_result
├── [CROSS-PROCESS BOUNDARY — Redis]
├── orchestrator.handle_job_completion
│   ├── monitor.wait_for_result
│   ├── state.update_job_status
│   ├── safety.process
│   │   ├── safety.auto_pr
│   │   ├── safety.validate (anti-stall)
│   │   └── integration.report_result
│   └── tracker.record_outcome
```

### How each approach models this

#### Approach A (OTel): Span tree

OTel's span model is purpose-built for this hierarchy. Each node is a span with `trace_id`, `span_id`, `parent_span_id`. The cross-process boundary is handled by propagating `traceparent`.

```
Span attributes: name, start_time, end_time, status, attributes{}
Parent-child: explicit via parent_span_id
Cross-process: traceparent header carries trace_id + parent_span_id
```

**Quality: 9/10** — Native hierarchical model. The GenAI semantic conventions add LLM-specific attributes (`gen_ai.system`, `gen_ai.usage.input_tokens`). However, the agent side cannot create proper child spans from bash, so the tree has a gap.

#### Approach B (Structured Logs + SQLite): Flat events with trace correlation

```sql
CREATE TABLE trace_events (
    id INTEGER PRIMARY KEY,
    trace_id TEXT NOT NULL,
    span_name TEXT NOT NULL,
    parent_span TEXT,  -- nullable, for hierarchy
    timestamp REAL NOT NULL,
    duration_ms REAL,
    status TEXT,
    attributes TEXT,  -- JSON blob
    source TEXT  -- 'controller' | 'agent'
);
CREATE INDEX idx_trace ON trace_events(trace_id);
CREATE INDEX idx_span ON trace_events(span_name);
```

**Quality: 7/10** — Can represent hierarchy via `parent_span`, but it's opt-in and easy to get wrong. No enforcement of tree structure. Ad-hoc queries are SQL-native. The flat model is actually better for some queries (e.g., "all classifier spans across all traces").

#### Approach C (Event Sourcing): Immutable event log

```
Stream: traces:{thread_id}:{job_id}
Events:
  1-0: {type: "task.received", task: "...", timestamp: "..."}
  2-0: {type: "classifier.started", ...}
  3-0: {type: "classifier.embedding.completed", duration_ms: 120, ...}
  4-0: {type: "classifier.completed", skills: [...], fallback: false, ...}
  ...
```

**Quality: 8/10** — Natural temporal ordering (Redis Stream IDs are timestamps). Hierarchy must be reconstructed from event types (e.g., `classifier.started` → `classifier.completed` bracket). The event log is the source of truth; materialized views can build any projection.

**Trade-off:** Events are inherently flat with temporal ordering. Trees must be derived. This is powerful (you can build multiple projections) but complex (you must build the materializer).

#### Approach D (Langfuse): Trace → Generation → Span tree

Langfuse's model is purpose-built for LLM applications:
```
Trace
├── Generation (LLM call with token counts, model, cost)
├── Span (arbitrary operation)
│   ├── Generation
│   └── Span
```

**Quality: 8/10 for LLM tracing, 5/10 for general orchestration.** Langfuse excels at modeling LLM calls but is awkward for K8s job spawning, Redis operations, and safety pipeline steps. The controller's orchestration flow is mostly non-LLM operations.

### Data model verdict

| Approach | Hierarchy | Cross-process | LLM-specific | General ops |
|----------|-----------|--------------|--------------|-------------|
| A (OTel) | Native tree | W3C standard | GenAI conventions | Excellent |
| B (SQLite) | Manual | Manual | Manual | Good |
| C (Events) | Derived | Natural | Manual | Good |
| D (Langfuse) | Native tree | Broken | Excellent | Poor |

---

## 5. Query Flexibility

### Test queries

1. "Show me all traces where the classifier fell back to tag-based search"
2. "Show me all tool invocations that took >30s"
3. "What is the average time from task received to agent start?"
4. "Which skills have the highest correlation with failed runs?"
5. "Show me the full trace for thread abc123"

#### Approach A (OTel + Jaeger/Tempo)

```
# Query 1: Requires span attributes
# Jaeger: service=ditto-factory tag=classifier.fallback=true
# Tempo/TraceQL: {span.classifier.fallback = true}

# Query 4: Requires joining trace data with outcome data
# OTel backends are trace stores, not analytics engines
# Would need to export to a data warehouse for correlation queries
```

**Flexibility: 6/10** — Good for single-trace exploration. Poor for aggregate analytics. TraceQL (Tempo) is improving but still limited compared to SQL. Cross-referencing with PerformanceTracker data requires a separate system.

#### Approach B (SQLite + SQL)

```sql
-- Query 1: Classifier fallback
SELECT trace_id, timestamp FROM trace_events
WHERE span_name = 'classifier.classify'
AND json_extract(attributes, '$.fallback') = true;

-- Query 2: Slow operations
SELECT * FROM trace_events WHERE duration_ms > 30000;

-- Query 3: Task-to-agent latency
SELECT t1.trace_id,
       (t2.timestamp - t1.timestamp) as latency_seconds
FROM trace_events t1
JOIN trace_events t2 ON t1.trace_id = t2.trace_id
WHERE t1.span_name = 'orchestrator.handle_task'
AND t2.span_name = 'agent.start';

-- Query 4: Skill-failure correlation (join with existing performance_tracker)
SELECT s.slug, te.trace_id, su.exit_code
FROM trace_events te
JOIN skill_usage su ON te.trace_id = su.thread_id
JOIN skills s ON su.skill_id = s.id
WHERE te.span_name = 'classifier.classify';
```

**Flexibility: 9/10** — SQL is the most flexible query language available. Can join with existing `PerformanceTracker` tables. Supports arbitrary aggregations, window functions, CTEs. SQLite's JSON functions handle attribute queries.

#### Approach C (Redis Streams → Materialized SQLite)

Same as Approach B for queries (materialized into SQLite). But adds latency — events must be materialized before they're queryable. Real-time queries require reading raw streams.

**Flexibility: 8/10** — Same as B after materialization, but with a lag. Raw stream queries (XRANGE with filtering) are limited.

#### Approach D (Langfuse API)

```python
# Langfuse SDK query
traces = langfuse.get_traces(session_id="thread_abc123")
# Limited filtering — Langfuse's query API is not SQL-flexible
# For complex queries, must export to a data warehouse
```

**Flexibility: 5/10** — Good UI for exploring individual traces. Limited programmatic query API. No SQL-level flexibility without exporting data.

---

## 6. Performance Impact

### Hot path analysis

The critical hot path is `Orchestrator._spawn_job()`. Currently:

```
handle_task → get_thread → try_acquire_lock → _spawn_job
  → build_system_prompt → classify(embed + search) → format_for_redis
  → push_task → spawner.spawn(K8s API) → create_job → record_injection
```

The `classify` step already includes a Voyage-3 API call (~200-500ms). The K8s Job creation is ~100-300ms. Total hot path: ~500-1000ms.

#### Approach A (OTel)

- **Span creation:** ~1-5μs per span (negligible)
- **Batch export:** Async, does not block hot path. OTel SDK batches spans and exports every 5s (configurable)
- **Memory:** Span objects held until export (~1KB per span, ~20 spans per trace = 20KB)
- **Risk:** If OTel Collector is down, SDK buffers in memory. Default buffer: 2048 spans. At 50 runs/day with 20 spans each = 1000 spans/day — no risk of overflow

**Overhead: ~0.1ms on hot path (negligible)**

#### Approach B (Structured Logs + SQLite)

- **SQLite write:** ~0.5-2ms per INSERT (with WAL mode)
- **Per trace:** ~15-20 events × 1ms = 15-20ms total, but can be async
- **Risk:** SQLite write lock contention under concurrent traces. WAL mode helps but has limits

**If synchronous: ~15-20ms on hot path (acceptable)**
**If async (fire-and-forget): ~0.1ms on hot path (negligible)**

Recommendation: Use `asyncio.create_task()` for trace writes, same pattern as `record_injection`:

```python
# Fire-and-forget, same error handling as existing tracker
try:
    await self._tracer.event(trace_id, "classifier.classify", ...)
except Exception:
    logger.exception("Trace event failed")
```

#### Approach C (Event Sourcing)

- **Redis XADD:** ~0.1-0.5ms per event (Redis is fast)
- **Per trace:** ~15-20 events × 0.3ms = 5-6ms
- **Materializer:** Runs separately, no hot path impact
- **Risk:** Redis memory growth. Each event is ~200 bytes. At 1000 runs/day × 20 events = 4MB/day. With 7-day retention: 28MB. Negligible

**Overhead: ~5ms on hot path (negligible)**

#### Approach D (Langfuse)

- **SDK call:** Network call to Langfuse server for each span. Batched by SDK (default: 15s flush interval)
- **If Langfuse is self-hosted locally:** ~1-5ms per batch flush
- **If Langfuse is remote:** ~50-200ms per batch flush
- **Risk:** Langfuse SDK is async but adds memory pressure for buffered events

**Overhead: ~0.1ms on hot path (SDK buffers), but batch flush adds background load**

### Performance verdict

All approaches have negligible hot-path impact when implemented with async/batched writes. The differentiator is operational risk — what happens when the tracing backend is slow or down (covered in Section 7).

---

## 7. Failure Modes

### What happens when tracing fails?

#### Approach A (OTel)

| Failure | Impact | Recovery |
|---------|--------|----------|
| OTel Collector down | Spans buffer in memory (2048 default), then drop silently | Collector restart; lost spans are gone |
| Collector disk full | Same as above | Operator intervention |
| Malformed span | SDK logs warning, continues | Fix instrumentation |
| Network partition (agent pod → collector) | Agent-side spans lost | Acceptable — agent tracing is already limited |

**Does it block the agent?** No. OTel SDK is designed to be non-blocking. `tracer.start_span()` never raises exceptions to application code.

**Data loss risk:** Medium. Spans can be silently dropped if buffer overflows.

#### Approach B (Structured Logs + SQLite)

| Failure | Impact | Recovery |
|---------|--------|----------|
| SQLite file locked | Write fails, caught by try/except | Retry or drop event |
| SQLite disk full | Write fails, caught by try/except | Operator intervention |
| SQLite corruption | Trace data lost, but PerformanceTracker uses separate DB | Recreate DB |
| Redis RPUSH fails (agent side) | Agent trace events lost, agent continues | Acceptable |

**Does it block the agent?** No, if implemented with try/except (same pattern as `record_injection`).

**Data loss risk:** Low. SQLite is ACID. Events either write or don't. No silent drops. Agent-side Redis events can fail but the agent's primary function (run Claude, push results) is unaffected.

#### Approach C (Event Sourcing)

| Failure | Impact | Recovery |
|---------|--------|----------|
| Redis down | ALL events lost AND task dispatch broken | Redis is already a hard dependency — if it's down, the whole system is down |
| Materializer crash | Events buffered in Redis, materialized on restart | Restart materializer |
| Materializer lag | Queries show stale data | Acceptable for analytics |
| Stream too large | Redis memory pressure | Set MAXLEN on streams |

**Does it block the agent?** No (agent writes are fire-and-forget). But if Redis itself is down, the agent can't read its task either — tracing failure is the least of your problems.

**Data loss risk:** Medium. Redis persistence depends on configuration (RDB snapshots, AOF). If Redis is configured with `appendonly yes`, events survive restarts. If not, a crash loses recent events.

**Critical concern:** This approach couples tracing availability to Redis availability. Currently Redis is a task queue with 1-hour TTLs. Making it an event store changes the durability requirements.

#### Approach D (Langfuse)

| Failure | Impact | Recovery |
|---------|--------|----------|
| Langfuse server down | SDK buffers events, eventually drops | Restart Langfuse |
| Langfuse PostgreSQL full | Langfuse server errors, SDK drops events | Operator intervention |
| Langfuse ClickHouse down | Analytics broken, ingestion may still work | Restart ClickHouse |
| Network partition | SDK drops events silently | Accept data loss |

**Does it block the agent?** No (SDK is async). But Langfuse is additional infrastructure that can fail independently.

**Data loss risk:** Medium-High. Three services (Langfuse server, PostgreSQL, ClickHouse) that can each fail. More moving parts = more failure modes.

### Failure mode verdict

| Approach | Blocks agent? | Data loss risk | Blast radius |
|----------|--------------|----------------|-------------|
| A (OTel) | No | Medium (silent drops) | Tracing only |
| B (SQLite) | No | Low (ACID writes) | Tracing only |
| C (Events) | No (but Redis is shared) | Medium | Tracing + task dispatch (shared Redis) |
| D (Langfuse) | No | Medium-High | Tracing only, but 3 services to fail |

**Approach B has the best failure characteristics:** ACID writes, no silent drops, isolated from the primary data path, no new infrastructure to fail.

---

## 8. Migration Path

### Can we start with one and evolve to another?

#### B → A (Structured Logs → OpenTelemetry)

This is the **cleanest migration path**. Here's why:

1. **Phase 1 (now):** Implement Approach B. Define trace events with `trace_id`, `span_name`, `parent_span`, `timestamp`, `duration_ms`, `attributes`.

2. **Phase 2 (when needed):** Add OTel SDK alongside structured logs. The `TraceLogger.event()` method becomes a wrapper that:
   - Writes to SQLite (backward compatibility)
   - Creates an OTel span (forward compatibility)

3. **Phase 3 (at scale):** Remove SQLite writes, rely on OTel backend.

The key insight: if you design Approach B's schema to match OTel's span model (trace_id, span_id, parent_span_id, attributes), migration is mechanical.

```python
# Phase 2: Dual-write wrapper
class TraceLogger:
    async def event(self, trace_id, span_name, parent_span=None, **attrs):
        # Write to SQLite (existing)
        await self._sqlite_insert(trace_id, span_name, parent_span, attrs)
        # Write to OTel (new)
        if self._otel_tracer:
            with self._otel_tracer.start_as_current_span(span_name, attributes=attrs):
                pass
```

**Migration cost: Low.** The span abstraction is the same; only the backend changes.

#### B → C (Structured Logs → Event Sourcing)

Possible but unnecessary. Event sourcing adds complexity (materializer, event versioning) without proportional benefit for tracing. If you need event sourcing for business reasons (audit log, replay), that's a separate decision.

**Migration cost: High.** Different paradigm, requires new infrastructure.

#### A → D (OTel → Langfuse)

Langfuse can consume OTel data via its OTLP endpoint (added in Langfuse v3). So A is a stepping stone to D if you later want LLM-specific UI.

**Migration cost: Low** (if Langfuse supports OTLP ingestion).

#### D → anything

Langfuse vendor lock-in. Its SDK emits to Langfuse's proprietary API. Moving away requires re-instrumenting everything.

**Migration cost: High.**

### Migration verdict

The only sensible migration paths are:
1. **B → A** (structured logs → OTel) — Clean, incremental, low risk
2. **A → D** (OTel → Langfuse via OTLP) — If LLM-specific UI becomes critical

Starting with B preserves maximum optionality.

---

## 9. Scalability

### Current: ~10-50 agent runs/day. Future: potentially 1000+/day.

#### Data volume estimates at 1000 runs/day

| Metric | Per run | Daily (1000 runs) | Monthly |
|--------|---------|-------------------|---------|
| Trace events | ~20 | 20,000 | 600,000 |
| Event size | ~500 bytes | 10 MB | 300 MB |
| SQLite rows | ~20 | 20,000 | 600,000 |

#### Approach A (OTel)

- **OTel Collector:** Handles millions of spans/second. No bottleneck
- **Backend (Jaeger):** Scales horizontally with Elasticsearch/Cassandra. Overkill at 1000 runs/day
- **Backend (Tempo):** Object storage (S3). Scales to billions of traces. Also overkill
- **Verdict:** Massively over-provisioned at 1000 runs/day. Makes sense at 100,000+/day

#### Approach B (SQLite)

- **SQLite limits:** Tested to billions of rows. 600K rows/month is trivial
- **Concurrent writes:** SQLite supports one writer at a time. With WAL mode, reads don't block writes. At 1000 runs/day = ~0.7 runs/minute, contention is zero
- **When SQLite breaks:** At ~10,000+ concurrent writes/second (not runs/day). That's 864M events/day. We're 4 orders of magnitude away
- **Migration trigger:** When you need concurrent write access from multiple controller replicas. At that point, move to PostgreSQL (which you already have) or OTel

**Verdict:** SQLite comfortably handles 1000 runs/day. Migration to PostgreSQL or OTel when running multiple controller replicas.

#### Approach C (Redis Streams)

- **Redis throughput:** 100K+ operations/second. 20K events/day is nothing
- **Memory:** 10MB/day with 7-day retention = 70MB. Negligible
- **Risk:** Redis is in-memory. At very high scale, trace data competes with task data for memory. Need `MAXLEN` on streams
- **Verdict:** Scales well but introduces memory pressure concerns at high volume

#### Approach D (Langfuse)

- **Self-hosted:** Depends on PostgreSQL + ClickHouse capacity. Both scale horizontally
- **Cloud:** Langfuse cloud handles the scaling
- **Risk:** Self-hosted Langfuse is another system to scale and operate
- **Verdict:** Scales but at the cost of operational complexity

### Scalability verdict

At current scale (10-50/day) and projected scale (1000/day), **all approaches are sufficient**. The differentiator is operational cost:

| Approach | Infra at 50/day | Infra at 1000/day | Infra at 100K/day |
|----------|----------------|-------------------|-------------------|
| A (OTel) | Overkill | Overkill | Right-sized |
| B (SQLite) | Perfect | Perfect | Needs PostgreSQL |
| C (Redis) | Fine | Fine | Memory pressure |
| D (Langfuse) | Overkill | Fine | Right-sized |

---

## 10. Verdict and Recommendation

### Final Rankings

| Rank | Approach | Score | Rationale |
|------|----------|-------|-----------|
| 1 | **B: Structured Logs + SQLite** | 8.2/10 | Best fit for current architecture, zero new infra, best failure modes, highest query flexibility, clean migration to OTel |
| 2 | **A: OpenTelemetry-Native** | 7.0/10 | Best data model, industry standard, but overkill for current scale and poor agent-side fit |
| 3 | **C: Event Sourcing** | 5.5/10 | Interesting but over-engineered. Materializer is unnecessary complexity. Redis durability concerns |
| 4 | **D: Langfuse-Integrated** | 4.5/10 | Best LLM tracing UI but fundamentally broken by bash entrypoint. Too much new infra |

### Recommended Path

**Phase 1 (now): Implement Approach B**

1. Add a `trace_events` table to the existing `aiosqlite` database (same pattern as `PerformanceTracker`)
2. Create a `TraceLogger` class with an `event()` method that mirrors OTel's span model
3. Instrument the orchestrator hot path (~15 events per trace)
4. Add `trace_id` to Redis task payload
5. Add `emit_trace` bash function to `entrypoint.sh` (writes to Redis list)
6. Controller reads agent trace events from Redis after job completion and writes to SQLite
7. Build report generation from SQL queries

**Phase 2 (when running multiple controller replicas): Migrate to OTel**

1. Add OTel SDK as a parallel export path
2. Deploy OTel Collector + Tempo/Jaeger
3. Gradually shift queries from SQLite to trace backend
4. Remove SQLite trace writes

**Phase 3 (if LLM-specific tracing becomes critical): Add Langfuse via OTLP**

1. Deploy self-hosted Langfuse with OTLP ingestion
2. Route OTel spans to both Tempo and Langfuse
3. Use Langfuse UI for LLM-specific exploration

### Design Principles for Phase 1

1. **Match OTel's span model** — Use `trace_id`, `span_id`, `parent_span_id`, `name`, `start_time`, `end_time`, `status`, `attributes`. This makes Phase 2 migration mechanical
2. **Fire-and-forget writes** — Never block the hot path for tracing. Use `asyncio.create_task()` with exception logging
3. **Agent events via Redis lists** — The agent writes to `trace:{trace_id}` using `RPUSH`. The controller reads and persists after job completion
4. **Separate DB file** — Use a dedicated SQLite file for traces, not the PerformanceTracker DB. Prevents trace volume from affecting skill metrics queries
5. **Retention policy** — Auto-delete traces older than 30 days via a periodic task

### What We're Giving Up

- **Real-time trace visualization** — No Jaeger/Tempo UI. Traces are queryable via SQL only. Acceptable for 10-50 runs/day
- **Distributed tracing standards** — No W3C Trace Context, no OTLP. Acceptable until multi-replica deployment
- **LLM-specific metrics** — No token counts, no cost tracking in traces. The PerformanceTracker handles outcome metrics separately
- **Agent-internal visibility** — Cannot see inside `claude -p`. This is a Claude CLI limitation, not an approach limitation. All four approaches share this constraint

### Architecture Decision Record

```
# ADR-005: Traceability Implementation Approach

## Status
Proposed

## Context
Ditto Factory needs end-to-end traceability across the controller-agent boundary
to debug failures, measure performance, and understand agent behavior. The agent
runs as a bash script in a K8s pod with no Python runtime, communicating via Redis
key-value pairs. Four approaches were evaluated across 10 technical dimensions.

## Decision
Implement structured JSON trace events persisted to SQLite (Approach B), with the
trace schema designed to match OpenTelemetry's span model for future migration.
Agent-side events are buffered in Redis lists and persisted by the controller
after job completion.

## Consequences
- Easier: Zero new infrastructure, follows existing codebase patterns, SQL query
  flexibility, ACID writes with clear failure modes
- Harder: No real-time trace UI (must build or query via SQL), manual hierarchy
  management, eventual migration to OTel when scaling to multiple controller replicas
```
