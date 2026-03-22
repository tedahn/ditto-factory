# Phase 5: Migration Path, Testing Strategy & Rollout

## Status: Proposed
## Date: 2026-03-21
## Approach: Option 4 — B Enhanced (Structured Trace Spans + SQLite)

---

## 1. Future Migration Paths

### 1.1 B -> D (Langfuse) Migration

**Trigger:** Month 3-6, or when the team needs: cost tracking dashboards, prompt version management, or multi-user trace exploration UI.

#### Field Mapping: TraceSpan -> Langfuse

| TraceSpan Field | Langfuse Concept | Notes |
|---|---|---|
| `trace_id` | `Trace.id` | W3C 128-bit format maps directly |
| `span_id` | `Observation.id` | W3C 64-bit format maps directly |
| `parent_span_id` | `Observation.parentObservationId` | Direct mapping |
| `operation_name` | `Observation.name` | e.g., `classify_task`, `spawn_k8s_job` |
| `model` | `Generation.model` | Only for `GENERATION`-type observations |
| `tokens_input` | `Generation.usage.input` | Direct mapping |
| `tokens_output` | `Generation.usage.output` | Direct mapping |
| `status` | `Observation.statusMessage` | Map `"ok"` -> `None`, `"error"` -> error message |
| `started_at` | `Observation.startTime` | Direct mapping |
| `ended_at` | `Observation.endTime` | Direct mapping |
| `input_summary` | `Observation.input` | Direct mapping |
| `output_summary` | `Observation.output` | Direct mapping |
| `tool_name` | `Span.name` (prefixed `tool:`) | For tool invocation spans |
| `thread_id` | `Trace.sessionId` | Groups traces by conversation thread |
| `job_id` | `Trace.metadata.job_id` | Correlation metadata |
| `error_type` | `Observation.statusMessage` | Included when `status == "error"` |
| `reasoning` | `Generation.metadata.reasoning` | Stored as metadata on generations |

#### Observation Type Mapping

Design this NOW in Phase 1 to make migration near-trivial:

```python
# Add to TraceSpan dataclass
class ObservationType(str, Enum):
    GENERATION = "GENERATION"  # LLM API calls (Claude, Voyage-3 embeddings)
    SPAN = "SPAN"              # Operations (classify, spawn, monitor)
    EVENT = "EVENT"            # Point-in-time occurrences (task_received, safety_check_passed)
```

| Ditto Factory Operation | Observation Type | Rationale |
|---|---|---|
| Claude API call inside agent | `GENERATION` | LLM call with tokens/cost |
| Voyage-3 embedding call | `GENERATION` | LLM call with tokens/cost |
| `orchestrate` (full _spawn_job) | `SPAN` | Container operation with duration |
| `classify_task` | `SPAN` | Operation with start/end |
| `inject_skills` | `SPAN` | Operation with start/end |
| `resolve_agent_type` | `SPAN` | Operation with start/end |
| `spawn_k8s_job` | `SPAN` | Operation with start/end |
| `agent_execution` | `SPAN` | Operation with start/end |
| `tool:{tool_name}` | `SPAN` | Tool use with duration |
| `monitor_result` | `SPAN` | Polling operation with duration |
| `task_received` | `EVENT` | Point-in-time, no duration |
| `safety_check_passed` | `EVENT` | Point-in-time, no duration |
| `job_completed` | `EVENT` | Point-in-time, no duration |

#### Schema Decisions to Make NOW (Phase 1)

1. **W3C Trace Context IDs** — Generate `trace_id` as 32-hex-char (128-bit) and `span_id` as 16-hex-char (64-bit) using W3C format. Both Langfuse and OTel understand this natively.

```python
import uuid

def generate_trace_id() -> str:
    """128-bit W3C trace ID."""
    return uuid.uuid4().hex  # 32 hex chars

def generate_span_id() -> str:
    """64-bit W3C span ID."""
    return uuid.uuid4().hex[:16]  # 16 hex chars
```

2. **Cost and token fields as first-class** — Not buried in a generic `attributes` dict.

```python
@dataclass
class TraceSpan:
    # ... existing fields ...
    observation_type: ObservationType  # NEW: GENERATION, SPAN, EVENT
    cost_usd: float | None = None     # NEW: computed from model pricing
    tokens_input: int | None = None   # Already planned
    tokens_output: int | None = None  # Already planned
```

3. **TraceStore as an abstraction** — Use a protocol/interface so the backend can be swapped.

