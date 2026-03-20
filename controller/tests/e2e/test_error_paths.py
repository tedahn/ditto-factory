"""
E2E Error Path Tests — Retry logic, invalid webhooks, failures.
"""
import hashlib
import hmac
import json
import time
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock

from controller.config import Settings
from controller.models import TaskRequest, Thread, Job, AgentResult, ThreadStatus, JobStatus
from controller.orchestrator import Orchestrator
from controller.state.redis_state import RedisState
from controller.integrations.registry import IntegrationRegistry
from controller.integrations.slack import SlackIntegration
from controller.integrations.github import GitHubIntegration
from controller.integrations.linear import LinearIntegration
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


@pytest.fixture
def settings():
    return Settings(
        anthropic_api_key="sk-test",
        auto_open_pr=True,
        retry_on_empty_result=True,
        max_empty_retries=1,
    )

@pytest.fixture
async def db(tmp_path):
    path = str(tmp_path / f"e2e_{uuid.uuid4().hex[:8]}.db")
    return await SQLiteBackend.create(f"sqlite:///{path}")

@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis()

@pytest.fixture
def redis_state(redis):
    return RedisState(redis)


# ─── Test: Anti-Stall Retry ──────────────────────────────────────────

class TestAntiStallRetry:

    async def test_empty_result_triggers_retry(self, settings, db, redis_state):
        """Agent exits 0 but 0 commits → retry spawned."""
        thread = Thread(
            id="retry-test-1", source="test", source_ref={},
            repo_owner="org", repo_name="repo", status=ThreadStatus.RUNNING,
        )
        await db.upsert_thread(thread)

        result = AgentResult(branch="df/retry/abc", exit_code=0, commit_count=0)
        mock_spawner = AsyncMock()
        mock_integration = AsyncMock()

        pipeline = SafetyPipeline(
            settings=settings, state_backend=db, redis_state=redis_state,
            integration=mock_integration, spawner=mock_spawner, github_client=AsyncMock(),
        )

        await pipeline.process(thread, result, retry_count=0)

        # Should retry, NOT report
        mock_spawner.assert_called_once()
        mock_integration.report_result.assert_not_called()

    async def test_max_retries_then_reports_failure(self, settings, db, redis_state):
        """After max retries, reports 'no changes' to integration."""
        thread = Thread(
            id="retry-max-test", source="test", source_ref={},
            repo_owner="org", repo_name="repo", status=ThreadStatus.RUNNING,
        )
        await db.upsert_thread(thread)

        result = AgentResult(branch="df/retry/abc", exit_code=0, commit_count=0)
        mock_spawner = AsyncMock()
        mock_integration = AsyncMock()

        pipeline = SafetyPipeline(
            settings=settings, state_backend=db, redis_state=redis_state,
            integration=mock_integration, spawner=mock_spawner, github_client=AsyncMock(),
        )

        await pipeline.process(thread, result, retry_count=1)  # already at max

        # Should report failure, NOT retry
        mock_spawner.assert_not_called()
        mock_integration.report_result.assert_called_once()
        reported_result = mock_integration.report_result.call_args[0][1]
        assert "no changes" in reported_result.stderr.lower()

    async def test_failed_exit_code_no_retry(self, settings, db, redis_state):
        """Non-zero exit code → report immediately, no retry regardless of commit count."""
        thread = Thread(
            id="fail-no-retry", source="test", source_ref={},
            repo_owner="org", repo_name="repo", status=ThreadStatus.RUNNING,
        )
        await db.upsert_thread(thread)

        result = AgentResult(branch="df/fail/abc", exit_code=1, commit_count=0, stderr="syntax error")
        mock_spawner = AsyncMock()
        mock_integration = AsyncMock()

        pipeline = SafetyPipeline(
            settings=settings, state_backend=db, redis_state=redis_state,
            integration=mock_integration, spawner=mock_spawner, github_client=AsyncMock(),
        )

        await pipeline.process(thread, result)

        mock_spawner.assert_not_called()
        mock_integration.report_result.assert_called_once()

    async def test_retry_disabled_reports_immediately(self, db, redis_state):
        """When retry_on_empty_result=False, reports immediately."""
        settings = Settings(
            anthropic_api_key="test",
            retry_on_empty_result=False,
        )
        thread = Thread(
            id="no-retry-test", source="test", source_ref={},
            repo_owner="org", repo_name="repo", status=ThreadStatus.RUNNING,
        )
        await db.upsert_thread(thread)

        result = AgentResult(branch="df/test/abc", exit_code=0, commit_count=0)
        mock_spawner = AsyncMock()
        mock_integration = AsyncMock()

        pipeline = SafetyPipeline(
            settings=settings, state_backend=db, redis_state=redis_state,
            integration=mock_integration, spawner=mock_spawner, github_client=AsyncMock(),
        )

        await pipeline.process(thread, result)

        mock_spawner.assert_not_called()
        mock_integration.report_result.assert_called_once()


