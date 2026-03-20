from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone
from controller.config import Settings
from controller.models import TaskRequest, Thread, Job, ThreadStatus, JobStatus
from controller.state.protocol import StateBackend
from controller.state.redis_state import RedisState
from controller.integrations.protocol import Integration
from controller.integrations.registry import IntegrationRegistry
from controller.prompt.builder import build_system_prompt
from controller.jobs.spawner import JobSpawner
from controller.jobs.monitor import JobMonitor
from controller.jobs.safety import SafetyPipeline

logger = logging.getLogger(__name__)

class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        state: StateBackend,
        redis_state: RedisState,
        registry: IntegrationRegistry,
        spawner: JobSpawner,
        monitor: JobMonitor,
        github_client=None,
    ):
        self._settings = settings
        self._state = state
        self._redis = redis_state
        self._registry = registry
        self._spawner = spawner
        self._monitor = monitor
        self._github_client = github_client

    async def handle_task(self, task_request: TaskRequest) -> None:
        thread_id = task_request.thread_id
        logger.info("Handling task for thread %s from %s", thread_id, task_request.source)

        # RESOLVE: Get or create thread
        thread = await self._state.get_thread(thread_id)
        if thread is None:
            thread = Thread(
                id=thread_id,
                source=task_request.source,
                source_ref=task_request.source_ref,
                repo_owner=task_request.repo_owner,
                repo_name=task_request.repo_name,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            await self._state.upsert_thread(thread)

        # CHECK: Is there an active job?
        active_job = await self._state.get_active_job_for_thread(thread_id)
        if active_job is not None:
            logger.info("Thread %s has active job %s, queuing message", thread_id, active_job.k8s_job_name)
            await self._redis.queue_message(thread_id, task_request.task)
            return

        # LOCK: Try to acquire advisory lock
        if not await self._state.try_acquire_lock(thread_id):
            logger.info("Thread %s is locked, queuing message", thread_id)
            await self._redis.queue_message(thread_id, task_request.task)
            return

        try:
            await self._spawn_job(thread, task_request)
        finally:
            await self._state.release_lock(thread_id)

    async def _spawn_job(
        self,
        thread: Thread,
        task_request: TaskRequest,
        is_retry: bool = False,
        retry_count: int = 0,
    ) -> None:
        thread_id = thread.id

        # PREPARE: Build system prompt
        integration = self._registry.get(task_request.source)
        claude_md = ""  # TODO: fetch CLAUDE.md from repo
        conversation_history = await self._state.get_conversation(
            thread_id, limit=self._settings.conversation_history_limit
        )
        conversation_strs = [
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in conversation_history
        ]

        system_prompt = build_system_prompt(
            repo_owner=thread.repo_owner,
            repo_name=thread.repo_name,
            task=task_request.task,
            claude_md=claude_md,
            conversation=conversation_strs if conversation_strs else None,
            is_retry=is_retry,
        )

        # Store conversation
        await self._state.append_conversation(thread_id, {
            "role": "user",
            "content": task_request.task,
            "source": task_request.source,
        })

        # Create branch name
        short_id = thread_id[:8]
        branch = f"df/{short_id}/{uuid.uuid4().hex[:8]}"

        # Push task to Redis
        await self._redis.push_task(thread_id, {
            "task": task_request.task,
            "system_prompt": system_prompt,
            "repo_url": f"https://github.com/{thread.repo_owner}/{thread.repo_name}.git",
            "branch": branch,
        })

        # SPAWN: Create K8s Job
        job_name = self._spawner.spawn(
            thread_id=thread_id,
            github_token="",  # TODO: get from GitHub App installation
            redis_url=self._settings.redis_url,
        )

        # Track job in state
        job = Job(
            id=uuid.uuid4().hex,
            thread_id=thread_id,
            k8s_job_name=job_name,
            status=JobStatus.RUNNING,
            task_context={"task": task_request.task, "branch": branch},
            started_at=datetime.now(timezone.utc),
        )
        await self._state.create_job(job)
        await self._state.update_thread_status(thread_id, ThreadStatus.RUNNING, job_name=job_name)

        logger.info("Spawned job %s for thread %s", job_name, thread_id)

    async def handle_job_completion(self, thread_id: str) -> None:
        """Called when a job completes (via monitor or webhook)."""
        thread = await self._state.get_thread(thread_id)
        if thread is None:
            logger.error("Thread %s not found for job completion", thread_id)
            return

        result = await self._monitor.wait_for_result(thread_id, timeout=60, poll_interval=1.0)
        if result is None:
            logger.error("No result found for thread %s", thread_id)
            return

        integration = self._registry.get(thread.source)
        if integration is None:
            logger.error("No integration found for source %s", thread.source)
            return

        pipeline = SafetyPipeline(
            settings=self._settings,
            state_backend=self._state,
            redis_state=self._redis,
            integration=integration,
            spawner=self._spawn_job,
            github_client=self._github_client,
        )

        await pipeline.process(thread, result)
