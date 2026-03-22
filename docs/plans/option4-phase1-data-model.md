# Option 4 (B Enhanced) — Phase 1: Trace Data Model & Storage

## Status: Proposed

## Decision Context

The team selected **Option 4: B Enhanced** — Structured Logs + SQLite combining:
- Formal event types from Approach C (TaskReceived, SkillsClassified, etc.)
- LLM cost/token fields from Approach D (Langfuse compatibility)
- OTel-compatible span IDs from Approach A (W3C trace-context format for future migration)

This document is the **implementation-ready plan for Phase 1**: the trace data model, SQLite storage layer, and in-process context propagation. A developer should be able to code directly from this plan.

---

## 1. TraceSpan Dataclass

**File**: `controller/src/controller/tracing/models.py`

```python
"""Trace data models for B Enhanced traceability."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def _generate_trace_id() -> str:
    """Generate W3C-compatible trace ID (32-char lowercase hex)."""
    return os.urandom(16).hex()


def _generate_span_id() -> str:
    """Generate W3C-compatible span ID (16-char lowercase hex)."""
    return os.urandom(8).hex()


class SpanStatus(str, Enum):
    """Span completion status."""
    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class TraceEventType(str, Enum):
    """Formal event types for the trace system.

    Each event type has specific required and optional fields documented
    in the FIELD_REQUIREMENTS dict below.
    """
    TASK_RECEIVED = "task_received"
    TASK_CLASSIFIED = "task_classified"
    SKILLS_INJECTED = "skills_injected"
    AGENT_SPAWNED = "agent_spawned"
    TOOL_INVOKED = "tool_invoked"
    REASONING_STEP = "reasoning_step"
    AGENT_COMPLETED = "agent_completed"
    PR_CREATED = "pr_created"
    SAFETY_CHECK = "safety_check"
    ERROR = "error"


# Documents which fields are required (R) or optional (O) per event type.
# Fields not listed are ignored for that event type.
# Common fields (span_id, trace_id, operation_name, started_at) are always required.
FIELD_REQUIREMENTS: dict[TraceEventType, dict[str, str]] = {
    TraceEventType.TASK_RECEIVED: {
        "thread_id": "R",
        "input_summary": "R",          # the task text
        "agent_name": "O",
    },
    TraceEventType.TASK_CLASSIFIED: {
        "thread_id": "R",
        "input_summary": "R",          # task text
        "output_summary": "R",         # classification result (agent_type, skills)
        "reasoning": "O",              # classifier reasoning
        "model": "O",                  # embedding model used
        "tokens_input": "O",
        "cost_usd": "O",
    },
    TraceEventType.SKILLS_INJECTED: {
        "thread_id": "R",
        "job_id": "R",
        "output_summary": "R",         # JSON list of injected skill slugs
    },
    TraceEventType.AGENT_SPAWNED: {
        "thread_id": "R",
        "job_id": "R",
        "agent_name": "R",             # agent type name
        "output_summary": "O",         # k8s job name
    },
    TraceEventType.TOOL_INVOKED: {
        "tool_name": "R",
        "tool_args": "O",              # JSON-serialized args (truncated to 2KB)
        "tool_result": "O",            # JSON-serialized result (truncated to 2KB)
        "model": "O",
        "tokens_input": "O",
        "tokens_output": "O",
        "cost_usd": "O",
    },
    TraceEventType.REASONING_STEP: {
        "reasoning": "R",              # the LLM reasoning text (truncated to 4KB)
        "model": "R",
        "tokens_input": "R",
        "tokens_output": "R",
        "cost_usd": "O",
    },
    TraceEventType.AGENT_COMPLETED: {
        "thread_id": "R",
        "job_id": "R",
        "status": "R",
        "output_summary": "O",         # exit code, commit count, PR URL
        "tokens_input": "O",           # total tokens for the agent run
        "tokens_output": "O",
        "cost_usd": "O",
    },
    TraceEventType.PR_CREATED: {
        "thread_id": "R",
        "job_id": "R",
        "output_summary": "R",         # PR URL
    },
    TraceEventType.SAFETY_CHECK: {
        "thread_id": "R",
        "job_id": "R",
        "output_summary": "R",         # pass/fail + reason
        "status": "R",
    },
    TraceEventType.ERROR: {
        "error_type": "R",             # exception class name
        "output_summary": "R",         # error message (truncated to 2KB)
        "thread_id": "O",
        "job_id": "O",
    },
}


@dataclass
class TraceSpan:
    """A single trace span representing one operation in the pipeline.

    Uses W3C trace-context compatible IDs:
    - trace_id: 32-char lowercase hex (128-bit)
    - span_id: 16-char lowercase hex (64-bit)
    - parent_span_id: 16-char lowercase hex or None for root spans

    These formats are directly compatible with OpenTelemetry, enabling
    future migration without data transformation.
    """

    # --- Identity (OTel-compatible) ---
    span_id: str = field(default_factory=_generate_span_id)
    trace_id: str = field(default_factory=_generate_trace_id)
    parent_span_id: str | None = None

    # --- Operation ---
    operation_name: TraceEventType = TraceEventType.TASK_RECEIVED
    agent_name: str | None = None      # e.g., "general", "frontend"

    # --- Timing ---
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    ended_at: datetime | None = None
    duration_ms: float | None = None   # computed on finish

    # --- Content ---
    input_summary: str | None = None   # truncated input (max 4KB)
    output_summary: str | None = None  # truncated output (max 4KB)
    reasoning: str | None = None       # LLM reasoning (max 4KB)

    # --- Tool invocation ---
    tool_name: str | None = None
    tool_args: str | None = None       # JSON string, max 2KB
    tool_result: str | None = None     # JSON string, max 2KB

    # --- LLM cost tracking (Langfuse-compatible) ---
    model: str | None = None           # e.g., "claude-sonnet-4-20250514"
    tokens_input: int | None = None
    tokens_output: int | None = None
    cost_usd: float | None = None

    # --- Status ---
    status: SpanStatus = SpanStatus.OK
    error_type: str | None = None      # e.g., "TimeoutError", "K8sJobFailed"

    # --- Correlation ---
    thread_id: str | None = None
    job_id: str | None = None

    def finish(self, status: SpanStatus = SpanStatus.OK) -> None:
        """Mark the span as finished. Computes duration_ms."""
        self.ended_at = datetime.now(timezone.utc)
        self.status = status
        if self.started_at:
            delta = self.ended_at - self.started_at
            self.duration_ms = delta.total_seconds() * 1000

    def finish_error(self, error: Exception) -> None:
        """Mark the span as failed with an exception."""
        self.error_type = type(error).__name__
        self.output_summary = _truncate(str(error), 2048)
        self.finish(status=SpanStatus.ERROR)


def _truncate(text: str | None, max_len: int) -> str | None:
    """Truncate text to max_len chars, appending '...[truncated]' if needed."""
    if text is None:
        return None
    if len(text) <= max_len:
        return text
    return text[: max_len - 14] + "...[truncated]"
```

