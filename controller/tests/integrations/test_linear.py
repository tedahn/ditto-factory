import hashlib
import hmac
import json
import pytest
from unittest.mock import AsyncMock
from fastapi import Request
from controller.integrations.linear import LinearIntegration

@pytest.fixture
def linear():
    return LinearIntegration(
        webhook_secret="test-secret",
        api_key="lin_api_test",
        team_repo_map={"ENG": ("myorg", "myrepo")},
    )

def make_linear_request(payload: dict, secret: str = "test-secret"):
    body = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    mock_req = AsyncMock(spec=Request)
    mock_req.body = AsyncMock(return_value=body)
    mock_req.headers = {"linear-signature": sig}
    return mock_req

async def test_rejects_invalid_signature(linear):
    req = make_linear_request({"type": "Comment", "action": "create"}, secret="wrong")
    result = await linear.parse_webhook(req)
    assert result is None

async def test_ignores_non_comment_events(linear):
    payload = {"type": "Issue", "action": "update", "data": {}}
    req = make_linear_request(payload)
    result = await linear.parse_webhook(req)
    assert result is None

async def test_filters_bot_comments(linear):
    payload = {
        "type": "Comment", "action": "create",
        "data": {
            "body": "auto comment",
            "user": {"isBot": True},
            "issue": {"identifier": "ENG-123", "title": "Fix", "team": {"key": "ENG"}},
        }
    }
    req = make_linear_request(payload)
    result = await linear.parse_webhook(req)
    assert result is None

async def test_parses_valid_comment(linear):
    payload = {
        "type": "Comment", "action": "create",
        "data": {
            "body": "please fix the auth flow",
            "user": {"isBot": False, "name": "Alice"},
            "issue": {"identifier": "ENG-42", "title": "Auth broken", "team": {"key": "ENG"}},
        }
    }
    req = make_linear_request(payload)
    result = await linear.parse_webhook(req)
    assert result is not None
    assert result.source == "linear"
    assert result.repo_owner == "myorg"
    assert result.repo_name == "myrepo"
    assert "please fix the auth flow" in result.task

async def test_unknown_team_maps_to_empty(linear):
    payload = {
        "type": "Comment", "action": "create",
        "data": {
            "body": "fix it",
            "user": {"isBot": False},
            "issue": {"identifier": "OTH-1", "title": "Bug", "team": {"key": "OTHER"}},
        }
    }
    req = make_linear_request(payload)
    result = await linear.parse_webhook(req)
    assert result is not None
    assert result.repo_owner == ""
