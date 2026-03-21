"""
Contract-Based E2E Test
=======================
Tests the full pipeline: webhook -> parse -> orchestrate -> spawn -> result -> safety -> report

Stubs:
  - K8s JobSpawner: Instead of creating a pod, directly writes AgentResult to Redis
  - GitHub API client: Records PR creation calls for assertion

Real:
  - Orchestrator, SafetyPipeline, RedisState (fakeredis), SQLiteBackend
"""
import hashlib
import hmac
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from controller.config import Settings
from controller.models import TaskRequest, Thread, AgentResult, ThreadStatus, JobStatus
from controller.orchestrator import Orchestrator
from controller.state.redis_state import RedisState
from controller.integrations.registry import IntegrationRegistry
from controller.integrations.github import GitHubIntegration
from controller.integrations.slack import SlackIntegration
from controller.jobs.spawner import JobSpawner
from controller.jobs.monitor import JobMonitor
from controller.jobs.safety import SafetyPipeline

try:
    import fakeredis.aioredis
    from controller.state.sqlite import SQLiteBackend

    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

pytestmark = pytest.mark.skipif(not HAS_DEPS, reason="fakeredis or aiosqlite not installed")


# -- Fixtures --


@pytest.fixture
def settings():
    return Settings(
        anthropic_api_key="sk-test-contract-e2e",
        auto_open_pr=True,
        retry_on_empty_result=False,
        github_enabled=True,
        github_webhook_secret="contract-test-secret",
        github_allowed_orgs=["testorg"],
        slack_enabled=True,
        slack_signing_secret="slack-contract-secret",
        slack_bot_token="xoxb-contract",
        slack_bot_user_id="U_BOT",
    )


@pytest.fixture
async def db(tmp_path):
    return await SQLiteBackend.create(f"sqlite:///{tmp_path / 'contract_e2e.db'}")


@pytest.fixture
async def redis():
    return fakeredis.aioredis.FakeRedis()


@pytest.fixture
def redis_state(redis):
    return RedisState(redis)


@pytest.fixture
def mock_k8s():
    batch = MagicMock()
    batch.create_namespaced_job = MagicMock()
    batch.delete_namespaced_job = MagicMock()
    return batch


@pytest.fixture
def spawner(settings, mock_k8s):
    return JobSpawner(settings=settings, batch_api=mock_k8s, namespace="test")


@pytest.fixture
def monitor(redis_state, mock_k8s):
    return JobMonitor(redis_state=redis_state, batch_api=mock_k8s, namespace="test")


@pytest.fixture
def github_client():
    """Stub GitHub client that records calls."""
    client = AsyncMock()
    client.create_pr = AsyncMock(return_value="https://github.com/testorg/myrepo/pull/100")
    return client


@pytest.fixture
def registry(settings):
    reg = IntegrationRegistry()

    github = GitHubIntegration(
        webhook_secret=settings.github_webhook_secret,
        allowed_orgs=settings.github_allowed_orgs,
    )
    # Mock HTTP client on github integration for report_result
    github._client = AsyncMock()
    github._client.post = AsyncMock(return_value=MagicMock(status_code=201))

    slack = SlackIntegration(
        signing_secret=settings.slack_signing_secret,
        bot_token=settings.slack_bot_token,
        bot_user_id=settings.slack_bot_user_id,
    )
    slack._client = AsyncMock()
    slack._client.post = AsyncMock()

    reg.register(github)
    reg.register(slack)
    return reg


@pytest.fixture
def orchestrator(settings, db, redis_state, registry, spawner, monitor, github_client):
    return Orchestrator(
        settings=settings,
        state=db,
        redis_state=redis_state,
        registry=registry,
        spawner=spawner,
        monitor=monitor,
        github_client=github_client,
    )


# -- Helpers --


def sign_github_payload(payload: dict, secret: str) -> tuple[bytes, str]:
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, sig


async def simulate_agent_result(redis_state: RedisState, thread_id: str, result: dict):
    """Simulate the agent container writing its result to Redis."""
    await redis_state.push_result(thread_id, result)


# -- Contract Validators --