---

## 2. Event Type Enum

Defined above in `TraceEventType`. The `FIELD_REQUIREMENTS` dict serves as machine-readable documentation of which fields matter per event type. This is NOT enforced at write time (would add latency to the hot path), but IS used by:
- The `TraceStore.validate_span()` method (optional, for development/testing)
- The future trace viewer UI to decide which columns to display per event type

---

## 3. SQLite Schema

**File**: `controller/src/controller/tracing/schema.sql` (embedded as a constant in `store.py`)

**Design decisions**:
- Separate database file (`trace_events.db`) from the skill_usage SQLite DB — trace events are high-frequency append-only writes vs low-frequency CRUD
- WAL mode for concurrent reads during writes
- Indexes on the most common query patterns
- `TEXT` for datetime columns (ISO-8601) — SQLite has no native datetime type and ISO strings sort correctly

```sql
-- trace_events.db schema
-- Separate from main application DB: high-frequency append-only workload

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;       -- Safe with WAL, better write throughput
PRAGMA cache_size = -8000;         -- 8MB cache
PRAGMA busy_timeout = 5000;        -- 5s retry on lock contention

CREATE TABLE IF NOT EXISTS trace_spans (
    -- Identity (OTel-compatible)
    span_id             TEXT PRIMARY KEY,       -- 16-char hex
    trace_id            TEXT NOT NULL,          -- 32-char hex
    parent_span_id      TEXT,                   -- 16-char hex, NULL for root

    -- Operation
    operation_name      TEXT NOT NULL,          -- TraceEventType value
    agent_name          TEXT,

    -- Timing
    started_at          TEXT NOT NULL,          -- ISO-8601 UTC
    ended_at            TEXT,                   -- ISO-8601 UTC
    duration_ms         REAL,

    -- Content (truncated for storage efficiency)
    input_summary       TEXT,                   -- max 4KB
    output_summary      TEXT,                   -- max 4KB
    reasoning           TEXT,                   -- max 4KB

    -- Tool invocation
    tool_name           TEXT,
    tool_args           TEXT,                   -- JSON, max 2KB
    tool_result         TEXT,                   -- JSON, max 2KB

    -- LLM cost tracking (Langfuse-compatible)
    model               TEXT,
    tokens_input        INTEGER,
    tokens_output       INTEGER,
    cost_usd            REAL,

    -- Status
    status              TEXT NOT NULL DEFAULT 'ok',  -- ok|error|timeout|cancelled
    error_type          TEXT,

    -- Correlation
    thread_id           TEXT,
    job_id              TEXT
);

-- Primary query patterns and their indexes:

-- 1. "Show me the full trace for this request"
CREATE INDEX IF NOT EXISTS idx_trace_spans_trace_id
    ON trace_spans (trace_id);

-- 2. "Show me all traces for this thread/conversation"
CREATE INDEX IF NOT EXISTS idx_trace_spans_thread_id
    ON trace_spans (thread_id)
    WHERE thread_id IS NOT NULL;

-- 3. "Show me all traces for this job"
CREATE INDEX IF NOT EXISTS idx_trace_spans_job_id
    ON trace_spans (job_id)
    WHERE job_id IS NOT NULL;

-- 4. "Show me all events of type X in the last N hours"
CREATE INDEX IF NOT EXISTS idx_trace_spans_operation_time
    ON trace_spans (operation_name, started_at);

-- 5. "Show me all errors in the last N hours"
CREATE INDEX IF NOT EXISTS idx_trace_spans_errors
    ON trace_spans (status, started_at)
    WHERE status != 'ok';

-- 6. "Show me spans for a specific tool"
CREATE INDEX IF NOT EXISTS idx_trace_spans_tool
    ON trace_spans (tool_name, started_at)
    WHERE tool_name IS NOT NULL;

-- Retention: managed by TraceStore.cleanup() -- not a DB-level feature
```

---

## 4. TraceStore Class

**File**: `controller/src/controller/tracing/store.py`

### Design decisions

