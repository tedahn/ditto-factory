# Option 4: B Enhanced — Master Implementation Plan

**Date:** 2026-03-21
**Decision:** Structured Logs + SQLite with formal event types (from C), LLM cost fields (from D), OTel-compatible IDs (from A)
**Verdict:** Unanimous across PM, Trend Researcher, Software Architect, and DevOps Engineer

---

## Executive Summary

Add full traceability to Ditto Factory so engineers can review **everything** an agent did — what skills were selected and why, what the agent executed, and what the outcome was. Compile traces into readable Markdown reports. Design for future migration to Langfuse (month 3-6) or OTel (when multi-service).

**Total estimated effort: 9-11 days**
**New dependencies: zero**
**New infrastructure: zero**

---

## Phase Overview

| Phase | Scope | Effort | Depends On | Output File |
|-------|-------|--------|------------|-------------|
| **1** | Trace Data Model & Storage | ~9 hours | — | `option4-phase1-data-model.md` |
| **2** | Orchestrator Instrumentation | ~10 hours | Phase 1 | `option4-phase2-orchestrator-instrumentation.md` |
| **3** | Agent-Side Tracing | ~8 hours | Phase 1 | `option4-phase3-agent-side-tracing.md` |
| **4** | Report Generator | ~3-4 days | Phases 1-3 | `option4-phase4-report-generator.md` |
| **5** | Migration, Testing & Rollout | ~2 days | Phases 1-4 | `option4-phase5-migration-testing-rollout.md` |

```
Phase 1 ──┬──→ Phase 2 ──┐
           │               ├──→ Phase 4 ──→ Phase 5
           └──→ Phase 3 ──┘
```

**Phases 2 and 3 can run in parallel** after Phase 1 completes.

---

## What Gets Built

### Phase 1: Foundation (~9 hours)

**New files:**
- `src/tracing/__init__.py` — package init
- `src/tracing/models.py` — TraceSpan dataclass (22 fields), TraceEventType enum (10 types)
- `src/tracing/store.py` — TraceStore class (batched SQLite writes, async queries, retention cleanup)
- `src/tracing/context.py` — TraceContext using contextvars, `trace_span()` context manager

**Key design decisions:**
- W3C trace ID format (32-char hex) + span ID (16-char hex) for OTel compatibility
- Separate `trace_events.db` file (high-frequency appends isolated from CRUD tables)
- WAL mode SQLite with 6 targeted indexes
- Batched writes: 50 spans or 5-second flush, thread-safe via asyncio.Lock
- 5 new config settings: `tracing_enabled`, `trace_db_path`, `trace_retention_days`, `trace_batch_size`, `trace_flush_interval`

### Phase 2: Orchestrator Instrumentation (~10 hours)

**9 instrumentation points:**
1. TASK_RECEIVED — webhook/API entry
2. TASK_CLASSIFIED — skill matching scores, method, fallback reason, boost/penalty
3. SKILLS_INJECTED — which skills, budget used, truncation
4. REDIS_PUSH — payload size, trace context propagation
5. AGENT_SPAWNED — image, job name, resources
6. JOB_MONITORED — poll count, timeout, result
7. SAFETY_CHECK — PR created?, retry?, report sent?
8. PERFORMANCE_TRACKED — skill outcomes
9. ERROR — any exception in the flow

**Key design decisions:**
- `@traced("operation_name")` decorator for sync/async functions
- `ClassificationDiagnostics` dataclass captures all candidate scores (THE most important data)
- Gated behind `settings.tracing_enabled` — zero cost when disabled
- `_emit_span()` wraps all writes in try/except — tracing never blocks execution
- `trace_id` + `parent_span_id` added to Redis task payload for cross-process propagation

### Phase 3: Agent-Side Tracing (~8 hours)

**What we CAN capture:**
- Pre/post execution timing
- Skills written to disk
- Git activity (files changed, diff stats, commit messages)
- Exit code, stderr, stdout
- Total duration

**What we CANNOT capture (honest):**
- Claude's internal reasoning (CLI black box)
- Individual tool invocations within Claude
- Token usage (not reported by CLI)

**Key design decisions:**
- Trace events accumulated in bash variable, written atomically with result
- `safe_trace()` wrapper — tracing errors swallowed via `2>/dev/null || true`
- No Docker image changes needed (redis-tools, jq, date already available)
- Stdout parsing for tool markers is opt-in (fragile, gated behind env var)
- Enhanced result payload includes `trace_events` array

### Phase 4: Report Generator (~3-4 days)

**Three report views:**
1. **Hierarchical** (default) — tree showing orchestrator → agent → tool calls
2. **Timeline** — chronological table with timestamps and durations
3. **Decision Summary** — focused on WHY decisions were made (classification scores, skill selection reasoning)

**Interfaces:**
- **CLI:** `python -m controller.tracing report <trace_id>`, `list`, `search`
- **API:** `GET /api/traces`, `GET /api/traces/{id}/report?view=hierarchical`
- **Auto-report:** generated after SafetyPipeline, attached to Job record, included in notifications

**Key design decisions:**
- Programmatic string building over Jinja2 (ADR documented)
- `TraceQueryEngine` data layer — renderers never touch SQL
- ANSI color coding in terminal, ASCII bar charts for scores
- Stderr truncation with "see full at..." links

### Phase 5: Migration, Testing & Rollout (~2 days)

**Migration paths designed in:**
- `TraceBackend` protocol enables backend swapping without changing instrumentation
- `ObservationType` enum (GENERATION/SPAN/EVENT) matches Langfuse model
- W3C trace IDs ensure both Langfuse and OTel migrations are mechanical

