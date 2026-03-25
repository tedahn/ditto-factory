# controller/src/controller/swarm/watchdog.py
"""Scheduling Watchdog — detects unschedulable agents and adjusts peer expectations."""
from __future__ import annotations

import logging
import time

from controller.models import (
    SwarmGroup, AgentStatus,
)
from controller.state.protocol import StateBackend
from controller.swarm.redis_streams import SwarmRedisStreams

logger = logging.getLogger(__name__)


class SchedulingWatchdog:
    """Detects K8s Jobs that fail to schedule and adjusts swarm expectations.

    Prevents swarm_wait_for_peers deadlocks by:
    1. Checking pending agents for FailedScheduling conditions
    2. Marking them as FAILED after a grace period
    3. Publishing peer_count_adjusted control messages
    """

    def __init__(
        self,
        core_api,  # kubernetes.client.CoreV1Api
        state: StateBackend,
        redis_streams: SwarmRedisStreams,
        namespace: str = "default",
        grace_seconds: int = 120,
    ):
        self._core_api = core_api
        self._state = state
        self._streams = redis_streams
        self._namespace = namespace
        self._grace_seconds = grace_seconds
        self._first_seen: dict[str, float] = {}  # agent_id -> first detection time

    async def check_group(self, group: SwarmGroup) -> None:
        """Check all pending agents in a group for scheduling failures."""
        agents = await self._state.list_swarm_agents(group.id)
        pending = [a for a in agents if a.status == AgentStatus.PENDING and a.k8s_job_name]

        if not pending:
            return

        for agent in pending:
            is_unschedulable = self._check_pod_schedulable(agent.k8s_job_name)
            if not is_unschedulable:
                # Pod is fine or not found yet — clear tracking
                self._first_seen.pop(agent.id, None)
                continue

            # Track first detection time
            now = time.monotonic()
            if agent.id not in self._first_seen:
                self._first_seen[agent.id] = now
                logger.info(
                    "Agent %s (%s) detected as unschedulable, starting grace period",
                    agent.id, agent.k8s_job_name,
                )

            elapsed = now - self._first_seen[agent.id]
            if elapsed < self._grace_seconds:
                continue

            # Grace period expired — mark as failed
            logger.warning(
                "Agent %s failed to schedule after %ds, marking as FAILED",
                agent.id, int(elapsed),
            )
            await self._state.update_swarm_agent(
                group.id, agent.id, AgentStatus.FAILED,
                result_summary={"error": "FailedScheduling", "reason": "insufficient_resources"},
            )
            self._first_seen.pop(agent.id, None)

            # Publish peer_count_adjusted control message
            active_count = sum(
                1 for a in agents
                if a.id != agent.id and a.status not in (AgentStatus.FAILED, AgentStatus.LOST)
            )
            control_msg = {
                "action": "peer_count_adjusted",
                "original_count": len(agents),
                "adjusted_count": active_count,
                "failed_agent": agent.id,
                "reason": "insufficient_resources",
            }
            await self._streams.send_control(group.id, control_msg)

    def _check_pod_schedulable(self, job_name: str) -> bool:
        """Check if a K8s Job's Pod has FailedScheduling condition.

        Returns True if unschedulable, False otherwise.
        """
        try:
            pods = self._core_api.list_namespaced_pod(
                namespace=self._namespace,
                label_selector=f"job-name={job_name}",
            )
            for pod in pods.items:
                if not pod.status or not pod.status.conditions:
                    continue
                for condition in pod.status.conditions:
                    if (
                        condition.type == "PodScheduled"
                        and condition.status == "False"
                        and condition.reason in ("Unschedulable", "FailedScheduling")
                    ):
                        return True
            return False
        except Exception:
            logger.warning("Failed to check pod status for %s", job_name, exc_info=True)
            return False
