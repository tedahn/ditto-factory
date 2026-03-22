"""Trace context propagation using contextvars."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from controller.tracing.models import TraceEventType, TraceSpan, generate_span_id, generate_trace_id
from controller.tracing.store import TraceStore

logger = logging.getLogger(__name__)

_trace_id: ContextVar[str | None] = ContextVar("_trace_id", default=None)
_span_id: ContextVar[str | None] = ContextVar("_span_id", default=None)


class TraceContext:
    """Holds current trace/span IDs for in-process propagation."""

    @staticmethod
    def get_trace_id() -> str | None:
        return _trace_id.get()

    @staticmethod
    def set_trace_id(tid: str) -> None:
        _trace_id.set(tid)

    @staticmethod
    def get_span_id() -> str | None:
        return _span_id.get()

    @staticmethod
    def set_span_id(sid: str) -> None:
        _span_id.set(sid)


@asynccontextmanager
async def trace_span(
    operation_name: TraceEventType,
    store: TraceStore | None = None,
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    thread_id: str = "",
    job_id: str | None = None,
    input_summary: str = "",
    agent_name: str | None = None,
) -> AsyncGenerator[TraceSpan, None]:
    """Context manager that creates, yields, and persists a TraceSpan.

    Auto-nests: if no trace_id given, inherits from context.
    If no parent_span_id given, uses current span as parent.
    Sets itself as current span for children.
    """
    tid = trace_id or TraceContext.get_trace_id() or generate_trace_id()
    pid = parent_span_id or TraceContext.get_span_id()
    sid = generate_span_id()

    span = TraceSpan(
        span_id=sid,
        trace_id=tid,
        parent_span_id=pid,
        operation_name=operation_name,
        started_at=datetime.now(timezone.utc),
        input_summary=input_summary,
        thread_id=thread_id,
        job_id=job_id,
        agent_name=agent_name,
    )

    old_trace = _trace_id.set(tid)
    old_span = _span_id.set(sid)

    try:
        yield span
        if span.status == "ok" and span.ended_at is None:
            span.complete(span.output_summary)
    except Exception as exc:
        span.status = "error"
        span.error_type = type(exc).__name__
        span.error_message = str(exc)[:500]
        span.ended_at = datetime.now(timezone.utc)
        span.duration_ms = (span.ended_at - span.started_at).total_seconds() * 1000
        raise
    finally:
        _trace_id.reset(old_trace)
        _span_id.reset(old_span)
        if store is not None:
            try:
                await store.insert_span(span)
            except Exception:
                logger.warning("Failed to persist trace span", exc_info=True)
