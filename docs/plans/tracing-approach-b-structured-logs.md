# Approach B: Lightweight Structured Logging with SQLite

**Status:** Proposed
**Date:** 2026-03-21
**Author:** Software Architect

---

## 1. Architecture Overview

This approach adds full request-lifecycle tracing to Ditto Factory using **nothing but Python's `logging` module, structured JSON events, and a SQLite table**. No OpenTelemetry SDK. No Jaeger. No collector sidecars. The entire tracing system is a single Python module (~400 LOC) and a single SQLite table.

### Design Philosophy

The best observability system is the one the team actually uses. OTel is powerful but carries real costs: SDK dependency sprawl (`opentelemetry-sdk`, `opentelemetry-api`, `opentelemetry-exporter-*`, `opentelemetry-instrumentation-*`), a collector to deploy and monitor, and a visualization backend (Jaeger/Tempo) to operate. For a system with a single orchestration process and ephemeral K8s pods that communicate through Redis, this is overhead without proportional value.

Structured logs give us 80% of what distributed tracing provides — correlated events across a request lifecycle, timing data, decision context — at 5% of the operational cost.

### Data Flow Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                    Controller Process                         │
│                                                              │
│  Webhook ──► Orchestrator ──► TaskClassifier ──► SkillInjector│
│     │            │                  │                  │      │
│     │  trace_id  │     trace_id     │    trace_id      │      │
│     ▼            ▼                  ▼                  ▼      │
│  ┌─────────────────────────────────────────────────────┐     │
│  │         StructuredTraceHandler (logging.Handler)     │     │
│  │         ─ buffers events in memory (batch_size=50)   │     │
│  │         ─ flushes to SQLite on batch/timer/shutdown  │     │
│  └────────────────────┬────────────────────────────────┘     │
│                       │                                      │
│                       ▼                                      │
│              ┌────────────────┐                              │
│              │  trace_events   │  (SQLite table)             │
│              │  table          │                              │
│              └────────────────┘                              │
│                       │                                      │
│    JobSpawner ────────┤  trace_id passed via Redis           │
│                       │  alongside task payload              │
└───────────────────────┼──────────────────────────────────────┘
                        │
          ┌─────────────┼─────────────────┐
          │   Redis     │                 │
          │  push_task({│                 │
          │    ...,     │                 │
          │    trace_id │                 │
          │  })         │                 │
          └─────────────┼─────────────────┘
                        │
┌───────────────────────┼──────────────────────────────────────┐
│  Agent Pod (K8s)      │                                      │
│                       ▼                                      │
│  Agent reads trace_id from Redis                             │
│  Agent writes trace events to Redis:                         │
│    result:{thread_id} = {                                    │
│      branch, exit_code, commit_count, stderr,                │
│      trace_events: [...]   ← NEW: array of structured events │
│    }                                                         │
└──────────────────────────────────────────────────────────────┘
                        │
┌───────────────────────┼──────────────────────────────────────┐
│  Controller (on job completion)                              │
│                       ▼                                      │
│  JobMonitor reads result from Redis                          │
│  Orchestrator.handle_job_completion() ingests trace_events   │
│  from result payload into local SQLite trace_events table    │
│                                                              │
│  ReportGenerator queries SQLite → renders Markdown report    │
└──────────────────────────────────────────────────────────────┘
```

### Why Not OTel?

| Concern | OTel (Approach A) | Structured Logs (This Approach) |
|---------|------------------|-------------------------------|
| Dependencies | ~8 new packages | 0 (stdlib `logging` + existing `aiosqlite`) |
| Infrastructure | Collector + Jaeger/Tempo | Nothing new |
| Cross-process correlation | Automatic via context propagation | Manual via Redis payload (simple) |
| Visualization | Jaeger flame graphs | Markdown reports (good enough for review) |
| Learning curve | OTel concepts (spans, context, baggage) | JSON + SQL (team already knows both) |
| Lock-in risk | Medium (OTel is a standard, but exporters vary) | Zero |

---

## 2. Log Event Schema

Every trace event is a flat JSON object. No nested spans, no parent-child trees. Events are correlated by `trace_id` and ordered by `timestamp`.

### Core Fields (every event)

```json
{
  "trace_id": "abc123def456",
  "thread_id": "th_8f3a2b1c",
  "event": "skill.classification.completed",
  "component": "task_classifier",
  "timestamp": "2026-03-21T14:32:01.234Z",
  "duration_ms": 45,
  "level": "INFO",
  "data": {}
}
```

| Field | Type | Description |
|-------|------|-------------|
| `trace_id` | `str` | UUID generated at request entry, correlates all events in one task lifecycle |
| `thread_id` | `str` | Ditto Factory thread ID (may span multiple traces if retried) |
| `event` | `str` | Dot-notation event name (see taxonomy below) |
| `component` | `str` | Which module emitted this: `orchestrator`, `classifier`, `injector`, `spawner`, `monitor`, `agent`, `safety` |
| `timestamp` | `str` | ISO 8601 with milliseconds, UTC |
| `duration_ms` | `int?` | How long this step took (null for point-in-time events) |
| `level` | `str` | Standard log level: DEBUG, INFO, WARNING, ERROR |
| `data` | `dict` | Event-specific payload (see below) |

### Event Taxonomy

```
orchestrator.task.received          # New task arrives
orchestrator.thread.created         # New thread created
orchestrator.thread.locked          # Advisory lock acquired
orchestrator.task.queued            # Task queued (active job exists)

