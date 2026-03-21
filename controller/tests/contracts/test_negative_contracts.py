"""Negative and Failure Contract Tests.

Tests error propagation, data corruption, and failure modes across
contract boundaries.
"""
import json

import pytest
import fakeredis.aioredis
from unittest.mock import AsyncMock
from controller.state.redis_state import RedisState
from controller.models import AgentResult, Thread, ThreadStatus


class TestStateBackendFailureContracts:
    """What happens when the state backend raises?"""

    async def test_orchestrator_propagates_state_errors(self):
        """Contract: StateBackend exceptions propagate to FastAPI error handler."""
        from controller.orchestrator import Orchestrator
        from controller.models import TaskRequest
        from controller.config import Settings

        state = AsyncMock()
        state.get_thread = AsyncMock(side_effect=Exception("DB connection lost"))

        orch = Orchestrator(
            settings=Settings(anthropic_api_key="test"),
            state=state,
            redis_state=AsyncMock(),
            registry=AsyncMock(),
            spawner=AsyncMock(),
            monitor=AsyncMock(),
        )
        task = TaskRequest(
            thread_id="a" * 64, source="github",
            source_ref={"number": 1}, repo_owner="o", repo_name="r", task="t",
        )
        with pytest.raises(Exception, match="DB connection lost"):
            await orch.handle_task(task)


class TestRedisCorruptionContracts:
    """What happens when Redis returns corrupted data?"""

    @pytest.fixture
    async def redis_state(self):
        redis = fakeredis.aioredis.FakeRedis()
        return RedisState(redis)

    async def test_corrupted_json_in_result(self, redis_state):
        """Contract: corrupted JSON in result key -> json.loads raises."""
        await redis_state._redis.set("result:corrupt", b"not-valid-json{{{")
        with pytest.raises(json.JSONDecodeError):
            await redis_state.get_result("corrupt")

    async def test_corrupted_json_in_task(self, redis_state):
        """Contract: corrupted JSON in task key -> json.loads raises."""
        await redis_state._redis.set("task:corrupt", b"<<<invalid>>>")
        with pytest.raises(json.JSONDecodeError):
            await redis_state.get_task("corrupt")

    async def test_empty_string_in_result(self, redis_state):
        """Contract: empty string treated as falsy, returns None."""
        await redis_state._redis.set("result:empty", b"")
        # Empty string is falsy in Python, so get_result returns None
        result = await redis_state.get_result("empty")
        assert result is None

    async def test_partial_result_missing_fields(self, redis_state):
        """Contract: result with missing required fields -> dict.get defaults handle it."""
        partial = {"branch": "df/test/x"}  # Missing exit_code, commit_count, stderr
        await redis_state.push_result("thread-partial", partial)
        parsed = await redis_state.get_result("thread-partial")

        # Controller code uses .get() with defaults, so this should work
        result = AgentResult(
            branch=parsed.get("branch", ""),
            exit_code=int(parsed.get("exit_code", 1)),
            commit_count=int(parsed.get("commit_count", 0)),
            stderr=parsed.get("stderr", ""),
        )
        assert result.exit_code == 1  # Default
        assert result.commit_count == 0  # Default


class TestIntegrationReportFailureContracts:
    """What happens when integration.report_result throws?"""

    async def test_report_failure_leaves_thread_in_non_idle(self):
        """Contract: if report_result raises, thread stays in RUNNING.
        This is an unhandled failure mode -- SafetyPipeline does not
        catch exceptions from integration.report_result."""
        from controller.jobs.safety import SafetyPipeline
        from controller.config import Settings

        integration = AsyncMock()
        integration.report_result = AsyncMock(side_effect=Exception("Slack API down"))
        state = AsyncMock()

        pipeline = SafetyPipeline(
            settings=Settings(anthropic_api_key="test", auto_open_pr=False),
            state_backend=state,
            redis_state=AsyncMock(drain_messages=AsyncMock(return_value=[])),
            integration=integration,
            spawner=AsyncMock(),
            github_client=AsyncMock(),
        )
        thread = Thread(
            id="t1", source="slack", source_ref={"channel": "C1"},
            repo_owner="o", repo_name="r", status=ThreadStatus.RUNNING,
        )
        result = AgentResult(branch="df/test/x", exit_code=0, commit_count=1, pr_url="url")

        with pytest.raises(Exception, match="Slack API down"):
            await pipeline.process(thread, result)

        # Thread status NOT updated to IDLE (update_thread_status never reached)
        state.update_thread_status.assert_not_called()

    async def test_spawner_failure_during_retry_propagates(self):
        """Contract: if spawner raises during retry, exception propagates."""
        from controller.jobs.safety import SafetyPipeline
        from controller.config import Settings

        spawner = AsyncMock(side_effect=Exception("K8s unavailable"))

        pipeline = SafetyPipeline(
            settings=Settings(
                anthropic_api_key="test", auto_open_pr=False,
                retry_on_empty_result=True, max_empty_retries=3,
            ),
            state_backend=AsyncMock(),
            redis_state=AsyncMock(),
            integration=AsyncMock(),
            spawner=spawner,
            github_client=AsyncMock(),
        )
        thread = Thread(
            id="t1", source="github", source_ref={},
            repo_owner="o", repo_name="r", status=ThreadStatus.RUNNING,
        )
        result = AgentResult(branch="df/test/x", exit_code=0, commit_count=0)

        with pytest.raises(Exception, match="K8s unavailable"):
            await pipeline.process(thread, result, retry_count=0)
