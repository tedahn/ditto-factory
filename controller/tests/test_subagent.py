"""Tests for subagent spawning (Phase 4)."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from controller.config import Settings
from controller.state.redis_state import RedisState
from controller.subagent import SubagentHandler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(**overrides) -> Settings:
    defaults = {
        "subagent_enabled": True,
        "subagent_timeout_seconds": 2,  # short for tests
        "max_subagents_per_task": 3,
        "subagent_depth_limit": 1,
        "subagent_inherit_branch": True,
        "redis_url": "redis://localhost:6379",
        "skill_registry_enabled": False,
    }
    defaults.update(overrides)
    return Settings(**defaults)


class FakeRedis:
    """Minimal async Redis mock that stores data in a dict."""

    def __init__(self):
        self._store: dict[str, str] = {}
        self._pubsub_instance = FakePubSub()

    async def get(self, key: str):
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self._store[key] = value

    async def incr(self, key: str):
        val = int(self._store.get(key, "0")) + 1
        self._store[key] = str(val)
        return val

    async def expire(self, key: str, seconds: int):
        pass

    async def publish(self, channel: str, message: str):
        pass

    def pubsub(self):
        return self._pubsub_instance


class FakePubSub:
    async def subscribe(self, *channels):
        pass

    async def get_message(self, ignore_subscribe_messages=True, timeout=1.0):
        return None

    async def unsubscribe(self, *channels):
        pass

    async def aclose(self):
        pass


def _make_handler(
    fake_redis: FakeRedis,
    spawner: MagicMock | None = None,
    settings: Settings | None = None,
    classifier=None,
    injector=None,
) -> SubagentHandler:
    s = settings or _make_settings()
    redis_state = RedisState.__new__(RedisState)
    redis_state._redis = fake_redis
    sp = spawner or MagicMock()
    sp.spawn = MagicMock(return_value="df-test-123")
    sp.delete = MagicMock()
    return SubagentHandler(
        settings=s,
        redis_state=redis_state,
        spawner=sp,
        classifier=classifier,
        injector=injector,
    )


def _publish_request(
    fake_redis: FakeRedis,
    request_id: str,
    parent_thread_id: str = "parent-abc",
    task: str = "Write unit tests for auth module",
    agent_type_hint: str = "",
) -> None:
    """Pre-populate a spawn request in fake Redis."""
    fake_redis._store[f"subagent_request:{request_id}"] = json.dumps({
        "parent_thread_id": parent_thread_id,
        "task": task,
        "agent_type_hint": agent_type_hint,
        "request_id": request_id,
        "timestamp": "2026-03-21T00:00:00Z",
    })
    # Also store the parent task so handler can resolve branch/repo
    fake_redis._store[f"task:{parent_thread_id}"] = json.dumps({
        "branch": "df/abc/feature",
        "repo_url": "https://github.com/org/repo.git",
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handler_spawns_job():
    """SubagentHandler should spawn a K8s job when it receives a request."""
    fake = FakeRedis()
    handler = _make_handler(fake)
    request_id = "parent-abc-sub-111"
    _publish_request(fake, request_id)

    # Pre-populate a result so handler doesn't timeout waiting
    # The child thread ID is dynamic, so we mock the spawner to capture
    # the thread_id and then set the result for it.
    original_spawn = handler._spawner.spawn

    def capture_spawn(**kwargs):
        tid = kwargs.get("thread_id") or "unknown"
        # Immediately post result for the child
        fake._store[f"result:{tid}"] = json.dumps({
            "branch": "df/abc/feature",
            "exit_code": 0,
            "commit_count": 2,
            "stderr": "",
        })
        return "df-test-123"

    handler._spawner.spawn = MagicMock(side_effect=capture_spawn)

    await handler._handle_spawn(request_id)

    handler._spawner.spawn.assert_called_once()
    call_kwargs = handler._spawner.spawn.call_args
    assert call_kwargs.kwargs.get("extra_env") == {"SUBAGENT_DEPTH": "1"}


@pytest.mark.asyncio
async def test_handler_forwards_result():
    """Handler should forward child result to subagent_result:{request_id}."""
    fake = FakeRedis()
    handler = _make_handler(fake)
    request_id = "parent-abc-sub-222"
    _publish_request(fake, request_id)

    expected_result = {
        "branch": "df/abc/feature",
        "exit_code": 0,
        "commit_count": 1,
        "stderr": "",
    }

    def capture_spawn(**kwargs):
        tid = kwargs.get("thread_id") or "unknown"
        fake._store[f"result:{tid}"] = json.dumps(expected_result)
        return "df-test-456"

    handler._spawner.spawn = MagicMock(side_effect=capture_spawn)

    await handler._handle_spawn(request_id)

    result_raw = fake._store.get(f"subagent_result:{request_id}")
    assert result_raw is not None
    result = json.loads(result_raw)
    assert result["exit_code"] == 0
    assert result["commit_count"] == 1


@pytest.mark.asyncio
async def test_timeout_posts_error():
    """Handler should post an error result when the child times out."""
    fake = FakeRedis()
    settings = _make_settings(subagent_timeout_seconds=1)  # 1s timeout
    handler = _make_handler(fake, settings=settings)
    request_id = "parent-abc-sub-333"
    _publish_request(fake, request_id)

    # Don't post any result -- child never completes
    handler._spawner.spawn = MagicMock(return_value="df-timeout-job")

    # Patch sleep to speed up polling
    with patch("controller.subagent.asyncio.sleep", new_callable=AsyncMock):
        await handler._handle_spawn(request_id)

    result_raw = fake._store.get(f"subagent_result:{request_id}")
    assert result_raw is not None
    result = json.loads(result_raw)
    assert result["exit_code"] == 1
    assert "timed out" in result["stderr"]


@pytest.mark.asyncio
async def test_missing_request_is_noop():
    """Handler should log a warning and return if request is missing from Redis."""
    fake = FakeRedis()
    handler = _make_handler(fake)

    # No request published -- should silently return
    await handler._handle_spawn("nonexistent-request-id")

    handler._spawner.spawn.assert_not_called()


@pytest.mark.asyncio
async def test_spawn_failure_posts_error():
    """If spawn raises, handler should still post an error result."""
    fake = FakeRedis()
    handler = _make_handler(fake)
    request_id = "parent-abc-sub-444"
    _publish_request(fake, request_id)

    handler._spawner.spawn = MagicMock(side_effect=RuntimeError("K8s down"))

    await handler._handle_spawn(request_id)

    result_raw = fake._store.get(f"subagent_result:{request_id}")
    assert result_raw is not None
    result = json.loads(result_raw)
    assert result["exit_code"] == 1
    assert "failed" in result["stderr"].lower()


@pytest.mark.asyncio
async def test_classifier_used_for_subagent():
    """When classifier is available, it should be called for subtask classification."""
    fake = FakeRedis()

    mock_classifier = AsyncMock()
    mock_classification = MagicMock()
    mock_classification.agent_type = "backend"
    mock_classification.skills = []
    mock_classifier.classify = AsyncMock(return_value=mock_classification)

    settings = _make_settings(skill_registry_enabled=True)
    handler = _make_handler(
        fake, settings=settings, classifier=mock_classifier
    )
    request_id = "parent-abc-sub-555"
    _publish_request(fake, request_id)

    def capture_spawn(**kwargs):
        tid = kwargs.get("thread_id") or "unknown"
        fake._store[f"result:{tid}"] = json.dumps({
            "branch": "df/abc/feature",
            "exit_code": 0,
            "commit_count": 0,
            "stderr": "",
        })
        return "df-classified-job"

    handler._spawner.spawn = MagicMock(side_effect=capture_spawn)

    await handler._handle_spawn(request_id)

    mock_classifier.classify.assert_called_once_with(
        task="Write unit tests for auth module"
    )


@pytest.mark.asyncio
async def test_child_inherits_parent_branch():
    """Child task in Redis should use the parent's branch."""
    fake = FakeRedis()
    handler = _make_handler(fake)
    request_id = "parent-abc-sub-666"
    _publish_request(fake, request_id, parent_thread_id="parent-xyz")
    fake._store["task:parent-xyz"] = json.dumps({
        "branch": "df/xyz/custom-branch",
        "repo_url": "https://github.com/org/myrepo.git",
    })

    child_thread_id_captured = []

    def capture_spawn(**kwargs):
        tid = kwargs.get("thread_id") or "unknown"
        child_thread_id_captured.append(tid)
        fake._store[f"result:{tid}"] = json.dumps({
            "branch": "df/xyz/custom-branch",
            "exit_code": 0,
            "commit_count": 1,
            "stderr": "",
        })
        return "df-branch-job"

    handler._spawner.spawn = MagicMock(side_effect=capture_spawn)

    await handler._handle_spawn(request_id)

    assert len(child_thread_id_captured) == 1
    child_tid = child_thread_id_captured[0]
    child_task_raw = fake._store.get(f"task:{child_tid}")
    assert child_task_raw is not None
    child_task = json.loads(child_task_raw)
    assert child_task["branch"] == "df/xyz/custom-branch"
    assert child_task["repo_url"] == "https://github.com/org/myrepo.git"
    assert child_task["is_subagent"] is True