- **Batched writes**: Buffer spans in memory, flush when buffer hits 50 or every 5 seconds (whichever comes first). This reduces SQLite write amplification from hundreds of small transactions to a few large ones.
- **Thread-safety**: Uses `asyncio.Lock` for the write buffer. All DB access is through `aiosqlite` (async wrapper around sqlite3). The write buffer + flush loop runs in the event loop, so no threading issues.
- **Separate from PerformanceTracker**: Different write patterns (append-only vs update), different retention policies, different query needs. Sharing a DB would create lock contention.

```python
"""Trace event storage backed by SQLite with batched writes."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Sequence

import aiosqlite

from controller.tracing.models import SpanStatus, TraceEventType, TraceSpan

logger = logging.getLogger(__name__)

# Schema embedded as constant — no external .sql file to manage
_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA cache_size = -8000;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS trace_spans (
    span_id             TEXT PRIMARY KEY,
    trace_id            TEXT NOT NULL,
    parent_span_id      TEXT,
    operation_name      TEXT NOT NULL,
    agent_name          TEXT,
    started_at          TEXT NOT NULL,
    ended_at            TEXT,
    duration_ms         REAL,
    input_summary       TEXT,
    output_summary      TEXT,
    reasoning           TEXT,
    tool_name           TEXT,
    tool_args           TEXT,
    tool_result         TEXT,
    model               TEXT,
    tokens_input        INTEGER,
    tokens_output       INTEGER,
    cost_usd            REAL,
    status              TEXT NOT NULL DEFAULT 'ok',
    error_type          TEXT,
    thread_id           TEXT,
    job_id              TEXT
);

CREATE INDEX IF NOT EXISTS idx_trace_spans_trace_id
    ON trace_spans (trace_id);
CREATE INDEX IF NOT EXISTS idx_trace_spans_thread_id
    ON trace_spans (thread_id) WHERE thread_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_trace_spans_job_id
    ON trace_spans (job_id) WHERE job_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_trace_spans_operation_time
    ON trace_spans (operation_name, started_at);
CREATE INDEX IF NOT EXISTS idx_trace_spans_errors
    ON trace_spans (status, started_at) WHERE status != 'ok';
CREATE INDEX IF NOT EXISTS idx_trace_spans_tool
    ON trace_spans (tool_name, started_at) WHERE tool_name IS NOT NULL;
"""

# Column order for INSERT — must match _span_to_tuple()
_INSERT_SQL = """
INSERT OR REPLACE INTO trace_spans (
    span_id, trace_id, parent_span_id,
    operation_name, agent_name,
    started_at, ended_at, duration_ms,
    input_summary, output_summary, reasoning,
    tool_name, tool_args, tool_result,
    model, tokens_input, tokens_output, cost_usd,
    status, error_type,
    thread_id, job_id
) VALUES (?,?,?, ?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?,?, ?,?, ?,?)
"""


class TraceStore:
    """Append-only trace event store backed by SQLite.

    Features:
    - Batched writes: buffers up to `batch_size` spans, flushes every
      `flush_interval_seconds` or when the buffer is full.
    - Thread-safe: uses asyncio.Lock for the write buffer.
    - Auto-cleanup: `cleanup()` removes spans older than `retention_days`.

    Usage:
        store = TraceStore("trace_events.db")
        await store.initialize()           # creates table + starts flush loop
        await store.insert_span(span)      # buffered write
        trace = await store.get_trace("abc123...")  # immediate read
        await store.shutdown()             # flush remaining + stop loop
    """

    def __init__(
        self,
        db_path: str,
        batch_size: int = 50,
        flush_interval_seconds: float = 5.0,
        retention_days: int = 30,
    ) -> None:
        self._db_path = db_path
        self._batch_size = batch_size
        self._flush_interval = flush_interval_seconds
        self._retention_days = retention_days

        self._buffer: list[TraceSpan] = []
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None
        self._initialized = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create schema and start the periodic flush loop."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
        self._flush_task = asyncio.create_task(self._flush_loop())
        self._initialized = True
        logger.info("TraceStore initialized: %s", self._db_path)

    async def shutdown(self) -> None:
        """Flush remaining buffer and cancel the flush loop."""
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self._flush_now()
        self._initialized = False
        logger.info("TraceStore shut down")

    # ------------------------------------------------------------------
    # Write path (buffered)
    # ------------------------------------------------------------------

    async def insert_span(self, span: TraceSpan) -> None:
        """Add a span to the write buffer. Flushes if buffer is full."""
        async with self._lock:
            self._buffer.append(span)
            if len(self._buffer) >= self._batch_size:
                await self._flush_locked()

    async def insert_spans(self, spans: Sequence[TraceSpan]) -> None:
        """Add multiple spans to the write buffer."""
        async with self._lock:
            self._buffer.extend(spans)
            if len(self._buffer) >= self._batch_size:
                await self._flush_locked()

    async def _flush_loop(self) -> None:
        """Periodically flush the buffer to SQLite."""
        while True:
            await asyncio.sleep(self._flush_interval)
            await self._flush_now()

    async def _flush_now(self) -> None:
        """Acquire lock and flush."""
        async with self._lock:
            await self._flush_locked()

    async def _flush_locked(self) -> None:
        """Flush buffer to SQLite. Caller must hold self._lock."""
        if not self._buffer:
            return
        batch = self._buffer[:]
        self._buffer.clear()

        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.executemany(
                    _INSERT_SQL,
                    [_span_to_tuple(s) for s in batch],
                )
                await db.commit()
            logger.debug("Flushed %d trace spans", len(batch))
        except Exception:
            logger.exception("Failed to flush %d trace spans", len(batch))
            # Re-add to buffer so they are not lost (best-effort)
            async with self._lock:
                self._buffer = batch + self._buffer

    # ------------------------------------------------------------------
    # Read path (immediate, no buffer)
    # ------------------------------------------------------------------

    async def get_trace(self, trace_id: str) -> list[TraceSpan]:
        """Get all spans for a trace, ordered by started_at.

        Args:
            trace_id: 32-char hex trace ID.

        Returns:
            List of TraceSpan ordered by started_at ascending.
        """
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM trace_spans WHERE trace_id = ? ORDER BY started_at",
                (trace_id,),
            )
            return [_row_to_span(r) for r in rows]

    async def get_spans_for_job(self, job_id: str) -> list[TraceSpan]:
        """Get all spans for a job, ordered by started_at.

        Args:
            job_id: The job ID to filter by.

        Returns:
            List of TraceSpan ordered by started_at ascending.
        """
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM trace_spans WHERE job_id = ? ORDER BY started_at",
                (job_id,),
            )
            return [_row_to_span(r) for r in rows]

    async def get_spans_for_thread(self, thread_id: str) -> list[TraceSpan]:
        """Get all spans for a thread, ordered by started_at.

        Args:
            thread_id: The thread ID to filter by.

        Returns:
            List of TraceSpan ordered by started_at ascending.
        """
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM trace_spans WHERE thread_id = ? ORDER BY started_at",
                (thread_id,),
            )
            return [_row_to_span(r) for r in rows]

    async def search_spans(
        self,
        *,
        operation_name: TraceEventType | None = None,
        status: SpanStatus | None = None,
        tool_name: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[TraceSpan]:
        """Search spans by operation type, status, tool, and time range.

        All filters are AND-combined. Returns most recent first.

        Args:
            operation_name: Filter by event type.
            status: Filter by span status.
            tool_name: Filter by tool name.
            since: Only spans started at or after this time.
            until: Only spans started before this time.
            limit: Max results (default 100, max 1000).

        Returns:
            List of TraceSpan ordered by started_at descending.
        """
        conditions: list[str] = []
        params: list[str | int] = []

        if operation_name is not None:
            conditions.append("operation_name = ?")
            params.append(operation_name.value)
        if status is not None:
            conditions.append("status = ?")
            params.append(status.value)
        if tool_name is not None:
            conditions.append("tool_name = ?")
            params.append(tool_name)
        if since is not None:
            conditions.append("started_at >= ?")
            params.append(since.isoformat())
        if until is not None:
            conditions.append("started_at < ?")
            params.append(until.isoformat())

        where = " AND ".join(conditions) if conditions else "1=1"
        limit = min(limit, 1000)

        sql = f"SELECT * FROM trace_spans WHERE {where} ORDER BY started_at DESC LIMIT ?"
        params.append(limit)

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(sql, params)
            return [_row_to_span(r) for r in rows]

    async def get_cost_summary(
        self,
        *,
        trace_id: str | None = None,
        thread_id: str | None = None,
        job_id: str | None = None,
        since: datetime | None = None,
    ) -> dict:
        """Aggregate LLM cost and token usage.

        Filter by trace, thread, job, or time range.

        Returns:
            Dict with keys: total_cost_usd, total_tokens_input,
            total_tokens_output, span_count.
        """
        conditions: list[str] = ["cost_usd IS NOT NULL"]
        params: list[str] = []

        if trace_id:
            conditions.append("trace_id = ?")
            params.append(trace_id)
        if thread_id:
            conditions.append("thread_id = ?")
            params.append(thread_id)
        if job_id:
            conditions.append("job_id = ?")
            params.append(job_id)
        if since:
            conditions.append("started_at >= ?")
            params.append(since.isoformat())

        where = " AND ".join(conditions)
        sql = f"""
            SELECT
                COALESCE(SUM(cost_usd), 0.0) AS total_cost_usd,
                COALESCE(SUM(tokens_input), 0) AS total_tokens_input,
                COALESCE(SUM(tokens_output), 0) AS total_tokens_output,
                COUNT(*) AS span_count
            FROM trace_spans
            WHERE {where}
        """

        async with aiosqlite.connect(self._db_path) as db:
            row = await db.execute_fetchall(sql, params)
            r = row[0] if row else (0.0, 0, 0, 0)
            return {
                "total_cost_usd": r[0],
                "total_tokens_input": r[1],
                "total_tokens_output": r[2],
                "span_count": r[3],
            }

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    async def cleanup(self, retention_days: int | None = None) -> int:
        """Delete spans older than retention_days.

        Args:
            retention_days: Override instance default. Defaults to
                self._retention_days (30 days).

        Returns:
            Number of deleted rows.
        """
        days = retention_days or self._retention_days
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()

        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "DELETE FROM trace_spans WHERE started_at < ?",
                (cutoff,),
            )
            await db.commit()
            deleted = cursor.rowcount
            logger.info(
                "Trace cleanup: deleted %d spans older than %d days",
                deleted,
                days,
            )
            return deleted

    async def count(self) -> int:
        """Return total number of stored spans."""
        async with aiosqlite.connect(self._db_path) as db:
            row = await db.execute_fetchall(
                "SELECT COUNT(*) FROM trace_spans"
            )
            return row[0][0] if row else 0


# ------------------------------------------------------------------
# Serialization helpers
# ------------------------------------------------------------------

def _span_to_tuple(span: TraceSpan) -> tuple:
    """Convert TraceSpan to a tuple matching _INSERT_SQL column order."""
    return (
        span.span_id,
        span.trace_id,
        span.parent_span_id,
        span.operation_name.value if isinstance(span.operation_name, TraceEventType) else span.operation_name,
        span.agent_name,
        span.started_at.isoformat() if span.started_at else None,
        span.ended_at.isoformat() if span.ended_at else None,
        span.duration_ms,
        span.input_summary,
        span.output_summary,
        span.reasoning,
        span.tool_name,
        span.tool_args,
        span.tool_result,
        span.model,
        span.tokens_input,
        span.tokens_output,
        span.cost_usd,
        span.status.value if isinstance(span.status, SpanStatus) else span.status,
        span.error_type,
        span.thread_id,
        span.job_id,
    )


def _row_to_span(row: aiosqlite.Row) -> TraceSpan:
    """Convert a database row to a TraceSpan dataclass."""
    return TraceSpan(
        span_id=row["span_id"],
        trace_id=row["trace_id"],
        parent_span_id=row["parent_span_id"],
        operation_name=TraceEventType(row["operation_name"]),
        agent_name=row["agent_name"],
        started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else datetime.now(timezone.utc),
        ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
        duration_ms=row["duration_ms"],
        input_summary=row["input_summary"],
        output_summary=row["output_summary"],
        reasoning=row["reasoning"],
        tool_name=row["tool_name"],
        tool_args=row["tool_args"],
        tool_result=row["tool_result"],
        model=row["model"],
        tokens_input=row["tokens_input"],
        tokens_output=row["tokens_output"],
        cost_usd=row["cost_usd"],
        status=SpanStatus(row["status"]) if row["status"] else SpanStatus.OK,
        error_type=row["error_type"],
        thread_id=row["thread_id"],
        job_id=row["job_id"],
    )
```

