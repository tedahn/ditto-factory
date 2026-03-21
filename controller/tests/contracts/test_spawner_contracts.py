"""Contract 4: Orchestrator -> JobSpawner.

Verifies that JobSpawner produces valid K8s Job specs with correct
naming, env vars, security context, and resource limits.
"""
import pytest
from unittest.mock import MagicMock
from controller.config import Settings
from controller.jobs.spawner import JobSpawner


class TestJobSpawnerContract:
    @pytest.fixture
    def settings(self):
        return Settings(anthropic_api_key="test", agent_image="ghcr.io/org/agent:latest")

    @pytest.fixture
    def mock_k8s(self):
        batch = MagicMock()
        batch.create_namespaced_job = MagicMock()
        return batch

    @pytest.fixture
    def spawner(self, settings, mock_k8s):
        return JobSpawner(settings=settings, batch_api=mock_k8s, namespace="test")

    def test_job_name_format(self, spawner):
        """Contract: job name is df-{short_id}-{timestamp}."""
        spec = spawner.build_job_spec("abc12345" * 8, "token", "redis://localhost")
        name = spec.metadata.name
        assert name.startswith("df-")
        parts = name.split("-")
        assert len(parts) == 3  # df, short_id, timestamp

    def test_job_name_valid_k8s_label(self, spawner):
        """Contract: job name contains only valid K8s characters."""
        weird_id = "thread/with:special!chars@here"
        spec = spawner.build_job_spec(weird_id, "token", "redis://localhost")
        name = spec.metadata.name
        assert all(c.isalnum() or c == "-" for c in name)
        assert len(name) <= 63

    def test_container_env_vars(self, spawner):
        """Contract: agent container has required env vars."""
        spec = spawner.build_job_spec("thread-1", "gh-token", "redis://redis:6379")
        container = spec.spec.template.spec.containers[0]
        env_names = {e.name for e in container.env}
        assert "THREAD_ID" in env_names
        assert "REDIS_URL" in env_names
        assert "GITHUB_TOKEN" in env_names

    def test_env_var_values(self, spawner):
        """Contract: env var values match inputs."""
        spec = spawner.build_job_spec("thread-xyz", "my-token", "redis://myredis:6379")
        container = spec.spec.template.spec.containers[0]
        env_map = {e.name: e.value for e in container.env if e.value is not None}
        assert env_map["THREAD_ID"] == "thread-xyz"
        assert env_map["REDIS_URL"] == "redis://myredis:6379"
        assert env_map["GITHUB_TOKEN"] == "my-token"

    def test_security_context(self, spawner):
        """Contract: agent runs as non-root with dropped capabilities."""
        spec = spawner.build_job_spec("thread-1", "token", "redis://localhost")
        sc = spec.spec.template.spec.containers[0].security_context
        assert sc.run_as_non_root is True
        assert sc.allow_privilege_escalation is False

    def test_backoff_limit(self, spawner):
        """Contract: job retries at most once."""
        spec = spawner.build_job_spec("thread-1", "token", "redis://localhost")
        assert spec.spec.backoff_limit == 1

    def test_restart_policy_never(self, spawner):
        """Contract: pod restart policy is Never."""
        spec = spawner.build_job_spec("thread-1", "token", "redis://localhost")
        assert spec.spec.template.spec.restart_policy == "Never"

    def test_active_deadline_matches_settings(self, spawner):
        """Contract: active_deadline_seconds matches settings.max_job_duration_seconds."""
        spec = spawner.build_job_spec("thread-1", "token", "redis://localhost")
        assert spec.spec.active_deadline_seconds == spawner._settings.max_job_duration_seconds

    def test_agent_image_from_settings(self, spawner):
        """Contract: container image comes from settings."""
        spec = spawner.build_job_spec("thread-1", "token", "redis://localhost")
        container = spec.spec.template.spec.containers[0]
        assert container.image == "ghcr.io/org/agent:latest"

    def test_spawn_returns_job_name(self, spawner, mock_k8s):
        """Contract: spawn() returns the job name string."""
        name = spawner.spawn("thread-1", "token", "redis://localhost")
        assert isinstance(name, str)
        assert name.startswith("df-")
        mock_k8s.create_namespaced_job.assert_called_once()

    def test_labels_include_thread_id(self, spawner):
        """Contract: job labels include sanitized thread ID for traceability."""
        spec = spawner.build_job_spec("abc12345" * 8, "token", "redis://localhost")
        labels = spec.metadata.labels
        assert "df/thread" in labels
        assert labels["app"] == "ditto-factory-agent"
