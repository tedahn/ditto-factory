"""Contract 1: Webhook -> Integration Parser.

Verifies that raw HTTP requests with signed payloads produce valid
TaskRequest dataclasses (or None for rejected inputs).
"""
import pytest
from controller.models import TaskRequest
from tests.contracts.conftest import (
    make_signed_request,
    make_slack_signed_request,
    make_linear_signed_request,
)

REQUIRED_TASK_REQUEST_FIELDS = {"thread_id", "source", "source_ref", "repo_owner", "repo_name", "task"}
VALID_SOURCES = {"slack", "github", "linear"}


class TaskRequestContractValidator:
    """Reusable validator for any integration's parse_webhook output."""

    @staticmethod
    def validate(task_req: TaskRequest | None, *, allow_none: bool = False):
        if task_req is None:
            assert allow_none, "parse_webhook returned None unexpectedly"
            return

        # All required fields are non-empty strings (except repo_owner/repo_name for Slack)
        assert isinstance(task_req.thread_id, str) and len(task_req.thread_id) > 0
        assert task_req.source in VALID_SOURCES
        assert isinstance(task_req.source_ref, dict) and len(task_req.source_ref) > 0
        assert isinstance(task_req.task, str) and len(task_req.task) > 0

        # Thread ID is deterministic (SHA256 hex)
        assert len(task_req.thread_id) == 64, (
            f"thread_id should be SHA256 hex, got len={len(task_req.thread_id)}"
        )

        # Conversation and images are lists
        assert isinstance(task_req.conversation, list)
        assert isinstance(task_req.images, list)


class TestGitHubWebhookContract:
    """Verify GitHub webhook payloads produce valid TaskRequests."""

    @pytest.fixture
    def github_integration(self):
        from controller.integrations.github import GitHubIntegration

        return GitHubIntegration(
            webhook_secret="test-secret",
            allowed_orgs=["testorg"],
        )

    async def test_issue_comment_produces_valid_task_request(self, github_integration):
        """Contract: issue_comment event -> TaskRequest with source='github'."""
        payload = {
            "action": "created",
            "issue": {"number": 42, "title": "Bug", "body": "desc"},
            "comment": {"body": "please fix this", "user": {"login": "human", "type": "User"}},
            "repository": {
                "full_name": "testorg/myrepo",
                "name": "myrepo",
                "owner": {"login": "testorg"},
            },
        }
        request = make_signed_request("issue_comment", payload, "test-secret")
        result = await github_integration.parse_webhook(request)

        TaskRequestContractValidator.validate(result)
        assert result.source == "github"
        assert result.repo_owner == "testorg"
        assert result.repo_name == "myrepo"
        assert "number" in result.source_ref

    async def test_issue_opened_produces_valid_task_request(self, github_integration):
        """Contract: issues/opened event -> TaskRequest with title+body as task."""
        payload = {
            "action": "opened",
            "issue": {"number": 10, "title": "New feature", "body": "Please add X"},
            "repository": {
                "full_name": "testorg/myrepo",
                "name": "myrepo",
                "owner": {"login": "testorg"},
            },
        }
        request = make_signed_request("issues", payload, "test-secret")
        result = await github_integration.parse_webhook(request)

        TaskRequestContractValidator.validate(result)
        assert result.source == "github"
        assert "New feature" in result.task

    async def test_bot_message_returns_none(self, github_integration):
        """Contract: bot messages must be filtered to prevent loops."""
        payload = {
            "action": "created",
            "issue": {"number": 1, "title": "T", "body": ""},
            "comment": {
                "body": "**Agent Result**\nBranch: `df/test/x`",
                "user": {"login": "ditto-bot", "type": "Bot"},
            },
            "repository": {
                "full_name": "testorg/repo",
                "name": "repo",
                "owner": {"login": "testorg"},
            },
        }
        request = make_signed_request("issue_comment", payload, "test-secret")
        result = await github_integration.parse_webhook(request)
        TaskRequestContractValidator.validate(result, allow_none=True)

    async def test_invalid_signature_returns_none(self, github_integration):
        """Contract: tampered payload must be rejected."""
        payload = {
            "action": "created",
            "comment": {"body": "x"},
            "repository": {
                "full_name": "testorg/r",
                "name": "r",
                "owner": {"login": "testorg"},
            },
        }
        request = make_signed_request("issue_comment", payload, "wrong-secret")
        result = await github_integration.parse_webhook(request)
        assert result is None

    async def test_thread_id_determinism(self, github_integration):
        """Contract: same input always produces same thread_id."""
        payload = {
            "action": "created",
            "issue": {"number": 42, "title": "Bug", "body": ""},
            "comment": {"body": "fix it", "user": {"login": "dev", "type": "User"}},
            "repository": {
                "full_name": "testorg/repo",
                "name": "repo",
                "owner": {"login": "testorg"},
            },
        }
        req1 = make_signed_request("issue_comment", payload, "test-secret")
        req2 = make_signed_request("issue_comment", payload, "test-secret")
        r1 = await github_integration.parse_webhook(req1)
        r2 = await github_integration.parse_webhook(req2)
        assert r1.thread_id == r2.thread_id

    async def test_disallowed_org_returns_none(self, github_integration):
        """Contract: payloads from orgs not in allowed list are rejected."""
        payload = {
            "action": "created",
            "issue": {"number": 1, "title": "T", "body": ""},
            "comment": {"body": "hello", "user": {"login": "dev", "type": "User"}},
            "repository": {
                "full_name": "otherorg/repo",
                "name": "repo",
                "owner": {"login": "otherorg"},
            },
        }
        request = make_signed_request("issue_comment", payload, "test-secret")
        result = await github_integration.parse_webhook(request)
        assert result is None

    async def test_unknown_event_type_returns_none(self, github_integration):
        """Contract: unrecognized event types are ignored."""
        payload = {"action": "created", "repository": {"name": "r", "owner": {"login": "testorg"}}}
        request = make_signed_request("deployment", payload, "test-secret")
        result = await github_integration.parse_webhook(request)
        assert result is None


