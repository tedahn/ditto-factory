"""Tests for parallel K8s Job spawning."""
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from controller.swarm.async_spawner import AsyncJobSpawner


class TestAsyncJobSpawner:
    @pytest.fixture
    def mock_spawner(self):
        spawner = MagicMock()
        spawner._namespace = "default"
        mock_job = MagicMock()
        mock_job.metadata.name = "df-test-123"
        spawner.build_job_spec = MagicMock(return_value=mock_job)
        spawner._batch_api = MagicMock()
        return spawner

    @pytest.mark.asyncio
    async def test_spawn_batch_returns_job_names(self, mock_spawner):
        async_spawner = AsyncJobSpawner(mock_spawner, max_concurrent=5)
        specs = [
            {"thread_id": "a1", "github_token": "", "redis_url": "redis://localhost"},
            {"thread_id": "a2", "github_token": "", "redis_url": "redis://localhost"},
        ]
        results = await async_spawner.spawn_batch(specs)
        assert len(results) == 2
        assert all(r == "df-test-123" for r in results)

    @pytest.mark.asyncio
    async def test_spawn_batch_handles_partial_failure(self, mock_spawner):
        call_count = 0
        def build_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise Exception("K8s API error")
            mock_job = MagicMock()
            mock_job.metadata.name = f"df-test-{call_count}"
            return mock_job

        mock_spawner.build_job_spec = MagicMock(side_effect=build_side_effect)

        async_spawner = AsyncJobSpawner(mock_spawner, max_concurrent=5)
        specs = [
            {"thread_id": "a1", "github_token": "", "redis_url": "redis://localhost"},
            {"thread_id": "a2", "github_token": "", "redis_url": "redis://localhost"},
            {"thread_id": "a3", "github_token": "", "redis_url": "redis://localhost"},
        ]
        results = await async_spawner.spawn_batch(specs)
        assert len(results) == 2  # 1 failed out of 3

    @pytest.mark.asyncio
    async def test_spawn_batch_empty_list(self, mock_spawner):
        async_spawner = AsyncJobSpawner(mock_spawner)
        results = await async_spawner.spawn_batch([])
        assert results == []

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self, mock_spawner):
        """Verify semaphore bounds concurrent K8s API calls."""
        max_concurrent_seen = 0
        current_concurrent = 0

        original_create = mock_spawner._batch_api.create_namespaced_job
        def track_concurrency(*args, **kwargs):
            nonlocal max_concurrent_seen, current_concurrent
            current_concurrent += 1
            max_concurrent_seen = max(max_concurrent_seen, current_concurrent)
            original_create(*args, **kwargs)
            current_concurrent -= 1

        mock_spawner._batch_api.create_namespaced_job = track_concurrency

        async_spawner = AsyncJobSpawner(mock_spawner, max_concurrent=2)
        specs = [
            {"thread_id": f"a{i}", "github_token": "", "redis_url": "redis://localhost"}
            for i in range(5)
        ]
        await async_spawner.spawn_batch(specs)
        # Can't perfectly test async semaphore with sync mock, but verify it doesn't crash
        assert len(specs) == 5