# ─── Test: PR Auto-Creation ──────────────────────────────────────────

class TestPRAutoCreation:

    async def test_auto_pr_when_commits_no_url(self, settings, db, redis_state):
        """Commits exist but no PR URL → auto-create PR."""
        thread = Thread(
            id="pr-auto-test", source="test", source_ref={},
            repo_owner="org", repo_name="repo", status=ThreadStatus.RUNNING,
        )
        await db.upsert_thread(thread)

        result = AgentResult(branch="df/pr/abc", exit_code=0, commit_count=3)
        mock_gh = AsyncMock()
        mock_gh.create_pr = AsyncMock(return_value="https://github.com/org/repo/pull/1")

        pipeline = SafetyPipeline(
            settings=settings, state_backend=db, redis_state=redis_state,
            integration=AsyncMock(), spawner=AsyncMock(), github_client=mock_gh,
        )

        await pipeline.process(thread, result)

        mock_gh.create_pr.assert_called_once()
        assert result.pr_url == "https://github.com/org/repo/pull/1"

    async def test_no_auto_pr_when_disabled(self, db, redis_state):
        """auto_open_pr=False → no PR creation even with commits."""
        settings = Settings(anthropic_api_key="test", auto_open_pr=False)
        thread = Thread(
            id="no-pr-test", source="test", source_ref={},
            repo_owner="org", repo_name="repo", status=ThreadStatus.RUNNING,
        )
        await db.upsert_thread(thread)

        result = AgentResult(branch="df/test/abc", exit_code=0, commit_count=3)
        mock_gh = AsyncMock()

        pipeline = SafetyPipeline(
            settings=settings, state_backend=db, redis_state=redis_state,
            integration=AsyncMock(), spawner=AsyncMock(), github_client=mock_gh,
        )

        await pipeline.process(thread, result)
        mock_gh.create_pr.assert_not_called()

    async def test_pr_creation_failure_still_reports(self, settings, db, redis_state):
        """If PR creation fails, still report result (without PR URL)."""
        thread = Thread(
            id="pr-fail-test", source="test", source_ref={},
            repo_owner="org", repo_name="repo", status=ThreadStatus.RUNNING,
        )
        await db.upsert_thread(thread)

        result = AgentResult(branch="df/test/abc", exit_code=0, commit_count=2)
        mock_gh = AsyncMock()
        mock_gh.create_pr = AsyncMock(side_effect=Exception("GitHub API error"))
        mock_integration = AsyncMock()

        pipeline = SafetyPipeline(
            settings=settings, state_backend=db, redis_state=redis_state,
            integration=mock_integration, spawner=AsyncMock(), github_client=mock_gh,
        )

        await pipeline.process(thread, result)

        # Should still report, just without PR URL
        mock_integration.report_result.assert_called_once()
        assert result.pr_url is None


# ─── Test: Webhook Rejection ─────────────────────────────────────────

