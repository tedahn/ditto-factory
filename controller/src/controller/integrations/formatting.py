"""Shared result message formatting for integrations."""
from __future__ import annotations

from controller.models import AgentResult, ResultType


def format_result_message(result: AgentResult) -> str:
    """Format a human-readable result message based on result type."""
    if result.exit_code != 0:
        msg = f"Task failed (exit code {result.exit_code})."
        if result.stderr:
            stderr_preview = result.stderr[:500]
            msg += f"\n```\n{stderr_preview}\n```"
        return msg

    if result.result_type == ResultType.REPORT:
        return _format_report(result)
    elif result.result_type == ResultType.PULL_REQUEST:
        return _format_pr(result)
    else:
        return _format_pr(result)


def _format_pr(result: AgentResult) -> str:
    if result.pr_url:
        return f"Done — {result.commit_count} commit(s). Pull request: {result.pr_url}"
    elif result.commit_count > 0:
        return f"Done — {result.commit_count} commit(s) on `{result.branch}`."
    else:
        return "Agent produced no changes."


def _format_report(result: AgentResult) -> str:
    parts = ["Analysis report complete."]
    for artifact in result.artifacts:
        summary = artifact.metadata.get("summary", "")
        if summary:
            parts.append(summary)
        if artifact.location and artifact.location != "inline":
            parts.append(f"Artifact: `{artifact.location}`")
    return "\n".join(parts)