---

## 5. TraceContext (contextvars-based)

**File**: `controller/src/controller/tracing/context.py`

This provides in-process trace propagation so that any code running within a request handler automatically inherits the current trace/span context. Child spans auto-nest under their parent.

```python
"""In-process trace context propagation using contextvars.

Usage:
    # At request entry point (e.g., webhook handler):
    async with trace_span(TraceEventType.TASK_RECEIVED, thread_id="abc") as span:
        span.input_summary = task_request.task

        # Nested span automatically becomes a child:
        async with trace_span(TraceEventType.TASK_CLASSIFIED) as child:
            child.output_summary = "agent_type=general, skills=[debug-react]"

    # From anywhere in the call stack:
    ctx = get_trace_context()
    ctx.trace_id   # current trace ID
    ctx.span_id    # current span ID
"""

from __future__ import annotations

import contextvars
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from controller.tracing.models import (
    SpanStatus,
    TraceEventType,
    TraceSpan,
    _generate_span_id,
    _generate_trace_id,
)

logger = logging.getLogger(__name__)

# --- Context variables ---
_current_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "trace_id", default=None
)
_current_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_span_id", default=None
)
_current_parent_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_parent_span_id", default=None
)

# Global reference to TraceStore — set once at app startup via configure()
_trace_store = None


@dataclass
class TraceContextSnapshot:
    """Read-only snapshot of the current trace context."""
    trace_id: str | None
    span_id: str | None
    parent_span_id: str | None


def configure(store) -> None:
    """Set the global TraceStore instance. Call once at app startup.

    Args:
        store: A TraceStore instance (or None to disable tracing).
    """
    global _trace_store
    _trace_store = store


def get_trace_context() -> TraceContextSnapshot:
    """Get a read-only snapshot of the current trace context.

    Safe to call from anywhere — returns None fields if no trace is active.
    """
    return TraceContextSnapshot(
        trace_id=_current_trace_id.get(),
        span_id=_current_span_id.get(),
        parent_span_id=_current_parent_span_id.get(),
    )


@asynccontextmanager
async def trace_span(
    operation: TraceEventType,
    *,
    trace_id: str | None = None,
    thread_id: str | None = None,
    job_id: str | None = None,
    agent_name: str | None = None,
) -> AsyncIterator[TraceSpan]:
    """Context manager that creates, propagates, and stores a trace span.

    Auto-nesting: if called within an existing trace_span, the new span
    becomes a child (inherits trace_id, sets parent_span_id).

    If no trace is active and no trace_id is provided, a new trace is started.

    Args:
        operation: The event type for this span.
        trace_id: Explicit trace ID (overrides context). Use for root spans.
        thread_id: Thread ID to attach to the span.
        job_id: Job ID to attach to the span.
        agent_name: Agent type name.

    Yields:
        The TraceSpan — callers can set additional fields before the
        context manager exits.

    On exit, the span is finished and inserted into the TraceStore
    (if configured). The previous context is restored.
    """
    # Resolve trace_id: explicit > inherited > new
    resolved_trace_id = trace_id or _current_trace_id.get() or _generate_trace_id()

    # Resolve parent: current span becomes parent of new span
    parent_span_id = _current_span_id.get()

    # Create the span
    span = TraceSpan(
        span_id=_generate_span_id(),
        trace_id=resolved_trace_id,
        parent_span_id=parent_span_id,
        operation_name=operation,
        agent_name=agent_name,
        thread_id=thread_id,
        job_id=job_id,
    )

    # Set context vars (save old values for restoration)
    token_trace = _current_trace_id.set(resolved_trace_id)
    token_span = _current_span_id.set(span.span_id)
    token_parent = _current_parent_span_id.set(parent_span_id)

    try:
        yield span
        if span.ended_at is None:
            span.finish(SpanStatus.OK)
    except Exception as exc:
        span.finish_error(exc)
        raise
    finally:
        # Persist span (fire-and-forget if store is configured)
        if _trace_store is not None:
            try:
                await _trace_store.insert_span(span)
            except Exception:
                logger.exception("Failed to persist trace span %s", span.span_id)

        # Restore previous context
        _current_trace_id.reset(token_trace)
        _current_span_id.reset(token_span)
        _current_parent_span_id.reset(token_parent)
```

