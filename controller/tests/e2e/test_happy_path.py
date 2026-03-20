"""
E2E Happy Path Tests — Full pipeline with real orchestrator, real state, fake Redis.
Tests: webhook parse → orchestrator → job spawn → result → safety → report
"""
import hashlib
import hmac
import json
import time
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from controller.config import Settings
from controller.models import TaskRequest, Thread, Job, AgentResult, ThreadStatus, JobStatus
from controller.orchestrator import Orchestrator
from controller.state.redis_state import RedisState
from controller.integrations.registry import IntegrationRegistry
from controller.integrations.slack import SlackIntegration
from controller.integrations.linear import LinearIntegration
from controller.integrations.github import GitHubIntegration
from controller.integrations.thread_id import derive_thread_id
from controller.jobs.spawner import JobSpawner
from controller.jobs.monitor import JobMonitor
from controller.jobs.safety import SafetyPipeline

try:
    import fakeredis.aioredis
    HAS_FAKEREDIS = True
except ImportError:
    HAS_FAKEREDIS = False

try:
    from controller.state.sqlite import SQLiteBackend
    HAS_SQLITE = True
except ImportError:
    HAS_SQLITE = False

pytestmark = [
    pytest.mark.skipif(not HAS_FAKEREDIS, reason="fakeredis not installed"),
    pytest.mark.skipif(not HAS_SQLITE, reason="aiosqlite not installed"),
]


# ─── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def settings():
    return Settings(
        anthropic_api_key="sk-test-e2e",
        auto_open_pr=True,
        retry_on_empty_result=True,
        max_empty_retries=1,
        slack_enabled=True,
        slack_signing_secret="slack-e2e-secret",
        slack_bot_token="xoxb-e2e",
        slack_bot_user_id="U_BOT_E2E",
        github_enabled=True,
        github_webhook_secret="gh-e2e-secret",
        github_allowed_orgs=["testorg"],
        linear_enabled=True,
        linear_webhook_secret="linear-e2e-secret",
        linear_api_key="lin_e2e_key",
    )


@pytest.fixture
async def db(tmp_path):
    path = str(tmp_path / f"e2e_{uuid.uuid4().hex[:8]}.db")
    backend = await SQLiteBackend.create(f"sqlite:///{path}")
    return backend


@pytest.fixture
async def redis():
    return fakeredis.aioredis.FakeRedis()


@pytest.fixture
def redis_state(redis):
    return RedisState(redis)


@pytest.fixture
def mock_k8s():
    batch = MagicMock()
    batch.create_namespaced_job = MagicMock(return_value=MagicMock(metadata=MagicMock(name="df-test-job")))
    batch.delete_namespaced_job = MagicMock()
    return batch


@pytest.fixture
def spawner(settings, mock_k8s):
    return JobSpawner(settings=settings, batch_api=mock_k8s, namespace="test")


@pytest.fixture
def monitor(redis_state, mock_k8s):
    return JobMonitor(redis_state=redis_state, batch_api=mock_k8s, namespace="test")


@pytest.fixture
def registry(settings):
    reg = IntegrationRegistry()
    slack = SlackIntegration(
        signing_secret=settings.slack_signing_secret,
        bot_token=settings.slack_bot_token,
        bot_user_id=settings.slack_bot_user_id,
    )
    # Mock the httpx client so no real HTTP calls are made
    slack._client = AsyncMock()
    slack._client.post = AsyncMock()
    slack._client.get = AsyncMock()
    reg.register(slack)
    return reg


@pytest.fixture
def orchestrator(settings, db, redis_state, registry, spawner, monitor):
    return Orchestrator(
        settings=settings,
        state=db,
        redis_state=redis_state,
        registry=registry,
        spawner=spawner,
        monitor=monitor,
    )


# ─── Helpers ─────────────────────────────────────────────────────────

def make_slack_payload(text, channel="C_E2E", thread_ts="100.000", user="U_HUMAN"):
    return {
        "event": {
            "type": "message",
            "text": text,
            "user": user,
            "channel": channel,
            "ts": str(time.time()),
            "thread_ts": thread_ts,
        }
    }


def sign_slack(payload, secret="slack-e2e-secret"):
    body = json.dumps(payload).encode()
    ts = str(int(time.time()))
    sig_base = f"v0:{ts}:{body.decode()}"
    sig = "v0=" + hmac.new(secret.encode(), sig_base.encode(), hashlib.sha256).hexdigest()
    return body, ts, sig


def make_github_issue_comment_payload(comment_body, org="testorg", repo="testrepo", number=1):
    """GitHub issue_comment webhook payload (action=created)."""
    return {
        "action": "created",
        "comment": {"body": comment_body, "user": {"login": "humanuser"}},
        "issue": {"number": number, "title": "Test Issue"},
        "repository": {
            "owner": {"login": org},
            "name": repo,
            "full_name": f"{org}/{repo}",
        },
    }