class TestSlackWebhookContract:
    """Verify Slack webhook payloads produce valid TaskRequests."""

    @pytest.fixture
    def slack_integration(self):
        from controller.integrations.slack import SlackIntegration

        return SlackIntegration(
            signing_secret="slack-secret",
            bot_token="xoxb-test",
            bot_user_id="U_BOT",
        )

    async def test_app_mention_produces_valid_task_request(self, slack_integration):
        """Contract: app_mention event -> TaskRequest with source='slack'."""
        payload = {
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "channel": "C123",
                "thread_ts": "1234567890.123456",
                "ts": "1234567890.123456",
                "text": "<@U_BOT> fix the login page",
                "user": "U_HUMAN",
            },
        }
        request = make_slack_signed_request(payload, "slack-secret")
        result = await slack_integration.parse_webhook(request)

        TaskRequestContractValidator.validate(result)
        assert result.source == "slack"
        assert "channel" in result.source_ref
        assert "thread_ts" in result.source_ref
        # Bot mention should be stripped from task
        assert "<@U_BOT>" not in result.task
        assert "fix the login page" in result.task

    async def test_bot_message_returns_none(self, slack_integration):
        """Contract: bot's own messages are filtered."""
        payload = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel": "C123",
                "ts": "1234567890.123456",
                "text": "result posted",
                "bot_id": "B123",
            },
        }
        request = make_slack_signed_request(payload, "slack-secret")
        result = await slack_integration.parse_webhook(request)
        assert result is None

    async def test_bot_user_id_message_returns_none(self, slack_integration):
        """Contract: messages from bot user ID are filtered."""
        payload = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel": "C123",
                "ts": "1234567890.123456",
                "text": "some text",
                "user": "U_BOT",
            },
        }
        request = make_slack_signed_request(payload, "slack-secret")
        result = await slack_integration.parse_webhook(request)
        assert result is None

    async def test_url_verification_returns_none(self, slack_integration):
        """Contract: URL verification challenge returns None (handled separately)."""
        payload = {"type": "url_verification", "challenge": "test-challenge"}
        request = make_slack_signed_request(payload, "slack-secret")
        result = await slack_integration.parse_webhook(request)
        assert result is None

    async def test_invalid_signature_returns_none(self, slack_integration):
        """Contract: tampered payload must be rejected."""
        payload = {
            "type": "event_callback",
            "event": {"type": "app_mention", "channel": "C1", "ts": "1", "text": "hi"},
        }
        request = make_slack_signed_request(payload, "wrong-secret")
        result = await slack_integration.parse_webhook(request)
        assert result is None

    async def test_empty_text_returns_none(self, slack_integration):
        """Contract: messages with no text after bot mention stripping are ignored."""
        payload = {
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "channel": "C123",
                "ts": "1234567890.123456",
                "text": "<@U_BOT>",
                "user": "U_HUMAN",
            },
        }
        request = make_slack_signed_request(payload, "slack-secret")
        result = await slack_integration.parse_webhook(request)
        assert result is None


