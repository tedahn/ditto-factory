import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from controller.jobs.monitor import JobMonitor
from controller.models import AgentResult
from controller.state.redis_state import RedisState

@pytest.fixture
def mock_redis():
    return AsyncMock(spec=RedisState)

@pytest.fixture
def mock_batch():
    return MagicMock()

@pytest.fixture
def monitor(mock_redis, mock_batch):
    return JobMonitor(redis_state=mock_redis, batch_api=mock_batch, namespace="default")

async def test_wait_for_result_immediate(monitor, mock_redis):
    mock_redis.get_result = AsyncMock(return_value={
        "branch": "df/t1/123", "exit_code": 0, "commit_count": 3, "stderr": ""
    })
    result = await monitor.wait_for_result("t1", timeout=10, poll_interval=0.1)
    assert result is not None
    assert result.branch == "df/t1/123"
    assert result.commit_count == 3

async def test_wait_for_result_timeout(monitor, mock_redis):
    mock_redis.get_result = AsyncMock(return_value=None)
    result = await monitor.wait_for_result("t1", timeout=0.3, poll_interval=0.1)
    assert result is None

async def test_wait_for_result_delayed(monitor, mock_redis):
    call_count = 0
    async def delayed_result(thread_id):
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            return {"branch": "df/t1/123", "exit_code": 0, "commit_count": 1, "stderr": ""}
        return None
    mock_redis.get_result = delayed_result
    result = await monitor.wait_for_result("t1", timeout=5, poll_interval=0.1)
    assert result is not None
    assert result.commit_count == 1

def test_is_job_running_active(monitor, mock_batch):
    mock_job = MagicMock()
    mock_job.status.active = 1
    mock_batch.read_namespaced_job.return_value = mock_job
    assert monitor.is_job_running("df-abc-123") is True

def test_is_job_running_completed(monitor, mock_batch):
    mock_job = MagicMock()
    mock_job.status.active = 0
    mock_batch.read_namespaced_job.return_value = mock_job
    assert monitor.is_job_running("df-abc-123") is False

def test_is_job_running_not_found(monitor, mock_batch):
    from kubernetes.client import ApiException
    mock_batch.read_namespaced_job.side_effect = ApiException(status=404)
    assert monitor.is_job_running("df-abc-123") is False