classifier.search.started           # Classification begins
classifier.embedding.cached         # Cache hit for task embedding
classifier.embedding.computed        # Cache miss, called Voyage-3
classifier.embedding.failed          # Embedding API error
classifier.tag_fallback.used         # Fell back to tag-based search
classifier.skills.matched           # Skills matched (data: {skills, scores, method})
classifier.budget.applied            # Budget/limit applied

injector.skills.formatted           # Skills formatted for Redis
injector.prompt.built               # System prompt constructed

spawner.job.created                 # K8s job created (data: {job_name, image, thread_id})
spawner.redis.task_pushed           # Task payload pushed to Redis

agent.started                       # Agent pod started (from pod)
agent.tool.invoked                  # Claude tool call (data: {tool, args_summary})
agent.reasoning.step                # High-level reasoning step
agent.commit.created                # Git commit made
agent.error                         # Agent-side error
agent.completed                     # Agent finished

monitor.poll.started                # Polling for result
monitor.result.received             # Result received from Redis

safety.pipeline.started             # Safety checks begin
safety.check.passed                 # Individual check passed
safety.check.failed                 # Individual check failed

tracker.injection.recorded          # Skill injection tracked
tracker.outcome.recorded            # Job outcome recorded
tracker.boost.applied               # Performance boost applied to scores

report.generated                    # Report rendered
```

### Example Event with Data

```json
{
  "trace_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "thread_id": "th_8f3a2b1c",
  "event": "classifier.skills.matched",
  "component": "task_classifier",
  "timestamp": "2026-03-21T14:32:01.279Z",
  "duration_ms": 45,
  "level": "INFO",
  "data": {
    "method": "semantic",
    "task_preview": "Add retry logic to the payment...",
    "skills_matched": [
      {"slug": "error-handling", "score": 0.87},
      {"slug": "async-patterns", "score": 0.72}
    ],
    "skills_rejected": [
      {"slug": "database-migration", "score": 0.31, "reason": "below_threshold"}
    ],
    "threshold": 0.5,
    "embedding_cached": true,
    "language_filter": ["python"],
    "total_candidates": 14
  }
}
```

---

## 3. Instrumentation Strategy

### 3.1 The Tracing Module

A single module provides the entire tracing API:

```python
# controller/tracing.py

from __future__ import annotations

import json
import logging
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

import aiosqlite

# ── Context propagation within the controller process ──
_current_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
_current_thread_id: ContextVar[str | None] = ContextVar("thread_id", default=None)

def new_trace(thread_id: str) -> str:
    """Start a new trace. Call at request entry point."""
    trace_id = uuid.uuid4().hex
    _current_trace_id.set(trace_id)
    _current_thread_id.set(thread_id)
    return trace_id

def get_trace_id() -> str | None:
    return _current_trace_id.get()

def get_thread_id() -> str | None:
    return _current_thread_id.get()


class TraceEvent:
    """A single structured trace event."""

    __slots__ = ("trace_id", "thread_id", "event", "component",
                 "timestamp", "duration_ms", "level", "data")

    def __init__(
        self,
        event: str,
        component: str,
        *,
        trace_id: str | None = None,
        thread_id: str | None = None,
        duration_ms: int | None = None,
        level: str = "INFO",
        data: dict[str, Any] | None = None,
    ):
        self.trace_id = trace_id or get_trace_id() or "unknown"
        self.thread_id = thread_id or get_thread_id() or "unknown"
        self.event = event
        self.component = component
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.duration_ms = duration_ms
        self.level = level
        self.data = data or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "thread_id": self.thread_id,
            "event": self.event,
            "component": self.component,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "level": self.level,
            "data": self.data,
        }


# ── Convenience emit function ──

_trace_logger = logging.getLogger("ditto.trace")

def emit(
    event: str,
    component: str,
    *,
    duration_ms: int | None = None,
    level: str = "INFO",
    data: dict[str, Any] | None = None,
    trace_id: str | None = None,
    thread_id: str | None = None,
) -> TraceEvent:
    """Emit a structured trace event.

    This both logs the event (for stdout/stderr capture) and returns
    the TraceEvent for direct storage if needed.
    """
    te = TraceEvent(
        event=event,
        component=component,
        trace_id=trace_id,
        thread_id=thread_id,
        duration_ms=duration_ms,
        level=level,
        data=data,
    )
    # Log as structured JSON on the dedicated trace logger
    _trace_logger.info(json.dumps(te.to_dict()))
    return te


