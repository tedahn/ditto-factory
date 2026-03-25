"""Parallel K8s Job spawner with semaphore-based concurrency control."""
from __future__ import annotations

import asyncio
import logging
from controller.jobs.spawner import JobSpawner

logger = logging.getLogger(__name__)


class AsyncJobSpawner:
    """Wraps JobSpawner for parallel K8s Job creation.

    Uses asyncio.gather() with a semaphore to limit concurrent K8s API
    calls, preventing API server overload while maximizing spawn speed.
    """

    def __init__(self, spawner: JobSpawner, max_concurrent: int = 20):
        self._spawner = spawner
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def spawn_one(self, **kwargs) -> str:
        """Spawn a single K8s Job with semaphore-bounded concurrency."""
        async with self._semaphore:
            job = self._spawner.build_job_spec(**kwargs)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                self._spawner._batch_api.create_namespaced_job,
                self._spawner._namespace,
                job,
            )
            return job.metadata.name

    async def spawn_batch(self, agent_specs: list[dict]) -> list[str]:
        """Spawn multiple K8s Jobs in parallel.

        Returns list of successful job names. Failed spawns are logged
        but don't block other agents from starting.
        """
        if not agent_specs:
            return []

        tasks = [self.spawn_one(**spec) for spec in agent_specs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        succeeded = []
        failed_count = 0
        for spec, result in zip(agent_specs, results):
            if isinstance(result, Exception):
                failed_count += 1
                logger.warning(
                    "Failed to spawn agent %s: %s",
                    spec.get("thread_id", "unknown"),
                    result,
                )
            else:
                succeeded.append(result)

        if failed_count:
            logger.warning(
                "%d/%d agents failed to spawn",
                failed_count, len(agent_specs),
            )

        return succeeded