def sign_github(payload, secret="gh-e2e-secret"):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, sig


def make_linear_payload(comment_body, issue_id="ENG-42", team_key="ENG"):
    return {
        "type": "Comment",
        "action": "create",
        "data": {
            "body": comment_body,
            "user": {"isBot": False, "name": "Tester"},
            "issue": {
                "identifier": issue_id,
                "title": "Test Issue",
                "team": {"key": team_key},
            },
        },
    }


def sign_linear(payload, secret="linear-e2e-secret"):
    body = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, sig


# ─── Test: Slack Happy Path ──────────────────────────────────────────

class TestSlackHappyPath:
    """Complete Slack flow: webhook → orchestrator → spawn → result → report."""

    async def test_slack_webhook_to_job_spawn(self, orchestrator, db, redis_state, mock_k8s, settings):
        """Slack message → thread created → task in Redis → K8s Job spawned."""
        slack = SlackIntegration(
            signing_secret=settings.slack_signing_secret,
            bot_token=settings.slack_bot_token,
            bot_user_id=settings.slack_bot_user_id,
        )

        payload = make_slack_payload("fix the auth bug", channel="C_E2E", thread_ts="100.000")
        body, ts, sig = sign_slack(payload)

        mock_request = AsyncMock()
        mock_request.body = AsyncMock(return_value=body)
        mock_request.headers = {
            "x-slack-request-timestamp": ts,
            "x-slack-signature": sig,
        }

        task_req = await slack.parse_webhook(mock_request)
        assert task_req is not None
        assert task_req.task == "fix the auth bug"
        assert task_req.source == "slack"
        # Slack doesn't resolve repo — set them manually
        task_req.repo_owner = "testorg"
        task_req.repo_name = "testrepo"

        await orchestrator.handle_task(task_req)

        # Verify thread created
        thread = await db.get_thread(task_req.thread_id)
        assert thread is not None
        assert thread.status == ThreadStatus.RUNNING
        assert thread.source == "slack"

        # Verify K8s Job was spawned
        mock_k8s.create_namespaced_job.assert_called_once()
        job_call = mock_k8s.create_namespaced_job.call_args
        assert job_call.kwargs["namespace"] == "test"

        # Verify task pushed to Redis
        task_data = await redis_state.get_task(task_req.thread_id)
        assert task_data is not None
        assert "fix the auth bug" in task_data["task"]

    async def test_slack_result_to_report(self, settings, db, redis_state):
        """Agent result → safety pipeline → Slack message posted."""
        thread = Thread(
            id="slack-e2e-result",
            source="slack",
            source_ref={"channel": "C_E2E", "thread_ts": "100.000"},
            repo_owner="testorg",
            repo_name="testrepo",
            status=ThreadStatus.RUNNING,
        )
        await db.upsert_thread(thread)

        result = AgentResult(
            branch="df/slack-e2e/abc123",
            exit_code=0,
            commit_count=5,
            pr_url="https://github.com/testorg/testrepo/pull/42",
        )

        # Use a real SlackIntegration but mock its HTTP client
        slack = SlackIntegration(
            signing_secret=settings.slack_signing_secret,
            bot_token=settings.slack_bot_token,
            bot_user_id=settings.slack_bot_user_id,
        )
        mock_post = AsyncMock()
        slack._client = AsyncMock()
        slack._client.post = mock_post

        mock_gh_client = AsyncMock()
        # result already has pr_url so create_pr should NOT be called
        pipeline = SafetyPipeline(
            settings=settings,
            state_backend=db,
            redis_state=redis_state,
            integration=slack,
            spawner=AsyncMock(),
            github_client=mock_gh_client,
        )

        await pipeline.process(thread, result)

        # Verify Slack API was called to post a message
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs.kwargs["json"]["channel"] == "C_E2E"
        assert "pull/42" in call_kwargs.kwargs["json"]["text"]

        # Verify thread reset to IDLE
        updated = await db.get_thread("slack-e2e-result")
        assert updated.status == ThreadStatus.IDLE


# ─── Test: GitHub Happy Path ─────────────────────────────────────────

