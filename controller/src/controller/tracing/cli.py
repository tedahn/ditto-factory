"""CLI for trace inspection: python -m controller.tracing <command>."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m controller.tracing",
        description="Ditto Factory trace inspector",
    )
    subparsers = parser.add_subparsers(dest="command")

    # report <trace_id> [--view ...] [--output ...]
    report_parser = subparsers.add_parser("report", help="Render a trace report")
    report_parser.add_argument("trace_id", help="Trace ID to report on")
    report_parser.add_argument(
        "--view",
        choices=["hierarchical", "timeline", "decision"],
        default="hierarchical",
        help="Report view type (default: hierarchical)",
    )
    report_parser.add_argument(
        "--output", "-o",
        help="Write report to file instead of stdout",
    )

    # list [--limit N]
    list_parser = subparsers.add_parser("list", help="List recent traces")
    list_parser.add_argument(
        "--limit", type=int, default=20,
        help="Number of traces to show (default: 20)",
    )

    # search [--status ...] [--since ...] [--operation ...] [--limit N]
    search_parser = subparsers.add_parser("search", help="Search trace spans")
    search_parser.add_argument(
        "--status",
        choices=["ok", "error", "timeout"],
        help="Filter by status",
    )
    search_parser.add_argument(
        "--since",
        help="Time window: 1h, 24h, 7d",
    )
    search_parser.add_argument(
        "--operation",
        help="Filter by operation name (e.g. task_classified)",
    )
    search_parser.add_argument(
        "--limit", type=int, default=20,
        help="Max results (default: 20)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> None:
    from controller.config import Settings
    from controller.tracing.store import TraceStore

    settings = Settings()
    store = TraceStore(db_path=settings.trace_db_path)
    await store.initialize()

    try:
        if args.command == "report":
            await _report(store, args)
        elif args.command == "list":
            await _list(store, args)
        elif args.command == "search":
            await _search(store, args)
    finally:
        await store.close()


async def _report(store: object, args: argparse.Namespace) -> None:
    from controller.tracing.renderer import TraceReportRenderer
    from controller.tracing.store import TraceStore

    assert isinstance(store, TraceStore)
    spans = await store.get_trace(args.trace_id)
    if not spans:
        print(f"No trace found: {args.trace_id}")
        return

    renderer = TraceReportRenderer()
    if args.view == "timeline":
        report = renderer.render_timeline(spans)
    elif args.view == "decision":
        report = renderer.render_decision_summary(spans)
    else:
        report = renderer.render_hierarchical(spans)

    if args.output:
        with open(args.output, "w") as f:
            f.write(report)
        print(f"Report written to {args.output}")
    else:
        print(report)


async def _list(store: object, args: argparse.Namespace) -> None:
    from controller.tracing.store import TraceStore

    assert isinstance(store, TraceStore)
    traces = await store.list_traces(limit=args.limit)

    if not traces:
        print("No traces found.")
        return

    # Print as formatted table
    print(f"{'TRACE ID':<36} {'THREAD':<20} {'SPANS':>5} {'STATUS':<6} {'STARTED'}")
    print("-" * 90)
    for t in traces:
        trace_id = t["trace_id"][:34]
        thread_id = (t["thread_id"] or "")[:18]
        span_count = t["span_count"]
        status = t["status"]
        started = t["started_at"][:19] if t["started_at"] else "--"
        print(f"{trace_id:<36} {thread_id:<20} {span_count:>5} {status:<6} {started}")


async def _search(store: object, args: argparse.Namespace) -> None:
    from controller.tracing.renderer import TraceReportRenderer
    from controller.tracing.store import TraceStore

    assert isinstance(store, TraceStore)

    since: datetime | None = None
    if args.since:
        since = _parse_since(args.since)

    spans = await store.search(
        operation_name=args.operation,
        status=args.status,
        since=since,
        limit=args.limit,
    )

    if not spans:
        print("No matching spans found.")
        return

    fmt = TraceReportRenderer._format_duration

    print(f"{'SPAN ID':<18} {'TRACE ID':<18} {'OPERATION':<20} {'STATUS':<7} {'DURATION'}")
    print("-" * 80)
    for s in spans:
        span_id = s.span_id[:16]
        trace_id = s.trace_id[:16]
        op = s.operation_name.value[:18]
        status = s.status
        duration = fmt(s.duration_ms)
        print(f"{span_id:<18} {trace_id:<18} {op:<20} {status:<7} {duration}")


def _parse_since(value: str) -> datetime:
    """Parse a human-friendly time window like '1h', '24h', '7d'."""
    value = value.strip().lower()
    now = datetime.now(timezone.utc)

    if value.endswith("h"):
        hours = int(value[:-1])
        return now - timedelta(hours=hours)
    elif value.endswith("d"):
        days = int(value[:-1])
        return now - timedelta(days=days)
    elif value.endswith("m"):
        minutes = int(value[:-1])
        return now - timedelta(minutes=minutes)
    else:
        raise ValueError(f"Invalid time window: {value}. Use format like 1h, 24h, 7d")
