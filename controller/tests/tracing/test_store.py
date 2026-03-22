"""Tests for TraceStore SQLite storage."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from controller.tracing.models import TraceEventType, TraceSpan, generate_span_id, generate_trace_id
from controller.tracing.store import TraceStore


@pytest.fixture
async def store(tmp_path: Path):
    db_path = str(tmp_path / "test_traces.db")
    s = TraceStore(db_path, batch_size=5, flush_interval=60.0)
    await s.initialize()
    yield s
    await s.close()


def _make_span(
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    operation_name: TraceEventType = TraceEventType.TASK_RECEIVED,
    thread_id: str = "thread-1",
    job_id: str | None = "job-1",
    status: str = "ok",
    started_at: datetime | None = None,
    agent_name: str | None = None,
    metadata: dict | None = None,
    tool_args: dict | None = None,
) -> TraceSpan:
    now = started_at or datetime.now(timezone.utc)
    span = TraceSpan(
        span_id=generate_span_id(),
        trace_id=trace_id or generate_trace_id(),
        parent_span_id=parent_span_id,
        operation_name=operation_name,
        started_at=now,
        thread_id=thread_id,
        job_id=job_id,
        status=status,
        agent_name=agent_name,
        metadata=metadata,
        tool_args=tool_args,
    )
    span.complete("done")
    span.status = status
    return span


async def test_initialize_creates_table(store: TraceStore):
    """initialize() should create the trace_spans table."""
    import aiosqlite

    async with aiosqlite.connect(store._db_path) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='trace_spans'"
        )
        row = await cursor.fetchone()
        assert row is not None


async def test_insert_and_get_trace(store: TraceStore):
    """Round-trip: insert spans and retrieve by trace_id."""
    tid = generate_trace_id()
    s1 = _make_span(trace_id=tid)
    s2 = _make_span(trace_id=tid, parent_span_id=s1.span_id)

    await store.insert_span(s1)
    await store.insert_span(s2)
    await store.flush()

    spans = await store.get_trace(tid)
    assert len(spans) == 2
    assert spans[0].trace_id == tid
    assert spans[1].parent_span_id == s1.span_id


async def test_batch_flush(store: TraceStore):
    """Buffer flushes automatically when batch_size is reached."""
    tid = generate_trace_id()
    for _ in range(5):
        await store.insert_span(_make_span(trace_id=tid))

    # batch_size=5 so should have auto-flushed
    spans = await store.get_trace(tid)
    assert len(spans) == 5


async def test_get_spans_for_job(store: TraceStore):
    s1 = _make_span(job_id="j-100")
    s2 = _make_span(job_id="j-100")
    s3 = _make_span(job_id="j-999")

    for s in [s1, s2, s3]:
        await store.insert_span(s)
    await store.flush()

    spans = await store.get_spans_for_job("j-100")
    assert len(spans) == 2
    assert all(s.job_id == "j-100" for s in spans)


async def test_get_spans_for_thread(store: TraceStore):
    s1 = _make_span(thread_id="t-abc")
    s2 = _make_span(thread_id="t-abc")
    s3 = _make_span(thread_id="t-other")

    for s in [s1, s2, s3]:
        await store.insert_span(s)
    await store.flush()

    spans = await store.get_spans_for_thread("t-abc")
    assert len(spans) == 2
    assert all(s.thread_id == "t-abc" for s in spans)


async def test_list_traces(store: TraceStore):
    tid1 = generate_trace_id()
    tid2 = generate_trace_id()
    await store.insert_span(_make_span(trace_id=tid1))
    await store.insert_span(_make_span(trace_id=tid1))
    await store.insert_span(_make_span(trace_id=tid2))
    await store.flush()

    traces = await store.list_traces(limit=10)
    assert len(traces) == 2
    # Each trace summary should have expected keys
    for t in traces:
        assert "trace_id" in t
        assert "span_count" in t
        assert "status" in t


async def test_list_traces_error_status(store: TraceStore):
    tid = generate_trace_id()
    await store.insert_span(_make_span(trace_id=tid, status="ok"))
    await store.insert_span(_make_span(trace_id=tid, status="error"))
    await store.flush()

    traces = await store.list_traces()
    assert len(traces) == 1
    assert traces[0]["status"] == "error"


async def test_search_by_operation(store: TraceStore):
    await store.insert_span(_make_span(operation_name=TraceEventType.TOOL_INVOKED))
    await store.insert_span(_make_span(operation_name=TraceEventType.AGENT_SPAWNED))
    await store.flush()

    results = await store.search(operation_name="tool_invoked")
    assert len(results) == 1
    assert results[0].operation_name == TraceEventType.TOOL_INVOKED


async def test_search_by_status(store: TraceStore):
    await store.insert_span(_make_span(status="ok"))
    await store.insert_span(_make_span(status="error"))
    await store.flush()

    results = await store.search(status="error")
    assert len(results) == 1
    assert results[0].status == "error"


async def test_search_by_time_range(store: TraceStore):
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    recent = datetime.now(timezone.utc)

    await store.insert_span(_make_span(started_at=old))
    await store.insert_span(_make_span(started_at=recent))
    await store.flush()

    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    results = await store.search(since=cutoff)
    assert len(results) == 1


async def test_cleanup_deletes_old_spans(store: TraceStore):
    old = datetime.now(timezone.utc) - timedelta(days=60)
    recent = datetime.now(timezone.utc)

    await store.insert_span(_make_span(started_at=old))
    await store.insert_span(_make_span(started_at=recent))
    await store.flush()

    deleted = await store.cleanup(retention_days=30)
    assert deleted == 1

    # Only recent span remains
    remaining = await store.search()
    assert len(remaining) == 1


async def test_metadata_round_trip(store: TraceStore):
    meta = {"region": "us-east-1", "version": 2}
    args = {"cmd": "ls", "flags": ["-la"]}
    span = _make_span(metadata=meta, tool_args=args)
    await store.insert_span(span)
    await store.flush()

    spans = await store.get_trace(span.trace_id)
    assert len(spans) == 1
    assert spans[0].metadata == meta
    assert spans[0].tool_args == args


async def test_close_flushes_remaining(tmp_path: Path):
    db_path = str(tmp_path / "close_test.db")
    s = TraceStore(db_path, batch_size=100, flush_interval=60.0)
    await s.initialize()

    tid = generate_trace_id()
    await s.insert_span(_make_span(trace_id=tid))
    # Not yet flushed (batch_size=100)
    await s.close()

    # Verify span was flushed on close
    s2 = TraceStore(db_path)
    await s2.initialize()
    spans = await s2.get_trace(tid)
    assert len(spans) == 1
    await s2.close()