---

## 6. File Layout

### New files to create

```
controller/src/controller/tracing/
    __init__.py              # Public API exports
    models.py                # TraceSpan, TraceEventType, SpanStatus, FIELD_REQUIREMENTS
    store.py                 # TraceStore class (SQLite + batched writes)
    context.py               # trace_span() context manager, get_trace_context()
```

### `__init__.py` contents

```python
"""Tracing subsystem for Ditto Factory (Option 4: B Enhanced).

Public API:
    - trace_span(): async context manager for creating trace spans
    - get_trace_context(): read current trace/span IDs
    - configure(): set the global TraceStore at app startup
    - TraceStore: SQLite-backed trace storage
    - TraceSpan: span data model
    - TraceEventType: formal event type enum
    - SpanStatus: span completion status enum
"""

from controller.tracing.context import configure, get_trace_context, trace_span
from controller.tracing.models import SpanStatus, TraceEventType, TraceSpan
from controller.tracing.store import TraceStore

__all__ = [
    "TraceEventType",
    "TraceSpan",
    "SpanStatus",
    "TraceStore",
    "configure",
    "get_trace_context",
    "trace_span",
]
```

### Existing files to modify

| File | Change | Reason |
|------|--------|--------|
| `controller/src/controller/config.py` | Add `trace_db_path`, `trace_retention_days`, `trace_batch_size`, `trace_flush_interval`, `tracing_enabled` fields to `Settings` | Configurable trace storage location and behavior |
| `controller/src/controller/main.py` | Initialize `TraceStore`, call `configure(store)` at startup, `store.shutdown()` at shutdown | Wire tracing into app lifecycle |
| `controller/src/controller/orchestrator.py` | No changes in Phase 1 | Instrumentation is Phase 2 |

