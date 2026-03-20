import hashlib
import hmac
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import Request
from controller.integrations.github import GitHubIntegration
from controller.models import AgentResult, Thread

@pytest.fixture
def github():
    return GitHubIntegration(
        webhook_secret="test-secret",
        app_id="12345",
        private_key="fake-key",
        allowed_orgs=["myorg"],
    )

def make_request(payload: dict, event: str, secret: str = "test-secret"):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    mock_req = AsyncMock(spec=Request)
    mock_req.body = AsyncMock(return_value=body)
    mock_req.headers = {"x-github-event": event, "x-hub-signature-256": sig}
    return mock_req

async def test_rejects_invalid_signature(github):
    req = make_request({"action": "created"}, "issue_comment", secret="wrong")
    result = await github.parse_webhook(req)
    assert result is None

async def test_filters_bot_messages(github):
    payload = {
        "action": "created",
        "comment": {"body": "**Pull Request Created** ..."},
        "issue": {"number": 1},
        "repository": {"owner": {"login": "myorg"}, "name": "repo"},
    }
    req = make_request(payload, "issue_comment")
    result = await github.parse_webhook(req)
    assert result is None

async def test_rejects_disallowed_org(github):
    payload = {
        "action": "created",
        "comment": {"body": "fix this bug"},
        "issue": {"number": 1},
        "repository": {"owner": {"login": "otherorg"}, "name": "repo"},
    }
    req = make_request(payload, "issue_comment")
    result = await github.parse_webhook(req)
    assert result is None

async def test_parses_issue_comment(github):
    payload = {
        "action": "created",
        "comment": {"body": "fix this bug please"},
        "issue": {"number": 42, "title": "Login broken"},
        "repository": {"owner": {"login": "myorg"}, "name": "repo", "full_name": "myorg/repo"},
    }
    req = make_request(payload, "issue_comment")
    result = await github.parse_webhook(req)
    assert result is not None
    assert result.repo_owner == "myorg"
    assert result.task == "fix this bug please"

async def test_parses_issue_opened(github):
    payload = {
        "action": "opened",
        "issue": {"number": 10, "title": "New bug", "body": "Steps to repro..."},
        "repository": {"owner": {"login": "myorg"}, "name": "repo"},
    }
    req = make_request(payload, "issues")
    result = await github.parse_webhook(req)
    assert result is not None
    assert "New bug" in result.task
    assert result.source_ref["type"] == "issue_opened"

async def test_parses_pr_review_comment(github):
    payload = {
        "action": "created",
        "comment": {"body": "please fix this line"},
        "pull_request": {"number": 5},
        "repository": {"owner": {"login": "myorg"}, "name": "repo"},
    }
    req = make_request(payload, "pull_request_review_comment")
    result = await github.parse_webhook(req)
    assert result is not None
    assert result.source_ref["type"] == "pr_review_comment"


async def test_report_result_posts_comment(github):
    github._client = AsyncMock()
    github._client.post = AsyncMock(return_value=MagicMock(status_code=201))

    result = AgentResult(branch="df/abc/123", exit_code=0, commit_count=3, pr_url="https://github.com/myorg/repo/pull/1")
    thread = Thread(id="t1", source="github", source_ref={"type": "issue_comment", "number": 42, "is_pr": False},
                    repo_owner="myorg", repo_name="repo")
    await github.report_result(thread, result)
    github._client.post.assert_called_once()


async def test_fetch_context_returns_comments(github):
    github._client = AsyncMock()
    mock_response = MagicMock()
    mock_response.json.return_value = [
        {"body": "first comment", "user": {"login": "alice"}},
        {"body": "second comment", "user": {"login": "bob"}},
    ]
    mock_response.status_code = 200
    github._client.get = AsyncMock(return_value=mock_response)

    thread = Thread(id="t1", source="github", source_ref={"type": "issue_comment", "number": 42, "is_pr": False},
                    repo_owner="myorg", repo_name="repo")
    context = await github.fetch_context(thread)
    assert "first comment" in context
    assert "second comment" in context
