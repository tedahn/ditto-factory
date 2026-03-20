import hashlib
import hmac
import json
import time
import pytest
from unittest.mock import AsyncMock
from fastapi import Request
from controller.integrations.slack import SlackIntegration

@pytest.fixture
def slack():
    return SlackIntegration(
        signing_secret="test-secret",
        bot_token="xoxb-test",
        bot_user_id="U_BOT",
    )

def make_slack_request(payload: dict, signing_secret: str = "test-secret"):
    body = json.dumps(payload).encode()
    timestamp = str(int(time.time()))
    sig_basestring = f"v0:{timestamp}:{body.decode()}"
    signature = "v0=" + hmac.new(signing_secret.encode(), sig_basestring.encode(), hashlib.sha256).hexdigest()
    mock_req = AsyncMock(spec=Request)
    mock_req.body = AsyncMock(return_value=body)
    mock_req.headers = {
        "x-slack-request-timestamp": timestamp,
        "x-slack-signature": signature,
    }
    return mock_req

async def test_rejects_invalid_signature(slack):
    req = make_slack_request({"event": {"type": "message", "text": "hello"}}, signing_secret="wrong-secret")
    result = await slack.parse_webhook(req)
    assert result is None

async def test_filters_bot_messages(slack):
    payload = {
        "event": {
            "type": "message",
            "text": "bot response",
            "bot_id": "B123",
            "channel": "C123",
            "ts": "123.456",
        }
    }
    req = make_slack_request(payload)
    result = await slack.parse_webhook(req)
    assert result is None

async def test_filters_own_bot_user(slack):
    payload = {
        "event": {
            "type": "message",
            "text": "self message",
            "user": "U_BOT",
            "channel": "C123",
            "ts": "123.456",
        }
    }
    req = make_slack_request(payload)
    result = await slack.parse_webhook(req)
    assert result is None

async def test_parses_valid_message(slack):
    payload = {
        "event": {
            "type": "message",
            "text": "fix the login bug",
            "user": "U_USER",
            "channel": "C123",
            "ts": "123.456",
            "thread_ts": "100.000",
        }
    }
    req = make_slack_request(payload)
    result = await slack.parse_webhook(req)
    assert result is not None
    assert result.task == "fix the login bug"
    assert result.source == "slack"

async def test_strips_bot_mention(slack):
    payload = {
        "event": {
            "type": "app_mention",
            "text": "<@U_BOT> fix the login bug",
            "user": "U_USER",
            "channel": "C123",
            "ts": "123.456",
        }
    }
    req = make_slack_request(payload)
    result = await slack.parse_webhook(req)
    assert result is not None
    assert result.task == "fix the login bug"

async def test_url_verification_returns_none(slack):
    payload = {"type": "url_verification", "challenge": "abc"}
    req = make_slack_request(payload)
    result = await slack.parse_webhook(req)
    assert result is None
