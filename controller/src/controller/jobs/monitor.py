from __future__ import annotations
import asyncio
import logging
from kubernetes import client as k8s
from controller.models import AgentResult
from controller.state.redis_state import RedisState

logger = logging.getLogger(__name__)

class JobMonitor:
    def __init__(self, redis_state: RedisState, batch_api: k8s.BatchV1Api, namespace: str = "default"):
        self._redis = redis_state
        self._batch_api = batch_api
        self._namespace = namespace

    async def wait_for_result(self, thread_id: str, timeout: int = 1800, poll_interval: float = 5.0) -> AgentResult | None:
        elapsed = 0.0
        while elapsed < timeout:
            result_data = await self._redis.get_result(thread_id)
            if result_data is not None:
                return AgentResult(
                    branch=result_data.get("branch", ""),
                    exit_code=int(result_data.get("exit_code", 1)),
                    commit_count=int(result_data.get("commit_count", 0)),
                    stderr=result_data.get("stderr", ""),
                    result=result_data.get("result"),
                )
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        return None

    def is_job_running(self, job_name: str) -> bool:
        try:
            job = self._batch_api.read_namespaced_job(name=job_name, namespace=self._namespace)
            if job.status.active and job.status.active > 0:
                return True
            return False
        except k8s.ApiException:
            return False