```python
from typing import Protocol

class TraceBackend(Protocol):
    async def write_span(self, span: TraceSpan) -> None: ...
    async def write_batch(self, spans: list[TraceSpan]) -> None: ...
    async def query_by_trace(self, trace_id: str) -> list[TraceSpan]: ...
    async def query_by_job(self, job_id: str) -> list[TraceSpan]: ...

class SQLiteTraceBackend:
    """Phase 1-4 implementation."""
    ...

class LangfuseTraceBackend:
    """Phase 5+ migration target."""
    ...
```

#### Migration Steps (Estimated: 2-5 days)

| Day | Task | Effort |
|-----|------|--------|
| 1 | Install `langfuse` SDK, configure connection (env vars: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`) | S |
| 1 | Implement `LangfuseTraceBackend` that maps `TraceSpan` -> Langfuse SDK calls (`langfuse.trace()`, `trace.span()`, `trace.generation()`, `trace.event()`) | M |
| 2 | Dual-write mode: write to both SQLite and Langfuse simultaneously for validation | M |
| 2-3 | Validate trace hierarchy in Langfuse UI matches SQLite reports | M |
| 3-4 | Migrate report queries from raw SQL to Langfuse API (`langfuse.fetch_traces()`, `langfuse.fetch_observations()`) | M |
| 4-5 | Update CLI commands to use Langfuse API or keep SQLite as local cache | S |
| 5 | Remove SQLite backend (or keep as local fallback), update config | S |

#### What Breaks

- **Direct SQL queries** — Any manual `SELECT * FROM trace_spans WHERE ...` stops working. Must use Langfuse API or UI.
- **Offline operation** — SQLite works without network; Langfuse requires connectivity (mitigated by Langfuse SDK's local queue + async flush).
- **Report generation speed** — SQLite queries are instant; Langfuse API adds network latency. Consider caching.
- **Data residency** — Traces leave your infrastructure unless self-hosting Langfuse.

#### What Improves

- **Langfuse UI** — Interactive trace explorer, timeline view, cost dashboard out of the box.
- **Cost tracking** — Automatic cost computation per model, aggregated by trace/user/time period.
- **Prompt management** — Version and A/B test system prompts through Langfuse.
- **Evaluation framework** — Score traces with custom evals, track quality over time.
- **Team collaboration** — Multiple users can explore traces without SSH/SQL access.
- **Alerting** — Set up alerts for cost spikes, error rate increases, latency regressions.

---

### 1.2 B -> A (OTel) Migration

**Trigger:** 3+ services needing distributed tracing, or organizational mandate for OTel standardization.

#### Field Mapping: TraceSpan -> OTel GenAI Semantic Conventions

| TraceSpan Field | OTel Span Attribute | Semantic Convention |
|---|---|---|
| `trace_id` | `TraceId` | W3C format — direct binary mapping |
| `span_id` | `SpanId` | W3C format — direct binary mapping |
| `parent_span_id` | `ParentSpanId` | Direct mapping via `SpanContext` |
| `operation_name` | `Span.name` | Maps to `gen_ai.operation.name` values |
| `agent_name` | `gen_ai.agent.name` | OTel GenAI agent semantic convention |
| `model` | `gen_ai.request.model` | Standard GenAI attribute |
| `tokens_input` | `gen_ai.usage.input_tokens` | Standard GenAI attribute |
| `tokens_output` | `gen_ai.usage.output_tokens` | Standard GenAI attribute |
| `tool_name` | `gen_ai.tool.name` | GenAI tool semantic convention |
| `tool_args` | `gen_ai.tool.call.arguments` | Serialized as JSON string |
| `status` | `SpanStatus` | Map `"ok"` -> `StatusCode.OK`, `"error"` -> `StatusCode.ERROR` |
| `error_type` | `error.type` | Standard OTel error attribute |
| `started_at` | `Span.start_time` | Direct mapping |
| `ended_at` | `Span.end_time` | Direct mapping |
| `input_summary` | `gen_ai.prompt` | Or custom attribute `ditto.input_summary` |
| `output_summary` | `gen_ai.completion` | Or custom attribute `ditto.output_summary` |
| `thread_id` | `ditto.thread_id` | Custom attribute (no OTel equivalent) |
| `job_id` | `ditto.job_id` | Custom attribute (no OTel equivalent) |

#### operation_name -> gen_ai.operation.name Mapping

| TraceSpan operation_name | OTel operation | Span Kind |
|---|---|---|
| `classify_task` | `gen_ai.operation.name: "classify"` | `INTERNAL` |
| `inject_skills` | `gen_ai.operation.name: "inject"` | `INTERNAL` |
| `resolve_agent_type` | `gen_ai.operation.name: "resolve"` | `INTERNAL` |
| `spawn_k8s_job` | Custom: `ditto.spawn` | `PRODUCER` |
| `agent_execution` | `gen_ai.operation.name: "execute"` | `CONSUMER` |
| `claude_call` | `gen_ai.operation.name: "chat"` | `CLIENT` |
| `embed_task` | `gen_ai.operation.name: "embeddings"` | `CLIENT` |
| `tool:{name}` | `gen_ai.operation.name: "execute_tool"` | `INTERNAL` |
| `monitor_result` | Custom: `ditto.monitor` | `INTERNAL` |

#### Migration Steps (Estimated: 3-7 days)

| Day | Task | Effort |
|-----|------|--------|
| 1 | Install `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp` | S |
| 1 | Configure `TracerProvider` with OTLP exporter pointing at collector (Jaeger, Grafana Tempo, etc.) | M |
| 2 | Implement `OTelTraceBackend` that creates OTel spans from `TraceSpan` objects | M |
| 2-3 | Add W3C `traceparent` serialization to Redis payload (replace raw `trace_id`/`parent_span_id` with formatted header) | M |
| 3-4 | Agent-side: create proper child spans from bash using `otel-cli` or structured log output parsed by collector | L |
| 4-5 | Validate trace hierarchy in Jaeger/Tempo UI | M |
| 5-7 | Migrate report generation to use OTel query API or keep SQLite as secondary store | M |

#### What Breaks

- **Simplicity** — OTel SDK adds dependency complexity and configuration surface.
- **Agent-side tracing** — Bash-based agents cannot natively create OTel spans. Need `otel-cli` binary or structured log parsing via OTel Collector.
- **SQLite reports** — Must be replaced with trace backend queries (Jaeger API, Tempo API, etc.).

#### What Improves

- **Distributed tracing** — Native cross-process correlation with standard propagation.
- **Ecosystem** — Hundreds of OTel-compatible backends, exporters, and visualization tools.
- **Standardization** — Industry standard; other teams/services can contribute spans.
- **Auto-instrumentation** — OTel Python SDK can auto-instrument HTTP clients, DB calls, etc.

---

## 2. Comprehensive Test Strategy

### 2.1 Unit Tests

Located in `controller/tests/test_tracing.py`.

#### TraceSpan Tests

```python
class TestTraceSpan:
    def test_create_span_with_required_fields(self):
        """Span creation with minimal fields produces valid W3C IDs."""
        span = TraceSpan(
            operation_name="test_op",
            observation_type=ObservationType.SPAN,
            thread_id="thread-123",
        )
        assert len(span.trace_id) == 32  # W3C 128-bit
        assert len(span.span_id) == 16   # W3C 64-bit
        assert span.parent_span_id is None
        assert span.status == "ok"

    def test_create_generation_span_with_tokens(self):
        """GENERATION spans include token and cost fields."""
        span = TraceSpan(
            operation_name="claude_call",
            observation_type=ObservationType.GENERATION,
            thread_id="thread-123",
            model="claude-opus-4-6",
            tokens_input=1500,
            tokens_output=800,
            cost_usd=0.0276,
        )
        assert span.observation_type == ObservationType.GENERATION
        assert span.tokens_input == 1500
        assert span.cost_usd == 0.0276

    def test_create_event_span_no_end_time(self):
        """EVENT spans have started_at but no ended_at."""
        span = TraceSpan(
            operation_name="task_received",
            observation_type=ObservationType.EVENT,
            thread_id="thread-123",
        )
        assert span.started_at is not None
        assert span.ended_at is None

    def test_span_parent_child_linking(self):
        """Child span references parent span_id."""
        parent = TraceSpan(operation_name="orchestrate", observation_type=ObservationType.SPAN, thread_id="t-1")
        child = TraceSpan(
            operation_name="classify_task",
            observation_type=ObservationType.SPAN,
            thread_id="t-1",
            trace_id=parent.trace_id,
            parent_span_id=parent.span_id,
        )
        assert child.trace_id == parent.trace_id
        assert child.parent_span_id == parent.span_id

    def test_span_serialization_roundtrip(self):
        """TraceSpan serializes to dict and deserializes without data loss."""

    def test_invalid_observation_type_rejected(self):
        """Unknown observation type raises ValueError."""
```

#### TraceStore Tests

```python
class TestSQLiteTraceBackend:
    @pytest.fixture
    def store(self, tmp_path):
        """In-memory SQLite backend for testing."""
        return SQLiteTraceBackend(db_path=str(tmp_path / "test_traces.db"))

    def test_write_single_span(self, store):
        """Single span write and read back."""

    def test_write_batch(self, store):
        """Batch write of 50 spans completes atomically."""

    def test_query_by_trace_id(self, store):
        """All spans for a trace_id are returned in chronological order."""

    def test_query_by_job_id(self, store):
        """All spans for a job_id are returned."""

    def test_query_by_thread_id(self, store):
        """All spans for a thread_id are returned."""

    def test_retention_cleanup(self, store):
        """Spans older than retention_days are deleted."""
        # Insert spans with started_at = 31 days ago
        # Run cleanup
        # Assert old spans deleted, recent spans retained

    def test_retention_cleanup_preserves_recent(self, store):
        """Cleanup does not delete spans within retention window."""

    def test_db_schema_created_on_init(self, store):
        """SQLite tables and indexes created on first use."""

    def test_concurrent_writes(self, store):
        """Multiple async writers don't corrupt the database."""
        # Use asyncio.gather with 10 concurrent write_batch calls
```

#### TraceContext Tests

```python
class TestTraceContext:
    def test_root_context_creates_trace_id(self):
        """Entering root context generates new trace_id."""
        with TraceContext.root(thread_id="t-1") as ctx:
            assert ctx.trace_id is not None
            assert ctx.current_span_id is not None
            assert ctx.parent_span_id is None

    def test_nested_context_inherits_trace_id(self):
        """Child context inherits parent's trace_id."""
        with TraceContext.root(thread_id="t-1") as root:
            with TraceContext.child(operation="classify") as child:
                assert child.trace_id == root.trace_id
                assert child.parent_span_id == root.current_span_id

    def test_context_isolation_across_tasks(self):
        """Concurrent asyncio tasks have isolated contexts (contextvars)."""

    def test_context_serialization_for_redis(self):
        """Context exports trace_id + span_id for Redis payload injection."""
        with TraceContext.root(thread_id="t-1") as ctx:
            payload = ctx.to_redis_payload()
            assert "trace_id" in payload
            assert "parent_span_id" in payload

    def test_context_deserialization_from_redis(self):
        """Context reconstructed from Redis payload on agent side."""
```

#### Report Renderer Tests

```python
class TestReportRenderer:
    def test_timeline_view_ordered_by_start_time(self):
        """Timeline view shows spans in chronological order."""

    def test_hierarchy_view_shows_tree_structure(self):
        """Hierarchy view renders parent-child relationships as indented tree."""

    def test_cost_summary_view_aggregates_tokens(self):
        """Cost view sums tokens and costs across GENERATION spans."""

    def test_empty_trace_produces_valid_report(self):
        """Report for trace with no spans doesn't crash."""

    def test_report_with_error_spans_highlights_failures(self):
        """Error spans are visually marked in all views."""

    def test_markdown_output_is_valid(self):
        """Generated markdown parses without errors."""
```

### 2.2 Integration Tests

Located in `controller/tests/integration/test_tracing_integration.py`.

```python
class TestTracingIntegration:
    @pytest.fixture
    def wired_orchestrator(self):
        """Orchestrator with real TraceContext, SQLiteTraceBackend, mock Redis/K8s."""

    async def test_full_orchestrator_flow_produces_trace_hierarchy(self, wired_orchestrator):
        """
        Complete _spawn_job produces:
        - Root span: orchestrate
          - Child: classify_task
          - Child: inject_skills
          - Child: resolve_agent_type
          - Child: spawn_k8s_job
          - Child: monitor_result
        All share the same trace_id. Parent-child links are correct.
        """

    async def test_trace_id_propagated_to_redis_payload(self, wired_orchestrator):
        """Redis payload includes trace_id and parent_span_id from orchestrator."""

    async def test_agent_trace_events_linked_to_orchestrator_trace(self):
        """
        Simulate agent writing trace events to Redis stream.
        Monitor collects them. Verify they share trace_id with orchestrator spans.
        """

    async def test_report_generation_from_end_to_end_trace(self, wired_orchestrator):
        """
        Run full flow -> generate report -> verify report contains:
        - All operations in timeline
        - Correct hierarchy nesting
        - Token/cost summary for GENERATION spans
        """

    async def test_cross_process_trace_propagation(self):
        """
        1. Orchestrator creates trace context
        2. Serializes to Redis payload (trace_id, parent_span_id)
        3. Agent-side deserializes and creates child spans
        4. Agent publishes trace events to Redis stream (traces:{thread_id})
        5. Monitor collects and stores in TraceStore
        6. Query by trace_id returns both orchestrator and agent spans
        """

    async def test_concurrent_jobs_have_independent_traces(self):
        """
        Two concurrent _spawn_job calls produce independent trace_ids.
        Spans from job A do not appear in job B's trace.
        """

    async def test_retry_job_creates_new_trace_linked_to_original(self):
        """
        Retry inherits original thread_id but creates new trace_id.
        Both traces are queryable by thread_id.
        """
```

### 2.3 Performance Tests

Located in `controller/tests/perf/test_tracing_performance.py`.

```python
class TestTracingPerformance:
    async def test_tracing_overhead_on_hot_path(self):
        """
        Measure overhead of TraceContext enter/exit + span creation
        on the orchestrator hot path.

        Acceptance criteria: < 5ms per span creation + context switch.

        Method:
        - Run _spawn_job with tracing enabled, measure wall time
        - Run _spawn_job with tracing disabled, measure wall time
        - Delta must be < 5ms per instrumented operation
        """

    async def test_sqlite_write_throughput(self, tmp_path):
        """
        Measure write throughput under concurrent load.

        Acceptance criteria: > 1000 spans/second with batch_size=50.

        Method:
        - 10 concurrent writers, each writing 100 spans in batches of 50
        - Total: 1000 spans
        - Must complete in < 1 second
        """

    async def test_report_generation_time_large_trace(self, tmp_path):
        """
        Generate report for trace with 100+ spans.

        Acceptance criteria: < 500ms for 100 spans, < 2s for 500 spans.

        Method:
        - Seed SQLite with synthetic trace (100 spans, 10 GENERATION, 90 SPAN)
        - Generate all three report views
        - Measure wall time
        """

    async def test_memory_impact_of_trace_buffering(self):
        """
        Measure memory delta when trace buffer holds max batch_size spans.

        Acceptance criteria: < 5MB for 50-span buffer.

        Method:
        - Measure process RSS before enabling tracing
        - Create 50 spans in buffer (don't flush)
        - Measure process RSS after
        - Delta must be < 5MB
        """

    async def test_sqlite_db_size_growth(self, tmp_path):
        """
        Estimate DB size growth rate for capacity planning.

        Method:
        - Write 10,000 representative spans
        - Measure DB file size
        - Extrapolate: if 100 jobs/day * 10 spans/job = 1000 spans/day
        - Estimate days until 1GB threshold
        """
```

### 2.4 Failure / Resilience Tests

Located in `controller/tests/test_tracing_resilience.py`.

```python
class TestTracingResilience:
    async def test_trace_store_write_failure_does_not_block_orchestrator(self):
        """
        If SQLite write fails (e.g., disk full), orchestrator continues.
        Error is logged but job proceeds normally.

        Method:
        - Use a TraceBackend that raises IOError on write
        - Run _spawn_job
        - Assert job completes successfully
        - Assert error logged with WARNING level
        """

    async def test_corrupted_agent_trace_events_handled_gracefully(self):
        """
        Agent publishes malformed JSON to Redis traces stream.
        Monitor skips the event and logs a warning.

        Method:
        - Push invalid JSON to traces:{thread_id}
        - Push valid trace event after it
        - Monitor collects both
        - Assert valid event stored, invalid event skipped
        - Assert warning logged for invalid event
        """

    async def test_sqlite_db_locked_retry_behavior(self):
        """
        When SQLite returns SQLITE_BUSY, writer retries with backoff.

        Method:
        - Acquire exclusive lock on SQLite DB from another connection
        - Attempt write from TraceStore
        - Assert retry happens (up to 3 attempts)
        - Release lock
        - Assert write succeeds on retry
        """

    async def test_missing_trace_id_in_redis_payload_fallback(self):
        """
        If Redis payload lacks trace_id (e.g., old controller version),
        agent generates a new trace_id and logs a warning.

        Method:
        - Create Redis payload without trace_id/parent_span_id
        - Simulate agent entrypoint reading it
        - Assert agent creates new trace_id
        - Assert warning logged
        """

    async def test_trace_context_survives_exception_in_operation(self):
        """
        If an instrumented operation raises an exception,
        the span is closed with status="error" and context is cleaned up.

        Method:
        - Enter TraceContext
        - Raise RuntimeError inside context
        - Assert span has status="error" and error_type="RuntimeError"
        - Assert context is properly exited (no leak)
        """

    async def test_tracing_disabled_via_env_var(self):
        """
        When TRACING_ENABLED=false, no spans are created or stored.
        TraceContext operations are no-ops.
        Orchestrator runs without any tracing overhead.
        """

    async def test_flush_failure_preserves_buffer(self):
        """
        If batch flush to SQLite fails, buffer is preserved
        and retried on next flush interval.
        """
```

---

## 3. Rollout Plan

### 3.1 Phase A: Shadow Mode (Week 1)

**Goal:** Validate tracing works without impacting production behavior.

| Task | Description | Owner | Done When |
|------|-------------|-------|-----------|
| Deploy with `TRACING_ENABLED=true` | Tracing runs but produces no user-visible output | Backend | Deployed, no errors in logs |
| Monitor DB size growth | Check `trace_events.db` size daily | Ops | Growth rate documented |
| Monitor write latency | Log `flush_latency_ms` metric | Backend | p99 < 50ms |
| Monitor error rate | Count `spans_failed` in logs | Backend | < 0.1% failure rate |
| Validate trace completeness | For 10 jobs, verify all expected spans present | Backend | 10/10 jobs have complete traces |
| Validate span hierarchy | For 10 jobs, verify parent-child links are correct | Backend | No orphaned spans |
| Load test | Run 50 concurrent jobs, verify no degradation | QA | Orchestrator latency unchanged |

**Rollback:** Set `TRACING_ENABLED=false`. No data loss — SQLite DB retained for analysis.

### 3.2 Phase B: Internal Review (Week 2)

**Goal:** Validate trace reports are useful for debugging.

| Task | Description | Owner | Done When |
|------|-------------|-------|-----------|
| Enable report CLI | `ditto trace report <job_id>` produces readable output | Backend | CLI works for all 3 views |
| Team reviews 5-10 real traces | Each team member reviews 2 traces for readability | Team | Feedback collected |
| Iterate on report format | Adjust timeline, hierarchy, cost views based on feedback | Backend | Team approves format |
| Fix data quality issues | Address missing fields, incorrect nesting, truncation problems | Backend | All issues resolved |
| Document SQL queries | Write 5 most useful ad-hoc queries for the team | Backend | Queries documented |

**Rollback:** Disable report generation only. Tracing continues silently.

### 3.3 Phase C: Integration (Week 3)

**Goal:** Surface traces to users through existing channels.

| Task | Description | Owner | Done When |
|------|-------------|-------|-----------|
| Auto-attach reports to Job records | `TRACE_AUTO_REPORT=true` generates report on job completion | Backend | Reports visible in Job detail |
| Slack notifications include trace summary | One-line summary: "7 operations, 2 tool calls, 2,300 tokens, $0.04" | Backend | Summary in Slack messages |
| GitHub PR comments include trace link | If report is served via API, link in PR comment | Backend | Link works |
| Enable API endpoints | `GET /api/traces/{trace_id}`, `GET /api/jobs/{job_id}/trace` | Backend | Endpoints return data |
| CLI commands documented | `ditto trace list`, `ditto trace report`, `ditto trace export` | Backend | Help text and examples |

**Rollback:** Disable auto-report and API endpoints independently via feature flags.

### 3.4 Phase D: Monitoring & Steady State (Ongoing)

**Goal:** Ensure tracing remains healthy as usage grows.

| Metric | Threshold | Action |
|--------|-----------|--------|
| `trace_events.db` size | > 500MB | Alert, review retention settings |
| `trace_events.db` size | > 1GB | Alert, force cleanup, consider archival |
| `spans_failed` rate | > 1% of total | Alert, investigate write failures |
| `flush_latency_ms` p99 | > 100ms | Alert, investigate SQLite contention |
| Trace completeness | < 95% of jobs | Alert, investigate missing instrumentation |
| Retention cron | Not run in 48h | Alert, check cron job health |

---

## 4. Feature Flags & Configuration

All configuration via environment variables with sensible defaults:

```python
class TracingSettings(BaseSettings):
    """Tracing configuration. All env vars prefixed with TRACE_."""

    # Core
    TRACING_ENABLED: bool = True
    TRACE_DB_PATH: str = "./data/trace_events.db"

    # Batching
    TRACE_BATCH_SIZE: int = 50
    TRACE_FLUSH_INTERVAL_SECONDS: float = 5.0

    # Retention
    TRACE_RETENTION_DAYS: int = 30
    TRACE_CLEANUP_INTERVAL_HOURS: int = 24

    # Reporting
    TRACE_AUTO_REPORT: bool = True
    TRACE_REPORT_FORMAT: str = "markdown"  # "markdown" | "json"

    # Future migration
    TRACE_BACKEND: str = "sqlite"  # "sqlite" | "langfuse" | "otel"
    LANGFUSE_PUBLIC_KEY: str | None = None
    LANGFUSE_SECRET_KEY: str | None = None
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    # Safety
    TRACE_MAX_INPUT_LENGTH: int = 2000    # Truncate input_summary
    TRACE_MAX_OUTPUT_LENGTH: int = 2000   # Truncate output_summary
    TRACE_MAX_TOOL_RESULT_LENGTH: int = 5000  # Truncate tool_result
```

### Configuration Precedence

1. Environment variables (highest priority)
2. `.env` file in project root
3. Default values in `TracingSettings` (lowest priority)

### Feature Flag Matrix

| Flag | Shadow Mode | Internal Review | Full Integration |
|------|-------------|-----------------|------------------|
| `TRACING_ENABLED` | `true` | `true` | `true` |
| `TRACE_AUTO_REPORT` | `false` | `false` | `true` |
| API endpoints | disabled | disabled | enabled |
| CLI commands | disabled | enabled | enabled |
| Slack integration | disabled | disabled | enabled |

---

## 5. Observability of Observability

### 5.1 Metrics to Emit

Track these via structured logging (and eventually Prometheus/StatsD):

```python
# Counters
trace.spans_written_total        # Successfully persisted spans
trace.spans_failed_total         # Failed span writes (by error_type)
trace.flushes_total              # Number of batch flushes
trace.reports_generated_total    # Number of reports generated

# Gauges
trace.db_size_bytes              # Current SQLite DB file size
trace.buffer_size                # Current number of spans in write buffer

# Histograms
trace.flush_latency_ms           # Time to flush a batch to SQLite
trace.span_creation_latency_us   # Time to create a TraceSpan object
trace.report_generation_ms       # Time to generate a report
```

### 5.2 Log Warnings

Emit structured log warnings (not errors, since tracing is non-blocking) for:

| Condition | Log Level | Message |
|-----------|-----------|---------|
| Batch flush failure | WARNING | `trace.flush_failed: {error}, batch_size={n}, will_retry={bool}` |
| DB size > 500MB | WARNING | `trace.db_large: size_mb={size}, retention_days={days}` |
| Span without parent (non-root) | WARNING | `trace.orphan_span: span_id={id}, operation={name}` |
| Trace missing expected spans | WARNING | `trace.incomplete: trace_id={id}, expected={n}, actual={m}` |
| Flush latency > 100ms | WARNING | `trace.slow_flush: latency_ms={ms}, batch_size={n}` |
| Buffer full (> 2x batch_size) | WARNING | `trace.buffer_full: size={n}, batch_size={max}` |

### 5.3 Health Check Endpoint

Add tracing health to the existing `/health` endpoint:

```json
{
  "status": "healthy",
  "components": {
    "tracing": {
      "status": "healthy",
      "db_size_mb": 42.5,
      "spans_last_hour": 350,
      "last_flush_at": "2026-03-21T14:30:00Z",
      "flush_latency_p99_ms": 12.3,
      "error_rate_pct": 0.0
    }
  }
}
```

---

## 6. Documentation

### 6.1 Documents to Write

| Document | Audience | Location | Content |
|----------|----------|----------|---------|
| Tracing Architecture | Engineers | `docs/architecture-tracing.md` | Data model, flow diagrams, design decisions |
| Adding Instrumentation | Engineers | `docs/guides/adding-tracing.md` | How to add a new traced operation (with code examples) |
| Querying Traces | Engineers + Ops | `docs/guides/querying-traces.md` | SQL examples, CLI commands, API endpoints |
| Running Reports | All | `docs/guides/trace-reports.md` | CLI usage, report formats, interpreting output |
| Troubleshooting | Ops | `docs/guides/tracing-troubleshooting.md` | Common issues and fixes |
| Migration Guide | Engineers | `docs/guides/tracing-migration.md` | Steps to migrate to Langfuse or OTel |

### 6.2 SQL Query Cookbook

Include in `docs/guides/querying-traces.md`:

```sql
-- 1. Get full trace for a job
SELECT * FROM trace_spans
WHERE job_id = 'job-abc123'
ORDER BY started_at;

-- 2. Find slow operations (>5s)
SELECT operation_name, thread_id,
       (julianday(ended_at) - julianday(started_at)) * 86400 as duration_s
FROM trace_spans
WHERE duration_s > 5
ORDER BY duration_s DESC;

-- 3. Token usage by model (last 7 days)
SELECT model,
       COUNT(*) as call_count,
       SUM(tokens_input) as total_input,
       SUM(tokens_output) as total_output,
       SUM(cost_usd) as total_cost
FROM trace_spans
WHERE observation_type = 'GENERATION'
  AND started_at > datetime('now', '-7 days')
GROUP BY model;

-- 4. Error rate by operation (last 24h)
SELECT operation_name,
       COUNT(*) as total,
       SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors,
       ROUND(100.0 * SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) / COUNT(*), 1) as error_pct