### Config additions (exact fields)

```python
# Add to Settings class in config.py:

    # Tracing (Option 4: B Enhanced)
    tracing_enabled: bool = False
    trace_db_path: str = "trace_events.db"
    trace_retention_days: int = 30
    trace_batch_size: int = 50
    trace_flush_interval: float = 5.0
```

### main.py startup wiring (pseudocode)

```python
# In the app lifespan or startup hook:

from controller.tracing import TraceStore, configure

if settings.tracing_enabled:
    trace_store = TraceStore(
        db_path=settings.trace_db_path,
        batch_size=settings.trace_batch_size,
        flush_interval_seconds=settings.trace_flush_interval,
        retention_days=settings.trace_retention_days,
    )
    await trace_store.initialize()
    configure(trace_store)

# In shutdown:
if trace_store:
    await trace_store.shutdown()
```

---

## 7. Test Plan

**Location**: `controller/tests/tracing/`

### Test files

| File | What it tests |
|------|--------------|
| `test_models.py` | TraceSpan creation, defaults, finish(), finish_error(), _truncate(), ID format validation |
| `test_store.py` | TraceStore CRUD, batching, flush, search, cleanup, cost_summary |
| `test_context.py` | trace_span() nesting, context propagation, error handling, configure() |

### `test_models.py` — TraceSpan unit tests

```python
"""Tests for controller.tracing.models."""

import pytest
from datetime import datetime, timezone
from controller.tracing.models import (
    TraceSpan, TraceEventType, SpanStatus,
    _generate_trace_id, _generate_span_id, _truncate,
)


class TestTraceIdGeneration:
    def test_trace_id_is_32_hex_chars(self):
        tid = _generate_trace_id()
        assert len(tid) == 32
        assert all(c in "0123456789abcdef" for c in tid)

    def test_span_id_is_16_hex_chars(self):
        sid = _generate_span_id()
        assert len(sid) == 16
        assert all(c in "0123456789abcdef" for c in sid)

    def test_ids_are_unique(self):
        ids = {_generate_trace_id() for _ in range(1000)}
        assert len(ids) == 1000


class TestTraceSpan:
    def test_defaults(self):
        span = TraceSpan()
        assert len(span.span_id) == 16
        assert len(span.trace_id) == 32
        assert span.parent_span_id is None
        assert span.status == SpanStatus.OK
        assert span.ended_at is None

    def test_finish_sets_ended_at_and_duration(self):
        span = TraceSpan()
        span.finish()
        assert span.ended_at is not None
        assert span.duration_ms is not None
        assert span.duration_ms >= 0
        assert span.status == SpanStatus.OK

    def test_finish_error_captures_exception(self):
        span = TraceSpan()
        span.finish_error(ValueError("something broke"))
        assert span.status == SpanStatus.ERROR
        assert span.error_type == "ValueError"
        assert "something broke" in span.output_summary

    def test_finish_error_truncates_long_message(self):
        span = TraceSpan()
        span.finish_error(RuntimeError("x" * 5000))
        assert len(span.output_summary) <= 2048


class TestTruncate:
    def test_short_string_unchanged(self):
        assert _truncate("hello", 100) == "hello"

    def test_none_returns_none(self):
        assert _truncate(None, 100) is None

    def test_long_string_truncated(self):
        result = _truncate("a" * 200, 50)
        assert len(result) <= 50
        assert result.endswith("...[truncated]")

    def test_exact_length_unchanged(self):
        s = "a" * 50
        assert _truncate(s, 50) == s
```

### `test_store.py` — TraceStore integration tests

