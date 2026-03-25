"""Tests for safety pipeline dispatch by result type."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from controller.models import (
    AgentResult, Thread, ThreadStatus, ResultType,
    Artifact,
)
from controller.jobs.safety import SafetyPipeline
from controller.config import Settings


def _make_thread(thread_id="t1"):
    return Thread(
        id=thread_id, source="slack", source_ref={},
        repo_owner="org", repo_name="repo",
        status=ThreadStatus.RUNNING,
    )


def _make_settings(**overrides):
    defaults = dict(
        anthropic_api_key="test",
        auto_open_pr=True,
        retry_on_empty_result=True,
        max_empty_retries=1,
    )
    defaults.update(overrides)
    return Settings(**defaults)


class TestSafetyPipelinePRDispatch:
    @pytest.mark.asyncio
    async def test_pr_auto_create_on_commits_no_pr(self):
        github_client = AsyncMock()
        github_client.create_pr = AsyncMock(return_value="https://github.com/org/repo/pull/1")
        state = AsyncMock()
        redis_state = AsyncMock()
        redis_state.drain_messages = AsyncMock(return_value=[])
        integration = AsyncMock()

        pipeline = SafetyPipeline(
            settings=_make_settings(),
            state_backend=state,
            redis_state=redis_state,
            integration=integration,
            spawner=AsyncMock(),
            github_client=github_client,
        )

        thread = _make_thread()
        result = AgentResult(branch="df/abc/123", exit_code=0, commit_count=2)
        await pipeline.process(thread, result)

        github_client.create_pr.assert_called_once()
        integration.report_result.assert_called_once()

    @pytest.mark.asyncio
    async def test_pr_retry_on_empty_result(self):
        spawner = AsyncMock()
        state = AsyncMock()
        redis_state = AsyncMock()
        integration = AsyncMock()

        pipeline = SafetyPipeline(
            settings=_make_settings(retry_on_empty_result=True, max_empty_retries=1),
            state_backend=state,
            redis_state=redis_state,
            integration=integration,
            spawner=spawner,
            github_client=AsyncMock(),
        )

        thread = _make_thread()
        result = AgentResult(branch="df/abc/123", exit_code=0, commit_count=0)
        await pipeline.process(thread, result, retry_count=0)

        spawner.assert_called_once_with("t1", is_retry=True, retry_count=1)
        integration.report_result.assert_not_called()


class TestSafetyPipelineReportDispatch:
    @pytest.mark.asyncio
    async def test_report_skips_pr_creation(self):
        github_client = AsyncMock()
        state = AsyncMock()
        redis_state = AsyncMock()
        redis_state.drain_messages = AsyncMock(return_value=[])
        integration = AsyncMock()

        pipeline = SafetyPipeline(
            settings=_make_settings(),
            state_backend=state,
            redis_state=redis_state,
            integration=integration,
            spawner=AsyncMock(),
            github_client=github_client,
        )

        thread = _make_thread()
        result = AgentResult(
            branch="", exit_code=0, commit_count=0,
            result_type=ResultType.REPORT,
            artifacts=[
                Artifact(result_type=ResultType.REPORT, location="inline",
                         metadata={"summary": "done"})
            ],
        )
        await pipeline.process(thread, result)

        github_client.create_pr.assert_not_called()
        integration.report_result.assert_called_once()

    @pytest.mark.asyncio
    async def test_report_no_retry_on_empty_commits(self):
        spawner = AsyncMock()
        state = AsyncMock()
        redis_state = AsyncMock()
        redis_state.drain_messages = AsyncMock(return_value=[])
        integration = AsyncMock()

        pipeline = SafetyPipeline(
            settings=_make_settings(retry_on_empty_result=True),
            state_backend=state,
            redis_state=redis_state,
            integration=integration,
            spawner=spawner,
            github_client=AsyncMock(),
        )

        thread = _make_thread()
        result = AgentResult(
            branch="", exit_code=0, commit_count=0,
            result_type=ResultType.REPORT,
            artifacts=[
                Artifact(result_type=ResultType.REPORT, location="inline",
                         metadata={"summary": "done"})
            ],
        )
        await pipeline.process(thread, result)

        spawner.assert_not_called()
        integration.report_result.assert_called_once()

    @pytest.mark.asyncio
    async def test_report_stores_artifacts(self):
        state = AsyncMock()
        state.create_artifact = AsyncMock()
        redis_state = AsyncMock()
        redis_state.drain_messages = AsyncMock(return_value=[])
        integration = AsyncMock()

        pipeline = SafetyPipeline(
            settings=_make_settings(),
            state_backend=state,
            redis_state=redis_state,
            integration=integration,
            spawner=AsyncMock(),
            github_client=AsyncMock(),
        )

        thread = _make_thread()
        artifact = Artifact(result_type=ResultType.REPORT, location="inline",
                            metadata={"summary": "done"})
        result = AgentResult(
            branch="", exit_code=0, commit_count=0,
            result_type=ResultType.REPORT,
            artifacts=[artifact],
        )
        await pipeline.process(thread, result)

        state.create_artifact.assert_called_once_with(task_id=thread.id, artifact=artifact)