FROM trace_spans
WHERE started_at > datetime('now', '-1 day')
GROUP BY operation_name
ORDER BY error_pct DESC;

-- 5. Average span count per job (trace completeness)
SELECT AVG(span_count) as avg_spans_per_job,
       MIN(span_count) as min_spans,
       MAX(span_count) as max_spans
FROM (
    SELECT job_id, COUNT(*) as span_count
    FROM trace_spans
    WHERE job_id IS NOT NULL
    GROUP BY job_id
);

-- 6. DB size and row count
SELECT COUNT(*) as total_spans,
       MIN(started_at) as oldest_span,
       MAX(started_at) as newest_span
FROM trace_spans;

-- 7. Orphaned spans (have parent_span_id but parent not found)
SELECT child.span_id, child.operation_name, child.parent_span_id
FROM trace_spans child
LEFT JOIN trace_spans parent ON child.parent_span_id = parent.span_id
WHERE child.parent_span_id IS NOT NULL
  AND parent.span_id IS NULL;
```

### 6.3 Adding New Instrumentation Points

Include in `docs/guides/adding-tracing.md`:

```python
# How to instrument a new operation:

from controller.tracing import TraceContext, ObservationType

async def my_new_operation(self, task):
    # 1. Create a child span context
    with TraceContext.child(
        operation="my_new_operation",
        observation_type=ObservationType.SPAN,
    ) as span:
        # 2. Set input summary
        span.set_input(f"Processing task: {task.id}")

        try:
            # 3. Do the work
            result = await self._do_work(task)

            # 4. Set output summary
            span.set_output(f"Result: {result.status}")
            return result

        except Exception as e:
            # 5. Record error (span auto-closes with error status)
            span.set_error(e)
            raise