```python
"""Tests for controller.tracing.store."""

import asyncio
import pytest
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from controller.tracing.models import TraceSpan, TraceEventType, SpanStatus
from controller.tracing.store import TraceStore


@pytest.fixture
async def store(tmp_path):
    """Create a TraceStore with a temp DB, small batch for testing."""
    db_path = str(tmp_path / "test_traces.db")
    s = TraceStore(db_path, batch_size=5, flush_interval_seconds=0.1, retention_days=7)
    await s.initialize()
    yield s
    await s.shutdown()


@pytest.fixture
def make_span():
    """Factory for creating test spans with specific attributes."""
    def _make(
        trace_id: str = "a" * 32,
        operation: TraceEventType = TraceEventType.TASK_RECEIVED,
        thread_id: str = "thread-1",
        job_id: str = "job-1",
        **kwargs,
    ) -> TraceSpan:
        span = TraceSpan(
            trace_id=trace_id,
            operation_name=operation,
            thread_id=thread_id,
            job_id=job_id,
        )
        for k, v in kwargs.items():
            setattr(span, k, v)
        span.finish()
        return span
    return _make


class TestInsertAndGet:
    async def test_insert_and_get_trace(self, store, make_span):
        span = make_span()
        await store.insert_span(span)
        await store._flush_now()  # force flush for test

        result = await store.get_trace(span.trace_id)
        assert len(result) == 1
        assert result[0].span_id == span.span_id

    async def test_get_spans_for_job(self, store, make_span):
        s1 = make_span(job_id="j1")
        s2 = make_span(job_id="j2")
        await store.insert_spans([s1, s2])
        await store._flush_now()

        result = await store.get_spans_for_job("j1")
        assert len(result) == 1
        assert result[0].job_id == "j1"

    async def test_get_spans_for_thread(self, store, make_span):
        s1 = make_span(thread_id="t1")
        s2 = make_span(thread_id="t2")
        await store.insert_spans([s1, s2])
        await store._flush_now()

        result = await store.get_spans_for_thread("t1")
        assert len(result) == 1


class TestBatching:
    async def test_auto_flush_on_batch_size(self, store, make_span):
        """Buffer should auto-flush when batch_size (5) is reached."""
        for i in range(5):
            await store.insert_span(make_span(trace_id=f"{i:032x}"))

        # Give flush a moment
        await asyncio.sleep(0.05)
        count = await store.count()
        assert count == 5

    async def test_periodic_flush(self, store, make_span):
        """Buffer should flush on timer even if batch_size not reached."""
        await store.insert_span(make_span())
        # flush_interval is 0.1s in fixture
        await asyncio.sleep(0.2)
        count = await store.count()
        assert count == 1


class TestSearch:
    async def test_search_by_operation(self, store, make_span):
        s1 = make_span(operation=TraceEventType.TASK_RECEIVED)
        s2 = make_span(operation=TraceEventType.ERROR)
        await store.insert_spans([s1, s2])
        await store._flush_now()

        result = await store.search_spans(operation_name=TraceEventType.ERROR)
        assert len(result) == 1
        assert result[0].operation_name == TraceEventType.ERROR

    async def test_search_by_status(self, store, make_span):
        ok = make_span()
        err = make_span(status=SpanStatus.ERROR, error_type="TestError")
        await store.insert_spans([ok, err])
        await store._flush_now()

        result = await store.search_spans(status=SpanStatus.ERROR)
        assert len(result) == 1

    async def test_search_by_time_range(self, store, make_span):
        old = make_span()
        old.started_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        new = make_span()

        await store.insert_spans([old, new])
        await store._flush_now()

        result = await store.search_spans(since=datetime(2025, 1, 1, tzinfo=timezone.utc))
        assert len(result) == 1


class TestCleanup:
    async def test_cleanup_removes_old_spans(self, store, make_span):
        old = make_span()
        old.started_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        new = make_span()

        await store.insert_spans([old, new])
        await store._flush_now()

        deleted = await store.cleanup(retention_days=7)
        assert deleted == 1
        assert await store.count() == 1


class TestCostSummary:
    async def test_aggregate_costs(self, store, make_span):
        s1 = make_span(tokens_input=100, tokens_output=50, cost_usd=0.01)
        s2 = make_span(tokens_input=200, tokens_output=100, cost_usd=0.02)
        await store.insert_spans([s1, s2])
        await store._flush_now()

        summary = await store.get_cost_summary(thread_id="thread-1")
        assert summary["total_cost_usd"] == pytest.approx(0.03)
        assert summary["total_tokens_input"] == 300
        assert summary["total_tokens_output"] == 150
        assert summary["span_count"] == 2


class TestConcurrentWrites:
    async def test_concurrent_inserts(self, store, make_span):
        """Multiple coroutines writing simultaneously should not lose data."""
        async def writer(n: int):
            for i in range(10):
                await store.insert_span(make_span(trace_id=f"{n * 100 + i:032x}"))

        await asyncio.gather(*[writer(n) for n in range(5)])
        await store._flush_now()
        count = await store.count()
        assert count == 50


class TestEdgeCases:
    async def test_empty_db_queries(self, store):
        assert await store.get_trace("nonexistent") == []
        assert await store.get_spans_for_job("nope") == []
        assert await store.count() == 0

    async def test_span_with_all_none_optional_fields(self, store):
        span = TraceSpan(operation_name=TraceEventType.TASK_RECEIVED)
        span.finish()
        await store.insert_span(span)
        await store._flush_now()

        result = await store.get_trace(span.trace_id)
        assert len(result) == 1
```

### `test_context.py` — TraceContext tests