class TestWebhookRejection:

    async def test_slack_invalid_signature_rejected(self):
        """Invalid Slack signature → None returned."""
        slack = SlackIntegration(signing_secret="real-secret", bot_token="xoxb", bot_user_id="U1")
        payload = json.dumps({"event": {"type": "message", "text": "hi"}}).encode()

        mock_req = AsyncMock()
        mock_req.body = AsyncMock(return_value=payload)
        mock_req.headers = {
            "x-slack-request-timestamp": str(int(time.time())),
            "x-slack-signature": "v0=invalid",
        }

        result = await slack.parse_webhook(mock_req)
        assert result is None

    async def test_slack_bot_message_filtered(self):
        """Bot messages are silently dropped."""
        slack = SlackIntegration(signing_secret="secret", bot_token="xoxb", bot_user_id="U_BOT")

        payload = {"event": {"type": "message", "text": "auto-reply", "bot_id": "B123", "channel": "C1", "ts": "1.0"}}
        body = json.dumps(payload).encode()
        ts = str(int(time.time()))
        sig = "v0=" + hmac.new(b"secret", f"v0:{ts}:{body.decode()}".encode(), hashlib.sha256).hexdigest()

        mock_req = AsyncMock()
        mock_req.body = AsyncMock(return_value=body)
        mock_req.headers = {"x-slack-request-timestamp": ts, "x-slack-signature": sig}

        result = await slack.parse_webhook(mock_req)
        assert result is None

    async def test_github_disallowed_org_rejected(self):
        """GitHub webhook from unapproved org → None."""
        github = GitHubIntegration(webhook_secret="secret", allowed_orgs=["approved-org"])

        payload = {
            "action": "created",
            "comment": {"body": "fix it"},
            "issue": {"number": 1},
            "repository": {"owner": {"login": "evil-org"}, "name": "repo"},
        }
        body = json.dumps(payload).encode()
        sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()

        mock_req = AsyncMock()
        mock_req.body = AsyncMock(return_value=body)
        mock_req.headers = {"x-github-event": "issue_comment", "x-hub-signature-256": sig}

        result = await github.parse_webhook(mock_req)
        assert result is None

    async def test_github_bot_marker_filtered(self):
        """GitHub comments with bot markers are filtered."""
        github = GitHubIntegration(webhook_secret="secret", allowed_orgs=["myorg"])

        payload = {
            "action": "created",
            "comment": {"body": "**Pull Request Created** https://..."},
            "issue": {"number": 1},
            "repository": {"owner": {"login": "myorg"}, "name": "repo"},
        }
        body = json.dumps(payload).encode()
        sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()

        mock_req = AsyncMock()
        mock_req.body = AsyncMock(return_value=body)
        mock_req.headers = {"x-github-event": "issue_comment", "x-hub-signature-256": sig}

        result = await github.parse_webhook(mock_req)
        assert result is None

    async def test_linear_bot_comment_filtered(self):
        """Linear bot comments are filtered."""
        linear = LinearIntegration(webhook_secret="secret", api_key="key")

        payload = {
            "type": "Comment", "action": "create",
            "data": {
                "body": "auto generated",
                "user": {"isBot": True},
                "issue": {"identifier": "X-1", "title": "Bug", "team": {"key": "X"}},
            },
        }
        body = json.dumps(payload).encode()
        sig = hmac.new(b"secret", body, hashlib.sha256).hexdigest()

        mock_req = AsyncMock()
        mock_req.body = AsyncMock(return_value=body)
        mock_req.headers = {"linear-signature": sig}

        result = await linear.parse_webhook(mock_req)
        assert result is None

    async def test_slack_empty_message_filtered(self):
        """Slack message with empty text after bot mention strip → None."""
        slack = SlackIntegration(signing_secret="secret", bot_token="xoxb", bot_user_id="U_BOT")

        payload = {"event": {"type": "app_mention", "text": "<@U_BOT>", "user": "U1", "channel": "C1", "ts": "1.0"}}
        body = json.dumps(payload).encode()
        ts = str(int(time.time()))
        sig = "v0=" + hmac.new(b"secret", f"v0:{ts}:{body.decode()}".encode(), hashlib.sha256).hexdigest()

        mock_req = AsyncMock()
        mock_req.body = AsyncMock(return_value=body)
        mock_req.headers = {"x-slack-request-timestamp": ts, "x-slack-signature": sig}

        result = await slack.parse_webhook(mock_req)
        assert result is None