# For LLM calls, use GENERATION type:
with TraceContext.child(
    operation="claude_call",
    observation_type=ObservationType.GENERATION,
) as span:
    response = await client.messages.create(model="claude-opus-4-6", ...)
    span.set_generation_metadata(
        model="claude-opus-4-6",
        tokens_input=response.usage.input_tokens,
        tokens_output=response.usage.output_tokens,
        cost_usd=compute_cost(response),
    )
```

### 6.4 Troubleshooting Guide

Include in `docs/guides/tracing-troubleshooting.md`:

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| No traces in DB | `TRACING_ENABLED=false` or DB path wrong | Check env vars, verify `TRACE_DB_PATH` is writable |
| Missing agent-side spans | Agent not publishing to Redis stream | Check agent entrypoint reads `trace_id` from payload |
| Orphaned spans | Parent span completed before child started | Check `parent_span_id` is set correctly in `TraceContext.child()` |
| DB growing too fast | Retention cleanup not running | Verify cron job, check `TRACE_RETENTION_DAYS` |
| Slow flush warnings | SQLite contention from concurrent writes | Increase `TRACE_BATCH_SIZE`, enable WAL mode |
| Reports show wrong hierarchy | `parent_span_id` linking incorrect | Verify `TraceContext` nesting matches operation nesting |
| Duplicate spans | Retry logic creating spans on each attempt | Ensure span creation is inside the retry loop, not outside |

---

## 7. ADR: Adopt Option 4 (B Enhanced) with Migration-Ready Schema

### Status
Accepted

### Context
We need traceability for debugging agent runs, understanding costs, and improving reliability. Four approaches were evaluated: A (OTel), B (structured logs + SQLite), C (event sourcing), D (Langfuse). The team needs something deployable in 7-9 days with zero infrastructure dependencies, but must not paint itself into a corner.

### Decision
Adopt Option 4 (B Enhanced): structured trace spans stored in SQLite, with schema decisions that make migration to Langfuse (month 3-6) or OTel (when needed) mechanical rather than architectural.

Key schema decisions:
1. W3C trace ID format (128-bit trace_id, 64-bit span_id)
2. Langfuse-compatible observation types (GENERATION, SPAN, EVENT)
3. First-class cost/token fields (not buried in attributes)
4. `TraceBackend` protocol for backend swappability
5. Formal event taxonomy from Approach C

### Consequences
**Easier:**
- Zero infrastructure to deploy — just SQLite
- Migration to Langfuse is 2-5 days of SDK wrapping
- Migration to OTel is 3-7 days of exporter swapping
- Schema is self-documenting via observation types

**Harder:**
- Must maintain custom report generator (Langfuse gives this for free)
- SQLite doesn't scale past single-node (fine for current architecture)
- Must manually compute costs (Langfuse auto-computes)
- Team needs SQL literacy for ad-hoc queries (mitigated by query cookbook)
