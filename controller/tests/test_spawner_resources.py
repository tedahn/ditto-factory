"""Tests for per-role resource profile in Job spawner."""
from unittest.mock import MagicMock
from controller.jobs.spawner import JobSpawner
from controller.config import Settings
from controller.models import ResourceProfile


class TestSpawnerResourceProfile:
    def test_default_resources_when_no_profile(self):
        settings = Settings(anthropic_api_key="test")
        spawner = JobSpawner(settings, MagicMock(), "default")
        job = spawner.build_job_spec("t1", "token", "redis://localhost")
        container = job.spec.template.spec.containers[0]
        assert container.resources.requests["cpu"] == settings.agent_cpu_request
        assert container.resources.requests["memory"] == settings.agent_memory_request

    def test_custom_resource_profile_applied(self):
        settings = Settings(anthropic_api_key="test")
        spawner = JobSpawner(settings, MagicMock(), "default")
        profile = ResourceProfile("100m", "250m", "256Mi", "512Mi")
        job = spawner.build_job_spec(
            "t1", "token", "redis://localhost",
            resource_profile=profile,
        )
        container = job.spec.template.spec.containers[0]
        assert container.resources.requests["cpu"] == "100m"
        assert container.resources.requests["memory"] == "256Mi"
        assert container.resources.limits["cpu"] == "250m"
        assert container.resources.limits["memory"] == "512Mi"

    def test_spawn_passes_resource_profile(self):
        settings = Settings(anthropic_api_key="test")
        mock_batch_api = MagicMock()
        spawner = JobSpawner(settings, mock_batch_api, "default")
        profile = ResourceProfile("100m", "250m", "256Mi", "512Mi")
        job_name = spawner.spawn(
            "t1", "token", "redis://localhost",
            resource_profile=profile,
        )
        # Verify the API was called
        mock_batch_api.create_namespaced_job.assert_called_once()
        # Verify the job spec has the right resources
        call_kwargs = mock_batch_api.create_namespaced_job.call_args
        job_body = call_kwargs[1]["body"] if "body" in call_kwargs[1] else call_kwargs[0][1]
        container = job_body.spec.template.spec.containers[0]
        assert container.resources.requests["cpu"] == "100m"
