from unittest.mock import AsyncMock
import pytest
from controller.jobs.safety import SafetyPipeline
from controller.models import AgentResult, Thread, ThreadStatus
from controller.config import Settings

@pytest.fixture
def settings():
    return Settings(
        anthropic_api_key="test",
        auto_open_pr=True,
        retry_on_empty_result=True,
        max_empty_retries=1,
    )

@pytest.fixture
def pipeline(settings):
    return SafetyPipeline(
        settings=settings,
        state_backend=AsyncMock(),
        redis_state=AsyncMock(),
        integration=AsyncMock(),
        spawner=AsyncMock(),
        github_client=AsyncMock(),
    )

async def test_successful_result_reports(pipeline):
    result = AgentResult(branch="df/abc/123", exit_code=0, commit_count=3, pr_url="https://github.com/org/repo/pull/1")
    thread = Thread(id="t1", source="slack", source_ref={}, repo_owner="org", repo_name="repo", status=ThreadStatus.RUNNING)
    await pipeline.process(thread, result)
    pipeline._integration.report_result.assert_called_once()

async def test_auto_opens_pr_when_missing(pipeline):
    result = AgentResult(branch="df/abc/123", exit_code=0, commit_count=3)
    thread = Thread(id="t1", source="slack", source_ref={}, repo_owner="org", repo_name="repo", status=ThreadStatus.RUNNING)
    pipeline._github_client.create_pr = AsyncMock(return_value="https://github.com/org/repo/pull/2")
    await pipeline.process(thread, result)
    pipeline._github_client.create_pr.assert_called_once()

async def test_retry_on_empty_result(pipeline):
    result = AgentResult(branch="df/abc/123", exit_code=0, commit_count=0)
    thread = Thread(id="t1", source="slack", source_ref={}, repo_owner="org", repo_name="repo", status=ThreadStatus.RUNNING)
    await pipeline.process(thread, result, retry_count=0)
    pipeline._spawner.assert_called_once()

async def test_no_retry_after_max(pipeline):
    result = AgentResult(branch="df/abc/123", exit_code=0, commit_count=0)
    thread = Thread(id="t1", source="slack", source_ref={}, repo_owner="org", repo_name="repo", status=ThreadStatus.RUNNING)
    await pipeline.process(thread, result, retry_count=1)
    pipeline._integration.report_result.assert_called_once()

async def test_checks_queue_after_completion(pipeline):
    result = AgentResult(branch="df/abc/123", exit_code=0, commit_count=2)
    thread = Thread(id="t1", source="slack", source_ref={}, repo_owner="org", repo_name="repo", status=ThreadStatus.RUNNING)
    pipeline._redis_state.drain_messages = AsyncMock(return_value=["also fix tests"])
    await pipeline.process(thread, result)
    pipeline._redis_state.drain_messages.assert_called_once_with("t1")