def assert_task_context_contract(context: dict):
    """Validate the task context written to Redis honors Contract 5."""
    required = {"task", "system_prompt", "repo_url", "branch"}
    assert required.issubset(context.keys()), f"Missing: {required - context.keys()}"
    assert context["repo_url"].startswith("https://github.com/")
    assert context["repo_url"].endswith(".git")
    assert all(isinstance(v, str) for v in context.values()), "All values must be strings"


def assert_agent_result_contract(result: AgentResult):
    """Validate AgentResult honors Contract 7."""
    assert isinstance(result.branch, str) and len(result.branch) > 0
    assert isinstance(result.exit_code, int)
    assert isinstance(result.commit_count, int) and result.commit_count >= 0
    assert isinstance(result.stderr, str)


# -- Tests --


class TestContractE2E:
    """
    Full pipeline contract test:
    1. Parse GitHub webhook (Contract 1)
    2. Orchestrator creates thread + spawns job (Contracts 2, 3, 4)
    3. Task context written to Redis (Contract 5)
    4. Agent writes result to Redis (Contract 6)
    5. Monitor reads result (Contract 7)
    6. SafetyPipeline creates PR + reports (Contracts 8, 9, 10)
    7. Queued messages are drained (Contract 11)
    """

    async def test_github_issue_full_pipeline(
        self, orchestrator, db, redis_state, mock_k8s, settings, github_client, registry,
    ):
        # -- Step 1: Parse webhook (Contract 1) --
        github = registry.get("github")
        payload = {
            "action": "created",
            "issue": {"number": 42, "title": "Fix auth", "body": "Auth is broken"},
            "comment": {
                "body": "please fix the authentication flow",
                "user": {"login": "developer", "type": "User"},
            },
            "repository": {
                "full_name": "testorg/myrepo",
                "name": "myrepo",
                "owner": {"login": "testorg"},
            },
        }
        body, sig = sign_github_payload(payload, settings.github_webhook_secret)
        mock_request = AsyncMock()
        mock_request.body = AsyncMock(return_value=body)
        mock_request.headers = {
            "x-github-event": "issue_comment",
            "x-hub-signature-256": sig,
        }
        task_req = await github.parse_webhook(mock_request)

        # Contract 1 validation
        assert task_req is not None
        assert task_req.source == "github"
        assert task_req.repo_owner == "testorg"
        assert task_req.repo_name == "myrepo"
        assert len(task_req.thread_id) == 64  # SHA256 hex

        # -- Step 2: Orchestrator handles task (Contracts 2, 3, 4) --
        await orchestrator.handle_task(task_req)

        # Contract 3: Thread created in state
        thread = await db.get_thread(task_req.thread_id)
        assert thread is not None
        assert thread.source == "github"
        assert thread.status == ThreadStatus.RUNNING

        # Contract 4: K8s Job was created
        mock_k8s.create_namespaced_job.assert_called_once()

        # -- Step 3: Validate task context in Redis (Contract 5) --
        task_context = await redis_state.get_task(task_req.thread_id)
        assert task_context is not None
        assert_task_context_contract(task_context)
        assert "authentication flow" in task_context["task"]

        # -- Step 4: Simulate agent writing result (Contract 6) --
        agent_output = {
            "branch": "df/github/abc12345",
            "exit_code": 0,
            "commit_count": 3,
            "stderr": "",
        }
        await simulate_agent_result(redis_state, task_req.thread_id, agent_output)

        # -- Step 5: Monitor reads result (Contract 7) --
        result = await orchestrator._monitor.wait_for_result(
            task_req.thread_id, timeout=5, poll_interval=0.1,
        )
        assert result is not None
        assert_agent_result_contract(result)
        assert result.commit_count == 3
        assert result.exit_code == 0

        # -- Step 6: SafetyPipeline processes result (Contracts 8, 9, 10) --
        integration = registry.get("github")
        pipeline = SafetyPipeline(
            settings=settings,
            state_backend=db,
            redis_state=redis_state,
            integration=integration,
            spawner=AsyncMock(),
            github_client=github_client,
        )
        await pipeline.process(thread, result)

        # Contract 9: PR was created
        github_client.create_pr.assert_called_once_with(
            owner="testorg", repo="myrepo", branch="df/github/abc12345",
        )
        assert result.pr_url == "https://github.com/testorg/myrepo/pull/100"

        # Contract 10: Result reported to integration
        integration._client.post.assert_called()

        # Contract 3: Thread back to IDLE
        thread = await db.get_thread(task_req.thread_id)
        assert thread.status == ThreadStatus.IDLE

    async def test_concurrent_message_queuing(
        self, orchestrator, db, redis_state, mock_k8s,
    ):
        """Contract 11: Second message during active job gets queued, then drained."""
        # First task spawns a job
        task1 = TaskRequest(
            thread_id="a" * 64,
            source="github",
            source_ref={"type": "issue_comment", "number": 1},
            repo_owner="testorg",
            repo_name="myrepo",
            task="first task",
        )
        await orchestrator.handle_task(task1)

        # Verify job spawned
        thread = await db.get_thread(task1.thread_id)
        assert thread.status == ThreadStatus.RUNNING
        mock_k8s.create_namespaced_job.assert_called_once()

        # Second task should be queued (Contract 11: enqueue)
        task2 = TaskRequest(
            thread_id="a" * 64,
            source="github",
            source_ref={"type": "issue_comment", "number": 1},
            repo_owner="testorg",
            repo_name="myrepo",
            task="follow-up task",
        )
        await orchestrator.handle_task(task2)

        # Should NOT have spawned a second job
        mock_k8s.create_namespaced_job.assert_called_once()  # Still just once

        # Simulate completion and drain (Contract 11: drain)
        await simulate_agent_result(redis_state, task1.thread_id, {
            "branch": "df/test/x",
            "exit_code": 0,
            "commit_count": 1,
            "stderr": "",
        })

        result = await orchestrator._monitor.wait_for_result(
            task1.thread_id, timeout=2, poll_interval=0.1,
        )
        assert result is not None

        pipeline = SafetyPipeline(
            settings=orchestrator._settings,
            state_backend=db,
            redis_state=redis_state,
            integration=AsyncMock(),
            spawner=AsyncMock(),
            github_client=AsyncMock(),
        )
        await pipeline.process(thread, result)

        # Contract 11: drain verified -- thread is IDLE and queue is empty
        thread = await db.get_thread(task1.thread_id)
        assert thread.status == ThreadStatus.IDLE
        remaining = await redis_state.drain_messages(task1.thread_id)
        assert remaining == []  # Already drained by pipeline

    async def test_failed_agent_result_pipeline(
        self, orchestrator, db, redis_state, mock_k8s, settings, registry,
    ):
        """Contract 8: Failed agent result is reported correctly."""
        task = TaskRequest(
            thread_id="b" * 64,
            source="github",
            source_ref={"type": "issue_comment", "number": 5},
            repo_owner="testorg",
            repo_name="myrepo",
            task="implement feature",
        )
        await orchestrator.handle_task(task)

        # Agent fails
        await simulate_agent_result(redis_state, task.thread_id, {
            "branch": "df/github/fail",
            "exit_code": 1,
            "commit_count": 0,
            "stderr": "Error: could not compile",
        })

        result = await orchestrator._monitor.wait_for_result(
            task.thread_id, timeout=2, poll_interval=0.1,
        )
        assert result is not None
        assert result.exit_code == 1

        thread = await db.get_thread(task.thread_id)
        integration = AsyncMock()
        github_client = AsyncMock()

        pipeline = SafetyPipeline(
            settings=settings,
            state_backend=db,
            redis_state=redis_state,
            integration=integration,
            spawner=AsyncMock(),
            github_client=github_client,
        )
        await pipeline.process(thread, result)

        # Contract 9: No PR created for failed result
        github_client.create_pr.assert_not_called()

        # Contract 10: Result still reported
        integration.report_result.assert_called_once()
        reported = integration.report_result.call_args[0][1]
        assert reported.exit_code == 1
        assert "could not compile" in reported.stderr
