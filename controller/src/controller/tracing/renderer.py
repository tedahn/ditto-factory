"""Render trace spans into readable Markdown reports."""

from __future__ import annotations

from datetime import datetime

from controller.tracing.models import TraceEventType, TraceSpan


class TraceReportRenderer:
    """Renders trace spans into readable Markdown reports."""

    def render_hierarchical(self, spans: list[TraceSpan], title: str = "") -> str:
        """Tree view of execution hierarchy."""
        if not spans:
            return "# Execution Trace\n\nNo spans recorded.\n"

        ordered = self._build_tree(spans)
        root_spans = [s for s in spans if s.parent_span_id is None]

        # Compute overall stats
        first = min(s.started_at for s in spans)
        last_ended = max(
            (s.ended_at for s in spans if s.ended_at is not None),
            default=first,
        )
        total_ms = (last_ended - first).total_seconds() * 1000 if last_ended != first else 0.0
        overall_status = "ERROR" if any(s.status == "error" for s in spans) else "SUCCESS"
        trace_id = spans[0].trace_id
        thread_id = spans[0].thread_id or trace_id

        heading = title or f"Execution Trace: {thread_id}"
        lines: list[str] = [
            f"# {heading}",
            "",
            f"**Duration:** {self._format_duration(total_ms)} | "
            f"**Status:** {overall_status} | "
            f"**Spans:** {len(spans)}",
            "",
        ]

        step = 0
        for span, depth in ordered:
            step += 1
            duration_str = self._format_duration(span.duration_ms)
            op_name = span.operation_name.value.upper()

            if depth == 0:
                lines.append(f"## {step}. {op_name} ({duration_str})")
            else:
                indent = "  " * depth
                lines.append(f"{indent}- **{step}. {op_name}** ({duration_str})")

            details = self._span_details(span)
            prefix = "  " * (depth + 1) if depth > 0 else ""
            for detail in details:
                lines.append(f"{prefix}- {detail}")

            lines.append("")

        return "\n".join(lines)

    def render_timeline(self, spans: list[TraceSpan]) -> str:
        """Chronological table with timestamps."""
        if not spans:
            return "# Timeline\n\nNo spans recorded.\n"

        sorted_spans = sorted(spans, key=lambda s: s.started_at)
        trace_start = sorted_spans[0].started_at

        lines: list[str] = [
            "# Timeline",
            "",
            "| Time | Duration | Event | Status | Details |",
            "|------|----------|-------|--------|---------|",
        ]

        for span in sorted_spans:
            rel_time = self._format_relative_time(span.started_at, trace_start)
            duration = self._format_duration(span.duration_ms)
            event = span.operation_name.value.upper()
            status = span.status.upper()
            details = self._short_details(span)
            lines.append(f"| {rel_time} | {duration} | {event} | {status} | {details} |")

        lines.append("")
        return "\n".join(lines)

    def render_decision_summary(self, spans: list[TraceSpan]) -> str:
        """Focus on WHY decisions were made."""
        if not spans:
            return "# Decision Summary\n\nNo spans recorded.\n"

        lines: list[str] = ["# Decision Summary", ""]

        # Classification decisions
        classification_spans = [
            s for s in spans
            if s.operation_name == TraceEventType.TASK_CLASSIFIED
        ]
        if classification_spans:
            lines.append("## Classification")
            for span in classification_spans:
                lines.append("")
                if span.input_summary:
                    lines.append(f"- **Input:** {span.input_summary}")
                if span.output_summary:
                    lines.append(f"- **Result:** {span.output_summary}")
                if span.reasoning:
                    lines.append(f"- **Reasoning:** {span.reasoning}")
                if span.metadata:
                    scores = span.metadata.get("scores")
                    if scores:
                        score_str = ", ".join(
                            f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}"
                            for k, v in scores.items()
                        )
                        lines.append(f"- **Scores:** {score_str}")
            lines.append("")

        # Skill injection decisions
        injection_spans = [
            s for s in spans
            if s.operation_name == TraceEventType.SKILLS_INJECTED
        ]
        if injection_spans:
            lines.append("## Skills Selected")
            for span in injection_spans:
                lines.append("")
                if span.output_summary:
                    lines.append(f"- **Selected:** {span.output_summary}")
                if span.reasoning:
                    lines.append(f"- **Reasoning:** {span.reasoning}")
                if span.metadata:
                    count = span.metadata.get("skill_count")
                    if count is not None:
                        lines.append(f"- **Count:** {count}")
            lines.append("")

        # Agent outcomes
        agent_spans = [
            s for s in spans
            if s.operation_name == TraceEventType.AGENT_COMPLETED
        ]
        if agent_spans:
            lines.append("## Agent Outcomes")
            for span in agent_spans:
                lines.append("")
                agent = span.agent_name or "unknown"
                lines.append(f"- **Agent:** {agent}")
                if span.output_summary:
                    lines.append(f"- **Result:** {span.output_summary}")
                if span.duration_ms is not None:
                    lines.append(f"- **Duration:** {self._format_duration(span.duration_ms)}")
                if span.status == "error":
                    lines.append(f"- **Error:** {span.error_type}: {span.error_message}")
            lines.append("")

        # Errors
        error_spans = [s for s in spans if s.status == "error"]
        if error_spans:
            lines.append("## Errors")
            for span in error_spans:
                lines.append("")
                lines.append(
                    f"- **{span.operation_name.value.upper()}:** "
                    f"{span.error_type or 'Unknown'} -- {span.error_message or 'No message'}"
                )
            lines.append("")

        # All reasoning fields
        reasoning_spans = [s for s in spans if s.reasoning]
        if reasoning_spans:
            lines.append("## Reasoning Trail")
            for span in reasoning_spans:
                lines.append("")
                lines.append(f"- **{span.operation_name.value.upper()}:** {span.reasoning}")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_duration(ms: float | None) -> str:
        """Format milliseconds as human-readable: '4m 32s', '180ms', '1.2s'."""
        if ms is None:
            return "--"
        if ms < 1000:
            return f"{ms:.0f}ms"
        seconds = ms / 1000
        if seconds < 60:
            if seconds == int(seconds):
                return f"{int(seconds)}s"
            return f"{seconds:.1f}s"
        minutes = int(seconds // 60)
        remaining = int(seconds % 60)
        if remaining == 0:
            return f"{minutes}m"
        return f"{minutes}m {remaining}s"

    @staticmethod
    def _format_relative_time(span_start: datetime, trace_start: datetime) -> str:
        """Format time relative to trace start: '00:00.180'."""
        delta = (span_start - trace_start).total_seconds()
        minutes = int(delta // 60)
        seconds = delta % 60
        return f"{minutes:02d}:{seconds:06.3f}"

    @staticmethod
    def _build_tree(spans: list[TraceSpan]) -> list[tuple[TraceSpan, int]]:
        """Build ordered list of (span, depth) tuples from parent relationships."""
        by_id: dict[str, TraceSpan] = {s.span_id: s for s in spans}
        children: dict[str | None, list[TraceSpan]] = {}
        for span in spans:
            children.setdefault(span.parent_span_id, []).append(span)

        # Sort children by started_at within each group
        for key in children:
            children[key].sort(key=lambda s: s.started_at)

        result: list[tuple[TraceSpan, int]] = []

        def _walk(parent_id: str | None, depth: int) -> None:
            for child in children.get(parent_id, []):
                result.append((child, depth))
                _walk(child.span_id, depth + 1)

        _walk(None, 0)

        # If no root spans found (orphaned spans), add all at depth 0
        if not result:
            for span in sorted(spans, key=lambda s: s.started_at):
                result.append((span, 0))

        return result

    @staticmethod
    def _span_details(span: TraceSpan) -> list[str]:
        """Extract detail lines for a span in hierarchical view."""
        details: list[str] = []
        if span.input_summary:
            details.append(f"**Input:** {span.input_summary}")
        if span.output_summary:
            details.append(f"**Output:** {span.output_summary}")
        if span.reasoning:
            details.append(f"**Reasoning:** {span.reasoning}")
        if span.tool_name:
            details.append(f"**Tool:** {span.tool_name}")
        if span.model:
            tokens = ""
            if span.tokens_input is not None or span.tokens_output is not None:
                tokens = f" (in={span.tokens_input or 0}, out={span.tokens_output or 0})"
            details.append(f"**Model:** {span.model}{tokens}")
        if span.cost_usd is not None:
            details.append(f"**Cost:** ${span.cost_usd:.4f}")
        if span.agent_name:
            details.append(f"**Agent:** {span.agent_name}")
        if span.status == "error":
            details.append(f"**Error:** {span.error_type}: {span.error_message}")
        if span.metadata:
            scores = span.metadata.get("scores")
            if scores:
                score_str = ", ".join(
                    f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}"
                    for k, v in scores.items()
                )
                details.append(f"**Scores:** {score_str}")
        return details

    @staticmethod
    def _short_details(span: TraceSpan) -> str:
        """One-line summary for timeline table."""
        parts: list[str] = []
        if span.input_summary:
            summary = span.input_summary[:60]
            if len(span.input_summary) > 60:
                summary += "..."
            parts.append(summary)
        if span.tool_name:
            parts.append(f"tool={span.tool_name}")
        if span.agent_name:
            parts.append(f"agent={span.agent_name}")
        if span.error_message:
            msg = span.error_message[:40]
            if len(span.error_message) > 40:
                msg += "..."
            parts.append(f"err={msg}")
        return "; ".join(parts) if parts else "--"
