"""Subagent spawn handler.

Listens for spawn requests from running agents via Redis pubsub.
Classifies the subtask, spawns a child K8s job, and posts the result.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from controller.config import Settings
from controller.state.redis_state import RedisState
from controller.jobs.spawner import JobSpawner

logger = logging.getLogger(__name__)


class SubagentHandler:
    """Handles subagent spawn requests published by parent agents via Redis."""

    def __init__(
        self,
        settings: Settings,
        redis_state: RedisState,
        spawner: JobSpawner,
        classifier=None,
        injector=None,
        state=None,
    ):
        self._settings = settings
        self._redis = redis_state
        self._spawner = spawner
        self._classifier = classifier
        self._injector = injector
        self._state = state
        self._running = False

    async def start(self) -> None:
        """Start listening for subagent spawn requests on Redis pubsub."""
        self._running = True
        logger.info("SubagentHandler started, listening for spawn requests")

        pubsub = self._redis._redis.pubsub()
        await pubsub.subscribe("subagent_requests")

        try:
            while self._running:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message and message["type"] == "message":
                    request_id = message["data"]
                    if isinstance(request_id, bytes):
                        request_id = request_id.decode()
                    asyncio.create_task(self._handle_spawn(request_id))
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe("subagent_requests")
            await pubsub.aclose()

    async def stop(self) -> None:
        """Signal the listener loop to exit."""
        self._running = False

    async def _handle_spawn(self, request_id: str) -> None:
        """Handle a single subagent spawn request."""
        try:
            raw = await self._redis._redis.get(f"subagent_request:{request_id}")
            if not raw:
                logger.warning("Subagent request %s not found in Redis", request_id)
                return

            request = json.loads(raw)
            parent_thread_id = request["parent_thread_id"]
            task = request["task"]
            agent_type_hint = request.get("agent_type_hint", "")

            logger.info(
                "Spawning subagent for parent %s: %.100s",
                parent_thread_id,
                task,
            )

            # Classify subtask and select skills when classifier is available
            skills_payload: list = []
            agent_image = self._settings.agent_image

            if self._classifier and self._settings.skill_registry_enabled:
                try:
                    classification = await self._classifier.classify(task=task)
                    if self._injector:
                        skills_payload = self._injector.format_for_redis(
                            classification.skills
                        )
                    agent_type = agent_type_hint or classification.agent_type
                except Exception:
                    logger.exception("Subagent classification failed, using defaults")
                    agent_type = agent_type_hint or "general"
            else:
                agent_type = agent_type_hint or "general"

            # Resolve parent branch and repo from parent task in Redis
            parent_task_raw = await self._redis._redis.get(
                f"task:{parent_thread_id}"
            )
            parent_branch = "main"
            parent_repo_url = ""
            if parent_task_raw:
                parent_task = json.loads(parent_task_raw)
                parent_branch = parent_task.get("branch", "main")
                parent_repo_url = parent_task.get("repo_url", "")

            # Create child thread ID
            child_thread_id = (
                f"{parent_thread_id}-sub-{uuid.uuid4().hex[:8]}"
            )

            # Push child task to Redis
            child_task = {
                "task": task,
                "system_prompt": (
                    "You are a subagent spawned by a parent agent. "
                    "Complete this subtask and commit your changes to the branch. "
                    "Do NOT push the branch - the parent agent will handle that.\n\n"
                    f"Subtask: {task}"
                ),
                "repo_url": parent_repo_url,
                "branch": parent_branch,
                "skills": skills_payload,
                "is_subagent": True,
                "parent_thread_id": parent_thread_id,
            }
            await self._redis._redis.set(
                f"task:{child_thread_id}",
                json.dumps(child_task),
                ex=3600,
            )

            # Spawn K8s job with SUBAGENT_DEPTH=1 to prevent recursive spawning
            job_name = self._spawner.spawn(
                thread_id=child_thread_id,
                github_token="",  # inherited via K8s secret
                redis_url=self._settings.redis_url,
                agent_image=agent_image,
                extra_env={"SUBAGENT_DEPTH": "1"},
            )

            logger.info(
                "Spawned subagent job %s for request %s", job_name, request_id
            )

            # Poll for child result with timeout
            timeout = self._settings.subagent_timeout_seconds
            start = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - start < timeout:
                result_raw = await self._redis._redis.get(
                    f"result:{child_thread_id}"
                )
                if result_raw:
                    # Forward result to parent via the subagent_result key
                    await self._redis._redis.set(
                        f"subagent_result:{request_id}",
                        result_raw,
                        ex=3600,
                    )
                    logger.info("Subagent %s completed successfully", request_id)
                    self._cleanup_job(job_name)
                    return

                await asyncio.sleep(5)

            # Timeout: post error result so parent does not hang
            await self._redis._redis.set(
                f"subagent_result:{request_id}",
                json.dumps(
                    {
                        "branch": parent_branch,
                        "exit_code": 1,
                        "commit_count": 0,
                        "stderr": "Subagent timed out",
                    }
                ),
                ex=3600,
            )
            logger.warning("Subagent %s timed out after %ds", request_id, timeout)
            self._cleanup_job(job_name)

        except Exception:
            logger.exception("Failed to handle subagent spawn %s", request_id)
            # Post error result so the parent agent does not block forever
            try:
                await self._redis._redis.set(
                    f"subagent_result:{request_id}",
                    json.dumps(
                        {
                            "branch": "unknown",
                            "exit_code": 1,
                            "commit_count": 0,
                            "stderr": "Subagent spawn failed",
                        }
                    ),
                    ex=3600,
                )
            except Exception:
                logger.exception("Failed to post error result for %s", request_id)

    def _cleanup_job(self, job_name: str) -> None:
        """Best-effort cleanup of a completed/timed-out K8s job."""
        try:
            self._spawner.delete(job_name)
        except Exception:
            logger.debug("Failed to delete job %s (may already be gone)", job_name)