**Rollout plan:**
1. Week 1: Shadow mode (traces written but not surfaced)
2. Week 2: Internal review (team reviews 5-10 real traces, iterate on format)
3. Week 3: Integration (auto-reports, CLI, API enabled)
4. Ongoing: Monitoring (DB size alerts, trace completeness metrics)

**Test strategy:**
- Unit: TraceSpan, TraceStore, TraceContext, renderers
- Integration: full orchestrator flow → trace hierarchy → report
- Performance: <5ms overhead on hot path, write throughput under load
- Resilience: tracing failure never blocks job execution

---

## Implementation Order (Recommended)

```
Day 1-2:   Phase 1 — Data model, SQLite schema, TraceStore, TraceContext
Day 2-3:   Phase 2 — Orchestrator instrumentation (can start late Day 1)
Day 2-3:   Phase 3 — Agent-side tracing (parallel with Phase 2)
Day 4-7:   Phase 4 — Report generator (all three views, CLI, API)
Day 8-9:   Phase 5 — Integration tests, shadow mode deployment, rollout
Day 10-11: Buffer — iteration based on reviewing real traces
```

---

## Files Inventory

### New Files (Phase 1-4)
```
src/tracing/
├── __init__.py          # Package init, re-exports
├── models.py            # TraceSpan, TraceEventType, ClassificationDiagnostics
├── store.py             # TraceStore (SQLite, batched writes)
├── context.py           # TraceContext (contextvars, trace_span context manager)
├── decorators.py        # @traced decorator
├── report_renderer.py   # Hierarchical, Timeline, Decision renderers
├── query_engine.py      # TraceQueryEngine (SQL → typed dataclasses)
├── cli.py               # CLI commands (report, list, search)
└── api.py               # FastAPI endpoints (/api/traces)

tests/tracing/
├── test_models.py
├── test_store.py
├── test_context.py
├── test_report_renderer.py
├── test_query_engine.py
├── test_cli.py
├── test_api.py
└── test_integration.py
```

### Modified Files
```
src/orchestrator.py      # Add trace spans to _spawn_job flow
src/skill_registry/
  classifier.py          # Add ClassificationDiagnostics to result
  injector.py            # Emit SKILLS_INJECTED span
src/spawner.py           # Emit AGENT_SPAWNED span
src/monitor.py           # Collect agent trace events from result
src/safety.py            # Emit SAFETY_CHECK span, trigger auto-report
src/config.py            # Add tracing config settings
src/main.py              # Initialize TraceStore on startup
agent/entrypoint.sh      # Add trace event collection
```

---

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `TRACING_ENABLED` | `true` | Master switch |
| `TRACE_DB_PATH` | `./data/trace_events.db` | SQLite file location |
| `TRACE_RETENTION_DAYS` | `30` | Auto-cleanup after N days |
| `TRACE_BATCH_SIZE` | `50` | Flush after N spans |
| `TRACE_FLUSH_INTERVAL` | `5` | Flush every N seconds |
| `TRACE_AUTO_REPORT` | `true` | Generate report on job completion |
| `TRACE_STDOUT_PARSING` | `false` | Opt-in fragile stdout parsing |

---

## Risk Mitigations

| Risk | Mitigation |
|------|-----------|
| Tracing breaks agent execution | All trace writes wrapped in try/except, never raise |
| SQLite DB grows unbounded | Retention cleanup cron, configurable TTL |
| Performance overhead | Batched writes, gated behind config flag, <5ms on hot path |
| Agent-side tracing fragile | Atomic writes, safe_trace() wrapper, opt-in stdout parsing |
| Migration to Langfuse painful | W3C IDs, ObservationType enum, TraceBackend protocol |
| Schema changes break queries | Versioned schema with migration support |

---

## Success Criteria

1. Every agent run produces a complete trace viewable as a Markdown report
2. Classification decisions show all candidate scores and selection reasoning
3. Tracing adds <5ms to the orchestration hot path
4. A tracing failure NEVER blocks agent execution
5. An engineer can answer "why did it pick these skills?" by reading the report
6. Migration to Langfuse takes <5 days when the time comes

---

## All Documents Produced

### Architecture Plans (4 approaches evaluated)
- `docs/plans/tracing-approach-a-otel-native.md`
- `docs/plans/tracing-approach-b-structured-logs.md`
- `docs/plans/tracing-approach-c-event-sourcing.md`
- `docs/plans/tracing-approach-d-langfuse.md`

### Cross-Examination Reviews (4 perspectives)
- `docs/reviews/2026-03-21-pm-cross-examination.md`
- `docs/reviews/2026-03-21-trend-analysis-llm-observability.md`
- `docs/reviews/2026-03-21-architect-cross-examination.md`
- `docs/reviews/2026-03-21-devops-ops-assessment.md`

### Analysis & Comparison
- `docs/reviews/2026-03-21-traceability-analysis.md`
- `docs/reviews/2026-03-21-final-comparison-matrix.md`

### Implementation Plans (Option 4: B Enhanced)
- `docs/plans/option4-phase1-data-model.md`
- `docs/plans/option4-phase2-orchestrator-instrumentation.md`
- `docs/plans/option4-phase3-agent-side-tracing.md`
- `docs/plans/option4-phase4-report-generator.md`
- `docs/plans/option4-phase5-migration-testing-rollout.md`
- `docs/plans/option4-master-implementation-plan.md` ← this document
