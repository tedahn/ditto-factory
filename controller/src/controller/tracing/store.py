"""SQLite-backed trace span storage with buffered writes."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from controller.tracing.models import TraceEventType, TraceSpan

logger = logging.getLogger(__name__)

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS trace_spans (
    span_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    parent_span_id TEXT,
    operation_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_ms REAL,
    input_summary TEXT DEFAULT '',
    output_summary TEXT DEFAULT '',
    reasoning TEXT DEFAULT '',
    tool_name TEXT,
    tool_args TEXT,
    tool_result TEXT,
    model TEXT,
    tokens_input INTEGER,
    tokens_output INTEGER,
    cost_usd REAL,
    status TEXT DEFAULT 'ok',
    error_type TEXT,
    error_message TEXT,
    thread_id TEXT DEFAULT '',
    job_id TEXT,
    agent_name TEXT,
    metadata TEXT
)"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_trace_spans_trace_id ON trace_spans(trace_id)",
    "CREATE INDEX IF NOT EXISTS idx_trace_spans_thread_id ON trace_spans(thread_id)",
    "CREATE INDEX IF NOT EXISTS idx_trace_spans_job_id ON trace_spans(job_id)",
    "CREATE INDEX IF NOT EXISTS idx_trace_spans_operation ON trace_spans(operation_name)",
    "CREATE INDEX IF NOT EXISTS idx_trace_spans_started_at ON trace_spans(started_at)",
    "CREATE INDEX IF NOT EXISTS idx_trace_spans_status ON trace_spans(status)",
]

_INSERT_SQL = """\
INSERT OR REPLACE INTO trace_spans (
    span_id, trace_id, parent_span_id, operation_name,
    started_at, ended_at, duration_ms,
    input_summary, output_summary, reasoning,
    tool_name, tool_args, tool_result,
    model, tokens_input, tokens_output, cost_usd,
    status, error_type, error_message,
    thread_id, job_id, agent_name, metadata
) VALUES (?,?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?,?, ?,?,?, ?,?,?,?)"""


def _span_to_row(span: TraceSpan) -> tuple:
    return (
        span.span_id,
        span.trace_id,
        span.parent_span_id,
        span.operation_name.value if isinstance(span.operation_name, TraceEventType) else span.operation_name,
        span.started_at.isoformat(),
        span.ended_at.isoformat() if span.ended_at else None,
        span.duration_ms,
        span.input_summary,
        span.output_summary,
        span.reasoning,
        span.tool_name,
        json.dumps(span.tool_args) if span.tool_args is not None else None,
        span.tool_result,
        span.model,
        span.tokens_input,
        span.tokens_output,
        span.cost_usd,
        span.status,
        span.error_type,
        span.error_message,
        span.thread_id,
        span.job_id,
        span.agent_name,
        json.dumps(span.metadata) if span.metadata is not None else None,
    )


def _row_to_span(row: aiosqlite.Row) -> TraceSpan:
    return TraceSpan(
        span_id=row["span_id"],
        trace_id=row["trace_id"],
        parent_span_id=row["parent_span_id"],
        operation_name=TraceEventType(row["operation_name"]),
        started_at=datetime.fromisoformat(row["started_at"]),
        ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
        duration_ms=row["duration_ms"],
        input_summary=row["input_summary"] or "",
        output_summary=row["output_summary"] or "",
        reasoning=row["reasoning"] or "",
        tool_name=row["tool_name"],
        tool_args=json.loads(row["tool_args"]) if row["tool_args"] else None,
        tool_result=row["tool_result"],
        model=row["model"],
        tokens_input=row["tokens_input"],
        tokens_output=row["tokens_output"],
        cost_usd=row["cost_usd"],
        status=row["status"] or "ok",
        error_type=row["error_type"],
        error_message=row["error_message"],
        thread_id=row["thread_id"] or "",
        job_id=row["job_id"],
        agent_name=row["agent_name"],
        metadata=json.loads(row["metadata"]) if row["metadata"] else None,
    )


class TraceStore:
    """Buffered, async SQLite store for trace spans."""

    def __init__(
        self,
        db_path: str,
        batch_size: int = 50,
        flush_interval: float = 5.0,
    ) -> None:
        self._db_path = db_path
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._buffer: list[TraceSpan] = []
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None

    async def initialize(self) -> None:
        """Create tables and indexes. Call once at startup."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(_CREATE_TABLE)
            for idx_sql in _CREATE_INDEXES:
                await db.execute(idx_sql)
            await db.commit()
        self._flush_task = asyncio.create_task(self._periodic_flush())

    async def _periodic_flush(self) -> None:
        """Background task that flushes the buffer periodically."""
        while True:
            try:
                await asyncio.sleep(self._flush_interval)
                await self.flush()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.warning("Periodic trace flush failed", exc_info=True)

    async def insert_span(self, span: TraceSpan) -> None:
        """Add span to write buffer. Flushes when buffer reaches batch_size."""
        async with self._lock:
            self._buffer.append(span)
            should_flush = len(self._buffer) >= self._batch_size
        if should_flush:
            await self.flush()

    async def flush(self) -> None:
        """Write buffered spans to SQLite."""
        async with self._lock:
            if not self._buffer:
                return
            batch = list(self._buffer)
            self._buffer.clear()

        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.executemany(_INSERT_SQL, [_span_to_row(s) for s in batch])
                await db.commit()
        except Exception:
            logger.warning("Failed to flush %d trace spans", len(batch), exc_info=True)
            # Re-add to buffer so spans are not lost
            async with self._lock:
                self._buffer = batch + self._buffer

    async def get_trace(self, trace_id: str) -> list[TraceSpan]:
        """Get all spans for a trace, ordered by started_at."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM trace_spans WHERE trace_id = ? ORDER BY started_at",
                (trace_id,),
            )
            rows = await cursor.fetchall()
            return [_row_to_span(r) for r in rows]

    async def get_spans_for_job(self, job_id: str) -> list[TraceSpan]:
        """Get all spans for a job."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM trace_spans WHERE job_id = ? ORDER BY started_at",
                (job_id,),
            )
            rows = await cursor.fetchall()
            return [_row_to_span(r) for r in rows]

    async def get_spans_for_thread(self, thread_id: str) -> list[TraceSpan]:
        """Get all spans for a thread."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM trace_spans WHERE thread_id = ? ORDER BY started_at",
                (thread_id,),
            )
            rows = await cursor.fetchall()
            return [_row_to_span(r) for r in rows]

    async def list_traces(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """List recent traces with summary info."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """\
                SELECT trace_id,
                       thread_id,
                       MIN(started_at) AS started_at,
                       COUNT(*) AS span_count,
                       CASE WHEN SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) > 0
                            THEN 'error' ELSE 'ok' END AS status
                FROM trace_spans
                GROUP BY trace_id
                ORDER BY started_at DESC
                LIMIT ? OFFSET ?""",
                (limit, offset),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "trace_id": r["trace_id"],
                    "thread_id": r["thread_id"],
                    "started_at": r["started_at"],
                    "span_count": r["span_count"],
                    "status": r["status"],
                }
                for r in rows
            ]

    async def search(
        self,
        operation_name: str | None = None,
        status: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
    ) -> list[TraceSpan]:
        """Search spans with filters."""
        conditions: list[str] = []
        params: list[str | int | float] = []

        if operation_name is not None:
            conditions.append("operation_name = ?")
            params.append(operation_name)
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if since is not None:
            conditions.append("started_at >= ?")
            params.append(since.isoformat())
        if until is not None:
            conditions.append("started_at <= ?")
            params.append(until.isoformat())

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM trace_spans{where} ORDER BY started_at DESC LIMIT ?"
        params.append(limit)

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [_row_to_span(r) for r in rows]

    async def cleanup(self, retention_days: int = 30) -> int:
        """Delete spans older than retention_days. Returns count deleted."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "DELETE FROM trace_spans WHERE started_at < ?",
                (cutoff,),
            )
            await db.commit()
            return cursor.rowcount

    async def close(self) -> None:
        """Flush remaining buffer and cancel background task."""
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self.flush()