class timed:
    """Context manager that measures duration and emits a trace event on exit.

    Usage:
        async with timed("classifier.search.started", "task_classifier") as t:
            results = await do_search()
            t.data = {"results": len(results)}
        # Automatically emits event with duration_ms on exit
    """

    def __init__(self, event: str, component: str, **kwargs: Any):
        self.event = event
        self.component = component
        self.kwargs = kwargs
        self.data: dict[str, Any] = {}
        self._start: float = 0

    async def __aenter__(self) -> timed:
        self._start = time.monotonic()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        duration_ms = int((time.monotonic() - self._start) * 1000)
        if exc_info[0] is not None:
            self.data["error"] = str(exc_info[1])
            self.kwargs["level"] = "ERROR"
        emit(
            self.event, self.component,
            duration_ms=duration_ms,
            data=self.data,
            **self.kwargs,
        )

    # Sync support
    def __enter__(self) -> timed:
        self._start = time.monotonic()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        duration_ms = int((time.monotonic() - self._start) * 1000)
        if exc_info[0] is not None:
            self.data["error"] = str(exc_info[1])
            self.kwargs["level"] = "ERROR"
        emit(
            self.event, self.component,
            duration_ms=duration_ms,
            data=self.data,
            **self.kwargs,
        )
```

### 3.2 SQLite Logging Handler

A custom `logging.Handler` that batches trace events and flushes them to SQLite:

```python
# controller/tracing_handler.py

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque
from typing import Any

import aiosqlite