class TestGitHubHappyPath:
    """Complete GitHub flow: issue comment → orchestrator → spawn → result → comment."""

    async def test_github_issue_comment_to_job_spawn(self, orchestrator, db, redis_state, mock_k8s, settings):
        """GitHub issue comment → parse → orchestrator → K8s Job."""
        github = GitHubIntegration(
            webhook_secret=settings.github_webhook_secret,
            allowed_orgs=settings.github_allowed_orgs,
        )

        payload = make_github_issue_comment_payload("please fix this regression")
        body, sig = sign_github(payload)

        mock_request = AsyncMock()
        mock_request.body = AsyncMock(return_value=body)
        mock_request.headers = {
            "x-github-event": "issue_comment",
            "x-hub-signature-256": sig,
        }

        task_req = await github.parse_webhook(mock_request)
        assert task_req is not None
        assert task_req.task == "please fix this regression"
        assert task_req.repo_owner == "testorg"
        assert task_req.source == "github"

        await orchestrator.handle_task(task_req)

        thread = await db.get_thread(task_req.thread_id)
        assert thread is not None
        assert thread.status == ThreadStatus.RUNNING
        mock_k8s.create_namespaced_job.assert_called_once()

    async def test_github_auto_pr_creation(self, settings, db, redis_state):
        """Result with commits but no PR → auto-creates PR via SafetyPipeline."""
        thread = Thread(
            id="gh-e2e-pr",
            source="github",
            source_ref={"type": "issue_comment", "number": 42, "is_pr": False},
            repo_owner="testorg",
            repo_name="testrepo",
            status=ThreadStatus.RUNNING,
        )
        await db.upsert_thread(thread)

        # No pr_url — pipeline should call create_pr
        result = AgentResult(branch="df/gh-e2e/xyz", exit_code=0, commit_count=2)
        mock_gh_client = AsyncMock()
        mock_gh_client.create_pr = AsyncMock(
            return_value="https://github.com/testorg/testrepo/pull/99"
        )
        mock_integration = AsyncMock()

        pipeline = SafetyPipeline(
            settings=settings,
            state_backend=db,
            redis_state=redis_state,
            integration=mock_integration,
            spawner=AsyncMock(),
            github_client=mock_gh_client,
        )

        await pipeline.process(thread, result)

        mock_gh_client.create_pr.assert_called_once()
        # Verify PR URL was set on result before reporting
        reported_result = mock_integration.report_result.call_args[0][1]
        assert reported_result.pr_url == "https://github.com/testorg/testrepo/pull/99"


# ─── Test: Linear Happy Path ─────────────────────────────────────────

class TestLinearHappyPath:
    """Complete Linear flow: comment webhook → orchestrator → spawn."""

    async def test_linear_comment_to_job_spawn(self, orchestrator, db, redis_state, mock_k8s, settings):
        """Linear comment → parse → orchestrator → K8s Job."""
        linear = LinearIntegration(
            webhook_secret=settings.linear_webhook_secret,
            api_key=settings.linear_api_key,
            team_repo_map={"ENG": ("testorg", "testrepo")},
        )

        payload = make_linear_payload("fix the performance regression", issue_id="ENG-42")
        body, sig = sign_linear(payload)

        mock_request = AsyncMock()
        mock_request.body = AsyncMock(return_value=body)
        mock_request.headers = {"linear-signature": sig}

        task_req = await linear.parse_webhook(mock_request)
        assert task_req is not None
        assert "fix the performance regression" in task_req.task
        assert task_req.repo_owner == "testorg"
        assert task_req.source == "linear"

        await orchestrator.handle_task(task_req)

        thread = await db.get_thread(task_req.thread_id)
        assert thread is not None
        assert thread.status == ThreadStatus.RUNNING
        mock_k8s.create_namespaced_job.assert_called_once()


# ─── Test: Cross-Integration Thread ID Isolation ─────────────────────

class TestThreadIsolation:
    """Verify that same identifiers from different sources get different threads."""

    async def test_same_number_different_sources(self, orchestrator, db, mock_k8s):
        """Issue #42 on GitHub vs ENG-42 on Linear → different threads."""
        gh_thread_id = derive_thread_id(
            "github_issue", repo_owner="org", repo_name="repo", issue_number=42
        )
        linear_thread_id = derive_thread_id("linear", issue_id="ENG-42")

        gh_task = TaskRequest(
            thread_id=gh_thread_id,
            source="github",
            source_ref={"type": "issue_comment", "number": 42, "is_pr": False},
            repo_owner="org",
            repo_name="repo",
            task="fix from github",
        )
        linear_task = TaskRequest(
            thread_id=linear_thread_id,
            source="linear",
            source_ref={"issue_id": "ENG-42"},
            repo_owner="org",
            repo_name="repo",
            task="fix from linear",
        )

        await orchestrator.handle_task(gh_task)
        await orchestrator.handle_task(linear_task)

        gh_thread = await db.get_thread(gh_thread_id)
        linear_thread = await db.get_thread(linear_thread_id)

        assert gh_thread is not None
        assert linear_thread is not None
        assert gh_thread.id != linear_thread.id
        assert gh_thread.source == "github"
        assert linear_thread.source == "linear"
