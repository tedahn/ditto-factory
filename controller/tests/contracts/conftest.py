"""Shared fixtures and helpers for contract tests."""
import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock

import pytest


def make_signed_request(event_type: str, payload: dict, secret: str) -> AsyncMock:
    """Create a mock FastAPI Request with valid GitHub webhook signature."""
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    request = AsyncMock()
    request.body = AsyncMock(return_value=body)
    request.headers = {
        "x-github-event": event_type,
        "x-hub-signature-256": sig,
    }
    return request


def make_slack_signed_request(payload: dict, secret: str) -> AsyncMock:
    """Create a mock FastAPI Request with valid Slack signature."""
    body = json.dumps(payload).encode()
    body_str = body.decode()
    ts = str(int(time.time()))
    sig_basestring = f"v0:{ts}:{body_str}"
    sig = "v0=" + hmac.new(secret.encode(), sig_basestring.encode(), hashlib.sha256).hexdigest()
    request = AsyncMock()
    request.body = AsyncMock(return_value=body)
    request.headers = {
        "x-slack-request-timestamp": ts,
        "x-slack-signature": sig,
    }
    return request


def make_linear_signed_request(payload: dict, secret: str) -> AsyncMock:
    """Create a mock FastAPI Request with valid Linear signature."""
    body = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    request = AsyncMock()
    request.body = AsyncMock(return_value=body)
    request.headers = {
        "linear-signature": sig,
    }
    return request


# Shared fixtures


@pytest.fixture
def valid_task_request():
    from controller.models import TaskRequest

    return TaskRequest(
        thread_id="a" * 64,
        source="github",
        source_ref={"type": "issue_comment", "number": 42},
        repo_owner="testorg",
        repo_name="myrepo",
        task="fix the bug",
    )


@pytest.fixture
def valid_agent_result_dict():
    """The canonical shape an agent MUST write to Redis."""
    return {
        "branch": "df/github/abc12345",
        "exit_code": 0,
        "commit_count": 3,
        "stderr": "",
    }


@pytest.fixture
def valid_task_context_dict():
    """The canonical shape the controller writes to Redis for the agent."""
    return {
        "task": "fix the login bug",
        "system_prompt": "You are a coding agent...",
        "repo_url": "https://github.com/testorg/myrepo.git",
        "branch": "df/github/abc12345",
    }
