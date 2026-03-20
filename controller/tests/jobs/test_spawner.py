from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from controller.jobs.spawner import JobSpawner
from controller.config import Settings

@pytest.fixture
def settings():
    return Settings(
        anthropic_api_key="sk-test",
        agent_image="df-agent:abc123",
        max_job_duration_seconds=1800,
        agent_cpu_request="500m",
        agent_memory_request="2Gi",
        agent_cpu_limit="2",
        agent_memory_limit="8Gi",
    )

@pytest.fixture
def spawner(settings):
    mock_batch = MagicMock()
    mock_batch.create_namespaced_job = MagicMock()
    return JobSpawner(settings=settings, batch_api=mock_batch, namespace="default")

def test_build_job_spec(spawner):
    spec = spawner.build_job_spec(
        thread_id="abc123def456",
        github_token="ghs_shortlived",
        redis_url="redis://redis:6379",
    )
    assert spec.metadata.name.startswith("df-abc123de-")
    assert spec.spec.template.spec.containers[0].image == "df-agent:abc123"
    sc = spec.spec.template.spec.containers[0].security_context
    assert sc.run_as_non_root is True
    assert sc.allow_privilege_escalation is False
    assert spec.spec.active_deadline_seconds == 1800
    assert spec.spec.ttl_seconds_after_finished == 300

def test_spawn_job(spawner):
    spawner.spawn("abc123", "ghs_token", "redis://redis:6379")
    spawner._batch_api.create_namespaced_job.assert_called_once()

def test_delete_job(spawner):
    mock_batch = spawner._batch_api
    mock_batch.delete_namespaced_job = MagicMock()
    spawner.delete("df-abc123-12345")
    mock_batch.delete_namespaced_job.assert_called_once()