class SQLiteTraceHandler(logging.Handler):
    """Logging handler that batches structured trace events into SQLite.

    Design choices:
    - Batches writes (default 50 events or 5 seconds) to avoid per-event I/O
    - Uses a background thread for flushing (non-blocking to async code)
    - WAL mode for concurrent reads during report generation
    - Graceful shutdown via flush_sync() called from atexit
    """

    def __init__(
        self,
        db_path: str,
        batch_size: int = 50,
        flush_interval_seconds: float = 5.0,
    ):
        super().__init__()
        self._db_path = db_path
        self._batch_size = batch_size
        self._flush_interval = flush_interval_seconds
        self._buffer: deque[dict[str, Any]] = deque()
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()

        # Start background flush timer
        self._timer: threading.Timer | None = None
        self._running = True
        self._schedule_flush()

    def emit(self, record: logging.LogRecord) -> None:
        """Buffer a trace event for batch insert."""
        try:
            event_data = json.loads(record.getMessage())
        except (json.JSONDecodeError, TypeError):
            return  # Not a structured trace event, skip

        with self._lock:
            self._buffer.append(event_data)
            if len(self._buffer) >= self._batch_size:
                self._flush_sync()

    def _schedule_flush(self) -> None:
        if not self._running:
            return
        self._timer = threading.Timer(self._flush_interval, self._timed_flush)
        self._timer.daemon = True
        self._timer.start()

    def _timed_flush(self) -> None:
        with self._lock:
            self._flush_sync()
        self._schedule_flush()

    def _flush_sync(self) -> None:
        """Synchronous flush — called from background thread."""
        if not self._buffer:
            return

        events = list(self._buffer)
        self._buffer.clear()

        import sqlite3
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executemany(
                """INSERT INTO trace_events
                   (trace_id, thread_id, event, component, timestamp,
                    duration_ms, level, data)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        e.get("trace_id"),
                        e.get("thread_id"),
                        e.get("event"),
                        e.get("component"),
                        e.get("timestamp"),
                        e.get("duration_ms"),
                        e.get("level"),
                        json.dumps(e.get("data", {})),
                    )
                    for e in events
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def close(self) -> None:
        """Flush remaining events and stop the timer."""
        self._running = False
        if self._timer:
            self._timer.cancel()
        with self._lock:
            self._flush_sync()
        super().close()
```

### 3.3 Instrumentation Point: Orchestrator

```python
# In controller/orchestrator.py — handle_task method

async def handle_task(self, task_request: TaskRequest) -> None:
    from controller.tracing import new_trace, emit, timed

    thread_id = task_request.thread_id
    trace_id = new_trace(thread_id)

    emit("orchestrator.task.received", "orchestrator", data={
        "source": task_request.source,
        "repo": f"{task_request.repo_owner}/{task_request.repo_name}",
        "task_preview": task_request.task[:200],
    })

    # ... existing thread upsert logic ...

    # CHECK: Is there an active job?
    active_job = await self._state.get_active_job_for_thread(thread_id)
    if active_job is not None:
        emit("orchestrator.task.queued", "orchestrator", data={
            "reason": "active_job_exists",
            "active_job": active_job.k8s_job_name,
        })
        await self._redis.queue_message(thread_id, task_request.task)
        return

    # ... lock logic ...

    try:
        await self._spawn_job(thread, task_request, trace_id=trace_id)
    finally:
        await self._state.release_lock(thread_id)
```

### 3.4 Instrumentation Point: TaskClassifier

```python
# In controller/skills/classifier.py — classify method

async def classify(
    self,
    task: str,
    language: list[str] | None = None,
    domain: list[str] | None = None,
) -> ClassificationResult:
    from controller.tracing import emit, timed

    emit("classifier.search.started", "task_classifier", data={
        "language_filter": language,
        "domain_filter": domain,
        "task_length": len(task),
    })

    matched_skills: list[Skill] = []
    task_embedding: list[float] | None = None
    filters = SkillFilters(language=language, domain=domain)

    if self._embedder:
        # Check cache
        task_embedding = self._cache.get(task)
        if task_embedding is not None:
            emit("classifier.embedding.cached", "task_classifier")
        else:
            async with timed("classifier.embedding.computed", "task_classifier") as t:
                task_embedding = await self._embedder.embed(task)
                self._cache.put(task, task_embedding)
                t.data = {"vector_dim": len(task_embedding)}

        async with timed("classifier.skills.matched", "task_classifier") as t:
            scored = await self._registry.search_by_embedding(
                task_embedding=task_embedding,
                filters=filters,
                limit=20,
            )
            min_sim = getattr(self._settings, "skill_min_similarity", 0.5)
            matched_skills = [s.skill for s in scored if s.score >= min_sim]
            t.data = {
                "method": "semantic",
                "matched_count": len(matched_skills),
                "rejected_count": len(scored) - len(matched_skills),
                "scores": [{"slug": s.skill.slug, "score": round(s.score, 3)}
                           for s in scored[:10]],
                "threshold": min_sim,
            }
    # ... tag fallback ...
```

### 3.5 Instrumentation Point: JobSpawner

```python
# In controller/jobs/spawner.py — spawn method

def spawn(
    self,
    thread_id: str,
    github_token: str,
    redis_url: str,
    agent_image: str | None = None,
    trace_id: str | None = None,
) -> str:
    from controller.tracing import emit, get_trace_id

    trace_id = trace_id or get_trace_id()
    job_spec = self.build_job_spec(
        thread_id=thread_id,
        github_token=github_token,
        redis_url=redis_url,
        agent_image=agent_image,
        extra_env={"TRACE_ID": trace_id} if trace_id else None,
    )
    job_name = job_spec.metadata.name

    emit("spawner.job.created", "spawner", data={
        "job_name": job_name,
        "image": agent_image or self._settings.agent_image,
        "thread_id": thread_id,
        "cpu_request": self._settings.agent_cpu_request,
        "memory_request": self._settings.agent_memory_request,
    })

    self._batch_api.create_namespaced_job(
        namespace=self._namespace,
        body=job_spec,
    )
    return job_name
```

---

## 4. Cross-Process Correlation

The hardest problem in tracing Ditto Factory is crossing the **Controller -> Redis -> Agent Pod** boundary. Here is how we solve it without any distributed tracing infrastructure.

### Controller Side: Pass trace_id Through Redis

The orchestrator already pushes a task payload to Redis via `push_task()`. We add `trace_id` to this payload:

```python
# In Orchestrator._spawn_job()

await self._redis.push_task(thread_id, {
    "task": task_request.task,
    "system_prompt": system_prompt,
    "repo_url": f"https://github.com/{thread.repo_owner}/{thread.repo_name}.git",
    "branch": branch,
    "skills": skills_payload,
    "trace_id": trace_id,        # NEW: propagate trace context
})
```

### Agent Pod Side: Read trace_id, Write trace_events

The agent pod reads `trace_id` from the Redis task payload and includes trace events in its result:

```python
# In agent pod startup (simplified)

import os
import json
import time

# Read trace context
task_data = redis.get(f"task:{thread_id}")
trace_id = task_data.get("trace_id", os.environ.get("TRACE_ID", "unknown"))

# Collect events during execution
trace_events = []

def agent_trace(event: str, **data):
    trace_events.append({
        "trace_id": trace_id,
        "thread_id": os.environ["THREAD_ID"],
        "event": event,
        "component": "agent",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_ms": data.pop("duration_ms", None),
        "level": data.pop("level", "INFO"),
        "data": data,
    })

# Example: wrap claude invocation
agent_trace("agent.started", image=os.environ.get("AGENT_IMAGE", "unknown"))

start = time.monotonic()
# ... run claude -p ...
duration = int((time.monotonic() - start) * 1000)

agent_trace("agent.completed", duration_ms=duration,
            exit_code=exit_code, commit_count=commit_count)

# Write result back to Redis — include trace events
redis.set(f"result:{thread_id}", json.dumps({
    "branch": branch,
    "exit_code": exit_code,
    "commit_count": commit_count,
    "stderr": stderr,
    "trace_events": trace_events,  # NEW: agent-side trace events
}))
```

### Controller Side: Ingest Agent Trace Events

When `handle_job_completion()` reads the result, it ingests the agent's trace events:

```python
# In Orchestrator.handle_job_completion()

result = await self._monitor.wait_for_result(thread_id, timeout=60)
if result is None:
    return

# Ingest agent-side trace events into local SQLite
if hasattr(result, 'trace_events') and result.trace_events:
    from controller.tracing_handler import ingest_events
    await ingest_events(self._trace_db_path, result.trace_events)
```

This gives us a **complete trace** across the process boundary, all stored in one SQLite table, all queryable with plain SQL.

---

## 5. Storage

### SQLite Schema

```sql
-- Run at application startup (controller/tracing_schema.py)

CREATE TABLE IF NOT EXISTS trace_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id    TEXT NOT NULL,
    thread_id   TEXT NOT NULL,
    event       TEXT NOT NULL,
    component   TEXT NOT NULL,
    timestamp   TEXT NOT NULL,        -- ISO 8601
    duration_ms INTEGER,              -- NULL for point-in-time events
    level       TEXT NOT NULL DEFAULT 'INFO',
    data        TEXT NOT NULL DEFAULT '{}',  -- JSON

    -- Denormalized for fast queries
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Primary query pattern: "show me everything for this trace"
CREATE INDEX IF NOT EXISTS idx_trace_events_trace_id
    ON trace_events(trace_id);

-- Secondary: "show me all traces for this thread"
CREATE INDEX IF NOT EXISTS idx_trace_events_thread_id
    ON trace_events(thread_id);

-- Tertiary: "show me recent errors"
CREATE INDEX IF NOT EXISTS idx_trace_events_level_ts
    ON trace_events(level, timestamp)
    WHERE level IN ('WARNING', 'ERROR');

-- Time-based queries for dashboards and cleanup
CREATE INDEX IF NOT EXISTS idx_trace_events_created_at
    ON trace_events(created_at);
```

### Why a Separate SQLite DB?

Use a **separate file** from the skill registry database: `trace_events.db`. Reasons:

1. **Different write patterns** — trace events are high-frequency append-only writes; skill data is low-frequency CRUD. Mixing them causes WAL contention.
2. **Different retention** — traces should be pruned aggressively (7-30 days); skill data is permanent.
3. **Independent backup/restore** — losing trace data is annoying but not catastrophic; losing skill data is.

### Retention Policy

```python
# controller/tracing_retention.py

async def prune_old_traces(db_path: str, max_age_days: int = 30) -> int:
    """Delete trace events older than max_age_days. Run daily via cron/scheduler."""
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "DELETE FROM trace_events WHERE created_at < datetime('now', ?)",
            (f"-{max_age_days} days",),
        )
        await db.commit()
        return cursor.rowcount
```

### Storage Estimates

| Scenario | Events/day | Avg event size | Daily storage | 30-day retention |
|----------|-----------|---------------|--------------|-----------------|
| Low (10 tasks/day) | ~150 | 500 bytes | 75 KB | 2.25 MB |
| Medium (100 tasks/day) | ~1,500 | 500 bytes | 750 KB | 22.5 MB |
| High (1,000 tasks/day) | ~15,000 | 500 bytes | 7.5 MB | 225 MB |

Even at high volume, this is trivial for SQLite. WAL mode handles concurrent reads (report generation) and writes (event ingestion) without issues.

---

## 6. Report Generation

### Report Query Engine

```python
# controller/tracing_report.py

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import aiosqlite


@dataclass
class TraceTimeline:
    """A complete trace rendered as a timeline of events."""
    trace_id: str
    thread_id: str
    events: list[dict]
    start_time: str
    end_time: str
    total_duration_ms: int
    component_summary: dict[str, int]  # component -> event count
    error_count: int


async def get_trace_timeline(db_path: str, trace_id: str) -> TraceTimeline | None:
    """Reconstruct the full timeline for a single trace."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM trace_events
               WHERE trace_id = ?
               ORDER BY timestamp ASC""",
            (trace_id,),
        )
        rows = await cursor.fetchall()

    if not rows:
        return None

    events = []
    component_counts: dict[str, int] = {}
    error_count = 0

    for row in rows:
        evt = {
            "event": row["event"],
            "component": row["component"],
            "timestamp": row["timestamp"],
            "duration_ms": row["duration_ms"],
            "level": row["level"],
            "data": json.loads(row["data"]),
        }
        events.append(evt)
        component_counts[row["component"]] = component_counts.get(row["component"], 0) + 1
        if row["level"] in ("WARNING", "ERROR"):
            error_count += 1

    # Calculate total duration
    first_ts = datetime.fromisoformat(events[0]["timestamp"])
    last_ts = datetime.fromisoformat(events[-1]["timestamp"])
    total_ms = int((last_ts - first_ts).total_seconds() * 1000)

    return TraceTimeline(
        trace_id=trace_id,
        thread_id=rows[0]["thread_id"],
        events=events,
        start_time=events[0]["timestamp"],
        end_time=events[-1]["timestamp"],
        total_duration_ms=total_ms,
        component_summary=component_counts,
        error_count=error_count,
    )


async def get_traces_for_thread(db_path: str, thread_id: str) -> list[str]:
    """Get all trace_ids for a thread, ordered by time."""
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            """SELECT DISTINCT trace_id
               FROM trace_events
               WHERE thread_id = ?
               ORDER BY timestamp ASC""",
            (thread_id,),
        )
        rows = await cursor.fetchall()
    return [row[0] for row in rows]
```

### Markdown Report Renderer

```python
# controller/tracing_report.py (continued)

async def render_trace_report(db_path: str, trace_id: str) -> str:
    """Render a human-readable Markdown report for a single trace."""
    timeline = await get_trace_timeline(db_path, trace_id)
    if timeline is None:
        return f"# Trace {trace_id}\n\nNo events found."

    lines = [
        f"# Trace Report: {trace_id[:12]}...",
        f"",
        f"| Field | Value |",
        f"|-------|-------|",
        f"| Thread | `{timeline.thread_id}` |",
        f"| Started | {timeline.start_time} |",
        f"| Ended | {timeline.end_time} |",
        f"| Duration | {timeline.total_duration_ms} ms |",
        f"| Events | {len(timeline.events)} |",
        f"| Errors | {timeline.error_count} |",
        f"",
        f"## Component Summary",
        f"",
    ]

    for comp, count in sorted(timeline.component_summary.items()):
        lines.append(f"- **{comp}**: {count} events")

    lines.extend(["", "## Timeline", ""])
    lines.append("| Time | Component | Event | Duration | Details |")
    lines.append("|------|-----------|-------|----------|---------|")

    for evt in timeline.events:
        ts_short = evt["timestamp"].split("T")[1][:12]
        dur = f"{evt['duration_ms']}ms" if evt["duration_ms"] else "-"
        level_marker = " :warning:" if evt["level"] == "WARNING" else \
                       " :x:" if evt["level"] == "ERROR" else ""

        # Summarize data field
        data_summary = _summarize_data(evt["data"])

        lines.append(
            f"| {ts_short} | {evt['component']} | "
            f"`{evt['event']}`{level_marker} | {dur} | {data_summary} |"
        )

    # Decision explanations section
    decision_events = [e for e in timeline.events
                       if "matched" in e["event"] or "selected" in e["event"]
                       or "fallback" in e["event"] or "rejected" in e["event"]]
    if decision_events:
        lines.extend(["", "## Key Decisions", ""])
        for evt in decision_events:
            lines.append(f"### {evt['event']}")
            lines.append(f"")
            lines.append(f"```json")
            lines.append(json.dumps(evt["data"], indent=2))
            lines.append(f"```")
            lines.append(f"")

    # Error section
    error_events = [e for e in timeline.events if e["level"] in ("WARNING", "ERROR")]
    if error_events:
        lines.extend(["", "## Errors and Warnings", ""])
        for evt in error_events:
            lines.append(f"- **[{evt['level']}]** `{evt['event']}` at {evt['timestamp']}")
            if evt["data"]:
                lines.append(f"  ```json\n  {json.dumps(evt['data'], indent=2)}\n  ```")

    return "\n".join(lines)


def _summarize_data(data: dict) -> str:
    """Create a one-line summary of event data for the timeline table."""
    if not data:
        return "-"
    parts = []
    for key in ("method", "matched_count", "slug", "job_name",
                 "exit_code", "reason", "score", "image"):
        if key in data:
            parts.append(f"{key}={data[key]}")
    if parts:
        return ", ".join(parts[:3])
    # Fallback: show first key-value
    first_key = next(iter(data))
    return f"{first_key}={str(data[first_key])[:30]}"
```

### Example Report Output

```markdown
# Trace Report: a1b2c3d4e5f6...

| Field | Value |
|-------|-------|
| Thread | `th_8f3a2b1c` |
| Started | 2026-03-21T14:32:01.100Z |
| Ended | 2026-03-21T14:35:22.890Z |
| Duration | 201790 ms |
| Events | 12 |
| Errors | 0 |

## Component Summary

- **agent**: 3 events
- **orchestrator**: 3 events
- **spawner**: 2 events
- **task_classifier**: 4 events

## Timeline

| Time | Component | Event | Duration | Details |
|------|-----------|-------|----------|---------|
| 14:32:01.100 | orchestrator | `orchestrator.task.received` | - | source=github |
| 14:32:01.150 | task_classifier | `classifier.search.started` | - | language_filter=python |
| 14:32:01.190 | task_classifier | `classifier.embedding.cached` | - | - |
| 14:32:01.234 | task_classifier | `classifier.skills.matched` | 45ms | method=semantic, matched_count=2 |
| 14:32:01.280 | orchestrator | `spawner.redis.task_pushed` | - | - |
| 14:32:01.350 | spawner | `spawner.job.created` | - | job_name=df-8f3a2b1c-1711024321 |
| 14:32:05.000 | agent | `agent.started` | - | image=ditto-agent:latest |
| 14:34:55.000 | agent | `agent.commit.created` | - | - |
| 14:35:22.000 | agent | `agent.completed` | 197000ms | exit_code=0, commit_count=1 |
| 14:35:22.500 | orchestrator | `monitor.result.received` | - | exit_code=0 |

## Key Decisions

### classifier.skills.matched

{
  "method": "semantic",
  "matched_count": 2,
  "scores": [
    {"slug": "error-handling", "score": 0.87},
    {"slug": "async-patterns", "score": 0.72}
  ],
  "threshold": 0.5
}
```

### API Endpoint for Reports

```python
# In controller/main.py or a new router

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

trace_router = APIRouter(prefix="/traces", tags=["tracing"])

@trace_router.get("/{trace_id}/report", response_class=PlainTextResponse)
async def get_trace_report(trace_id: str):
    """Render a Markdown trace report."""
    from controller.tracing_report import render_trace_report
    report = await render_trace_report(settings.trace_db_path, trace_id)
    return PlainTextResponse(report, media_type="text/markdown")

@trace_router.get("/thread/{thread_id}")
async def list_traces_for_thread(thread_id: str):
    """List all trace IDs for a thread."""
    from controller.tracing_report import get_traces_for_thread
    traces = await get_traces_for_thread(settings.trace_db_path, thread_id)
    return {"thread_id": thread_id, "traces": traces}
```

---

## 7. Pros and Cons

### What This Approach Does Well

| Strength | Detail |
|----------|--------|
| **Zero new dependencies** | Uses `logging` (stdlib), `aiosqlite` (already installed), `json` (stdlib). Nothing new to install, version, or audit. |
| **Zero new infrastructure** | No collector pods, no Jaeger deployment, no Tempo cluster. The trace store is a SQLite file on the controller's filesystem. |
| **Instantly queryable** | `sqlite3 trace_events.db "SELECT * FROM trace_events WHERE trace_id='abc'"` — no query language to learn, no UI to navigate. |
| **Readable reports** | Markdown output is copy-pasteable into GitHub issues, Slack threads, or PR descriptions. Engineers can read it without tooling. |
| **Decision transparency** | The event schema is designed to capture *why* decisions were made (skill scores, thresholds, fallback reasons), not just *that* they happened. |
| **Trivial to test** | The tracing module is pure Python. Test it with `unittest`. No mocking of gRPC exporters. |
| **Incremental adoption** | Add `emit()` calls one at a time. No all-or-nothing instrumentation. If a component has no tracing, the system still works. |

### What This Approach Cannot Do

| Limitation | Impact | Mitigation |
|-----------|--------|------------|
| **No flame graphs** | Cannot visually inspect nested span timing. | Timeline table with duration_ms is sufficient for sequential pipeline. Our flow is linear, not deeply nested. |
| **No distributed visualization** | No Jaeger-style waterfall UI across services. | We have ONE service (controller) and ephemeral pods. A Jaeger deployment would show two nodes. Not worth it. |
| **No automatic HTTP/DB instrumentation** | OTel auto-instruments FastAPI, aiosqlite, Redis. We don't get that for free. | We only need tracing at business-logic decision points, not at every HTTP handler. Manual instrumentation is more precise. |
| **SQLite single-writer bottleneck** | If multiple controller replicas write to the same SQLite file, WAL contention occurs. | For now, we run a single controller. When we scale to multiple replicas, we either shard trace DBs by instance or migrate to Postgres (a smaller migration than OTel). |
| **No real-time streaming** | Events are batched (5s flush interval). No live tail. | Structured logs also go to stdout. Use `kubectl logs -f` for real-time. |
| **Agent pod tracing is coarse** | We only capture what the agent writes to Redis. We cannot see individual Claude API calls or tool invocations in real-time. | Agent-side tracing can be extended later. The `trace_events` array in Redis is the extension point. |

### When to Reconsider

Move to OTel (Approach A) if any of these become true:

1. **Multiple controller replicas** with shared tracing needs
2. **SRE team** that expects Grafana/Jaeger dashboards
3. **Sub-second latency debugging** where flame graphs matter
4. **Compliance requirements** mandating standardized trace export

---

## 8. Implementation Effort

### Phase 1: Core Tracing (3-4 days)

| Task | Effort | Files |
|------|--------|-------|
| Create `controller/tracing.py` (TraceEvent, emit, timed, ContextVars) | 0.5 day | 1 new file |
| Create `controller/tracing_handler.py` (SQLiteTraceHandler with batching) | 1 day | 1 new file |
| Create `controller/tracing_schema.py` (table creation, retention) | 0.5 day | 1 new file |
| Wire handler into logging config at startup | 0.5 day | `main.py` |
| Instrument `Orchestrator.handle_task()` and `handle_job_completion()` | 1 day | `orchestrator.py` |
| Tests for tracing module | 0.5 day | 1 new test file |

**Deliverable:** Controller emits structured trace events for task lifecycle. Events stored in SQLite.

### Phase 2: Full Instrumentation (2-3 days)

| Task | Effort | Files |
|------|--------|-------|
| Instrument `TaskClassifier.classify()` | 0.5 day | `classifier.py` |
| Instrument `SkillInjector` | 0.25 day | `injector.py` |
| Instrument `JobSpawner.spawn()` | 0.25 day | `spawner.py` |
| Instrument `JobMonitor.wait_for_result()` | 0.25 day | `monitor.py` |
| Instrument `SafetyPipeline.process()` | 0.5 day | `safety.py` |
| Add `trace_id` to Redis task payload | 0.25 day | `orchestrator.py`, `redis_state.py` |
| Agent pod: read trace_id, write trace_events to result | 1 day | Agent-side code |

**Deliverable:** End-to-end trace from webhook to job completion, including agent pod events.

### Phase 3: Reports and API (2 days)

| Task | Effort | Files |
|------|--------|-------|
| Create `controller/tracing_report.py` (query engine + Markdown renderer) | 1 day | 1 new file |
| Add FastAPI routes for trace reports | 0.5 day | `main.py` or new router |
| Add retention cron job | 0.25 day | `tracing_schema.py` |
| Integration tests (full trace round-trip) | 0.25 day | 1 test file |

**Deliverable:** Engineers can hit `/traces/{trace_id}/report` and get a readable Markdown report.

### Total: 7-9 days

Compare with OTel (Approach A) estimate of 12-18 days including infrastructure setup and collector deployment.

---

## 9. Configuration

```python
# In controller/config.py — add to Settings

class Settings(BaseSettings):
    # ... existing fields ...

    # Tracing
    trace_enabled: bool = True
    trace_db_path: str = "trace_events.db"
    trace_batch_size: int = 50
    trace_flush_interval: float = 5.0
    trace_retention_days: int = 30
```

### Startup Wiring

```python
# In controller/main.py

import logging
from controller.tracing_handler import SQLiteTraceHandler
from controller.tracing_schema import ensure_trace_schema

async def lifespan(app: FastAPI):
    # ... existing startup ...

    if settings.trace_enabled:
        await ensure_trace_schema(settings.trace_db_path)
        trace_handler = SQLiteTraceHandler(
            db_path=settings.trace_db_path,
            batch_size=settings.trace_batch_size,
            flush_interval_seconds=settings.trace_flush_interval,
        )
        logging.getLogger("ditto.trace").addHandler(trace_handler)
        logging.getLogger("ditto.trace").setLevel(logging.DEBUG)

    yield

    # Shutdown: flush remaining trace events
    if settings.trace_enabled:
        trace_handler.close()
```

---

## ADR: Structured Logging over OpenTelemetry

### Status
Proposed

### Context
Ditto Factory has no decision tracing, no reasoning capture, and no trace correlation across the request lifecycle. We need observability into why skills were selected and how agents performed. Two approaches were evaluated: full OpenTelemetry (Approach A) and lightweight structured logging with SQLite (Approach B).

### Decision
Adopt Approach B: structured JSON logging with SQLite storage. The system has a single controller process and ephemeral K8s pods communicating through Redis. The architecture does not benefit from distributed tracing infrastructure. Manual structured logging at business-logic decision points provides more actionable data (skill scores, classification reasoning, fallback decisions) than automatic HTTP/DB instrumentation.

### Consequences
- **Easier:** Adding new trace events (one line of code), querying traces (plain SQL), generating reports (Markdown), testing (no mocks for gRPC exporters), deploying (no collector/Jaeger), onboarding engineers (JSON + SQL).
- **Harder:** Scaling to multiple controller replicas, getting flame graph visualizations, integrating with existing APM/SRE dashboards, achieving automatic instrumentation coverage.