class TestLinearWebhookContract:
    """Verify Linear webhook payloads produce valid TaskRequests."""

    @pytest.fixture
    def linear_integration(self):
        from controller.integrations.linear import LinearIntegration

        return LinearIntegration(
            webhook_secret="linear-secret",
            api_key="lin_test",
            team_repo_map={"ENG": ("myorg", "myrepo")},
        )

    async def test_comment_create_produces_valid_task_request(self, linear_integration):
        """Contract: Comment create event -> TaskRequest with source='linear'."""
        payload = {
            "type": "Comment",
            "action": "create",
            "data": {
                "body": "Please implement this feature",
                "issue": {
                    "identifier": "ENG-123",
                    "title": "Add dark mode",
                    "team": {"key": "ENG"},
                },
                "user": {"name": "Developer", "isBot": False},
            },
        }
        request = make_linear_signed_request(payload, "linear-secret")
        result = await linear_integration.parse_webhook(request)

        TaskRequestContractValidator.validate(result)
        assert result.source == "linear"
        assert result.repo_owner == "myorg"
        assert result.repo_name == "myrepo"
        assert "issue_id" in result.source_ref
        assert "ENG-123" in result.task

    async def test_bot_comment_returns_none(self, linear_integration):
        """Contract: bot-generated comments are filtered."""
        payload = {
            "type": "Comment",
            "action": "create",
            "data": {
                "body": "automated message",
                "issue": {"identifier": "ENG-1", "title": "T", "team": {"key": "ENG"}},
                "user": {"name": "Bot", "isBot": True},
            },
        }
        request = make_linear_signed_request(payload, "linear-secret")
        result = await linear_integration.parse_webhook(request)
        assert result is None

    async def test_non_comment_event_returns_none(self, linear_integration):
        """Contract: non-Comment event types are ignored."""
        payload = {
            "type": "Issue",
            "action": "create",
            "data": {"title": "New issue"},
        }
        request = make_linear_signed_request(payload, "linear-secret")
        result = await linear_integration.parse_webhook(request)
        assert result is None

    async def test_invalid_signature_returns_none(self, linear_integration):
        """Contract: tampered payload must be rejected."""
        payload = {
            "type": "Comment",
            "action": "create",
            "data": {"body": "test", "issue": {"identifier": "X-1", "title": "T", "team": {"key": "X"}}},
        }
        request = make_linear_signed_request(payload, "wrong-secret")
        result = await linear_integration.parse_webhook(request)
        assert result is None

    async def test_unmapped_team_has_empty_repo(self, linear_integration):
        """Contract: unknown team key results in empty repo_owner/repo_name."""
        payload = {
            "type": "Comment",
            "action": "create",
            "data": {
                "body": "do this",
                "issue": {
                    "identifier": "OPS-42",
                    "title": "Fix infra",
                    "team": {"key": "OPS"},
                },
                "user": {"name": "Dev", "isBot": False},
            },
        }
        request = make_linear_signed_request(payload, "linear-secret")
        result = await linear_integration.parse_webhook(request)
        assert result is not None
        assert result.repo_owner == ""
        assert result.repo_name == ""