```python
"""Tests for controller.tracing.context."""

import pytest
from unittest.mock import AsyncMock

from controller.tracing.context import (
    configure,
    get_trace_context,
    trace_span,
)
from controller.tracing.models import TraceEventType, SpanStatus


@pytest.fixture
def mock_store():
    store = AsyncMock()
    store.insert_span = AsyncMock()
    configure(store)
    yield store
    configure(None)


class TestTraceSpanContext:
    async def test_creates_root_span(self, mock_store):
        async with trace_span(TraceEventType.TASK_RECEIVED) as span:
            assert span.trace_id is not None
            assert span.parent_span_id is None

        mock_store.insert_span.assert_called_once()

    async def test_nested_spans_inherit_trace_id(self, mock_store):
        async with trace_span(TraceEventType.TASK_RECEIVED) as parent:
            parent_trace = parent.trace_id
            async with trace_span(TraceEventType.TASK_CLASSIFIED) as child:
                assert child.trace_id == parent_trace
                assert child.parent_span_id == parent.span_id

    async def test_deeply_nested_spans(self, mock_store):
        async with trace_span(TraceEventType.TASK_RECEIVED) as root:
            async with trace_span(TraceEventType.TASK_CLASSIFIED) as mid:
                async with trace_span(TraceEventType.SKILLS_INJECTED) as leaf:
                    assert leaf.parent_span_id == mid.span_id
                    assert mid.parent_span_id == root.span_id
                    assert leaf.trace_id == root.trace_id

    async def test_context_restored_after_span(self, mock_store):
        ctx_before = get_trace_context()
        async with trace_span(TraceEventType.TASK_RECEIVED):
            pass
        ctx_after = get_trace_context()
        assert ctx_before.trace_id == ctx_after.trace_id
        assert ctx_before.span_id == ctx_after.span_id

    async def test_error_in_span_sets_error_status(self, mock_store):
        with pytest.raises(ValueError):
            async with trace_span(TraceEventType.TOOL_INVOKED) as span:
                raise ValueError("test error")

        persisted = mock_store.insert_span.call_args[0][0]
        assert persisted.status == SpanStatus.ERROR
        assert persisted.error_type == "ValueError"

    async def test_explicit_trace_id(self, mock_store):
        async with trace_span(
            TraceEventType.TASK_RECEIVED,
            trace_id="a" * 32,
        ) as span:
            assert span.trace_id == "a" * 32

    async def test_thread_and_job_id_propagated(self, mock_store):
        async with trace_span(
            TraceEventType.TASK_RECEIVED,
            thread_id="t1",
            job_id="j1",
        ) as span:
            assert span.thread_id == "t1"
            assert span.job_id == "j1"


class TestGetTraceContext:
    async def test_returns_none_outside_span(self):
        configure(None)
        ctx = get_trace_context()
        assert ctx.trace_id is None
        assert ctx.span_id is None

    async def test_returns_current_ids_inside_span(self, mock_store):
        async with trace_span(TraceEventType.TASK_RECEIVED) as span:
            ctx = get_trace_context()
            assert ctx.trace_id == span.trace_id
            assert ctx.span_id == span.span_id


class TestNoStoreConfigured:
    async def test_span_works_without_store(self):
        configure(None)
        async with trace_span(TraceEventType.TASK_RECEIVED) as span:
            span.input_summary = "test"
        # No error, span just not persisted
```

---

## 8. ADR

```markdown
# ADR-004: Separate SQLite Database for Trace Events

## Status
Accepted

## Context
The controller already uses SQLite via aiosqlite for skill_usage tracking
(PerformanceTracker). Trace events will have a fundamentally different write
pattern: high-frequency append-only (potentially hundreds of spans per task)
vs low-frequency CRUD (one skill_usage row per skill per task).

Sharing a database file would mean:
- WAL checkpoint contention between the trace writer and skill_usage reader
- Trace cleanup (DELETE old rows) would trigger autovacuum affecting skill_usage queries
- Different retention policies would need to coexist in one schema

## Decision
Use a separate `trace_events.db` file for the tracing subsystem. The TraceStore
class manages its own connection pool and lifecycle independently of
PerformanceTracker.

## Consequences
- Easier: Independent WAL, independent retention, independent backup/restore.
  No risk of trace write storms affecting skill_usage queries.
- Harder: Two SQLite files to manage in deployment. Two database connections
  in the process. Slightly more configuration (trace_db_path setting).
- Reversible: If we later migrate to Postgres or OTel collector, only TraceStore
  needs to change. No impact on PerformanceTracker.
```

---

## Summary: Implementation Checklist

| # | Task | Est. |
|---|------|------|
| 1 | Create `controller/src/controller/tracing/__init__.py` | 10 min |
| 2 | Create `controller/src/controller/tracing/models.py` with TraceSpan, enums, FIELD_REQUIREMENTS | 1 hr |
| 3 | Create `controller/src/controller/tracing/store.py` with TraceStore | 2 hr |
| 4 | Create `controller/src/controller/tracing/context.py` with trace_span() context manager | 1 hr |
| 5 | Add config fields to `controller/src/controller/config.py` | 15 min |
| 6 | Add startup/shutdown wiring to `controller/src/controller/main.py` | 30 min |
| 7 | Create `controller/tests/tracing/__init__.py` | 5 min |
| 8 | Create `controller/tests/tracing/test_models.py` | 1 hr |
| 9 | Create `controller/tests/tracing/test_store.py` | 2 hr |
| 10 | Create `controller/tests/tracing/test_context.py` | 1 hr |
| **Total** | | **~9 hr** |

### What Phase 1 does NOT include (deferred to Phase 2)
- Instrumenting the orchestrator, spawner, monitor, safety pipeline
- API endpoints for querying traces
- Scheduled cleanup job (manual `store.cleanup()` only)
- Structured log integration (correlating span_id with log lines)
