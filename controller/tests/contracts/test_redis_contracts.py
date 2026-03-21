"""Contracts 5, 6, 7, 11: Redis serialization boundaries.

Contract 5: Controller writes task context that agent can read.
Contract 6: Agent writes result that controller can parse.
Contract 7: Redis -> JobMonitor result polling.
Contract 11: Message queuing for follow-ups (FIFO, atomic drain).
"""
import json
import pytest
import fakeredis.aioredis
from controller.state.redis_state import RedisState, TASK_TTL, RESULT_TTL
from controller.models import AgentResult


TASK_CONTEXT_REQUIRED_KEYS = {"task", "system_prompt", "repo_url", "branch"}
AGENT_RESULT_REQUIRED_KEYS = {"branch", "exit_code", "commit_count", "stderr"}


class TestTaskContextContract:
    """Contract 5: Controller writes task context that agent can read."""

    @pytest.fixture
    async def redis_state(self):
        redis = fakeredis.aioredis.FakeRedis()
        return RedisState(redis)

    async def test_push_task_sets_correct_key(self, redis_state):
        context = {
            "task": "fix bug",
            "system_prompt": "You are an agent",
            "repo_url": "https://github.com/org/repo.git",
            "branch": "df/github/abc123",
        }
        await redis_state.push_task("thread-1", context)
        raw = await redis_state._redis.get("task:thread-1")
        assert raw is not None

        parsed = json.loads(raw)
        assert TASK_CONTEXT_REQUIRED_KEYS.issubset(parsed.keys()), (
            f"Missing keys: {TASK_CONTEXT_REQUIRED_KEYS - parsed.keys()}"
        )

    async def test_task_context_all_values_are_strings(self, redis_state):
        """Agent expects all values to be strings."""
        context = {
            "task": "fix bug",
            "system_prompt": "prompt",
            "repo_url": "https://github.com/o/r.git",
            "branch": "df/test/x",
        }
        await redis_state.push_task("thread-2", context)
        parsed = await redis_state.get_task("thread-2")
        for key in TASK_CONTEXT_REQUIRED_KEYS:
            assert isinstance(parsed[key], str), f"{key} should be str, got {type(parsed[key])}"

    async def test_task_context_ttl(self, redis_state):
        """Task context must have a TTL to prevent unbounded growth."""
        await redis_state.push_task(
            "thread-3", {"task": "x", "system_prompt": "y", "repo_url": "z", "branch": "b"}
        )
        ttl = await redis_state._redis.ttl("task:thread-3")
        assert ttl > 0
        assert ttl <= TASK_TTL

    async def test_repo_url_format(self, redis_state):
        """repo_url must be a valid GitHub clone URL."""
        context = {
            "task": "t",
            "system_prompt": "s",
            "repo_url": "https://github.com/myorg/myrepo.git",
            "branch": "df/test/b",
        }
        await redis_state.push_task("thread-4", context)
        parsed = await redis_state.get_task("thread-4")
        assert parsed["repo_url"].startswith("https://github.com/")
        assert parsed["repo_url"].endswith(".git")

    async def test_roundtrip_preserves_data(self, redis_state):
        """Contract: push then get returns identical data."""
        context = {
            "task": "implement feature X",
            "system_prompt": "You are a helpful coding agent.",
            "repo_url": "https://github.com/org/repo.git",
            "branch": "df/abc/12345678",
        }
        await redis_state.push_task("thread-rt", context)
        parsed = await redis_state.get_task("thread-rt")
        assert parsed == context

    async def test_get_nonexistent_task_returns_none(self, redis_state):
        """Contract: missing key returns None, not an exception."""
        result = await redis_state.get_task("nonexistent")
        assert result is None


class TestAgentResultContract:
    """Contract 6: Agent writes result that controller can parse."""

    @pytest.fixture
    async def redis_state(self):
        redis = fakeredis.aioredis.FakeRedis()
        return RedisState(redis)

    async def test_valid_result_roundtrip(self, redis_state):
        """Simulate agent writing, controller reading."""
        agent_output = {
            "branch": "df/github/abc123",
            "exit_code": 0,
            "commit_count": 5,
            "stderr": "",
        }
        await redis_state.push_result("thread-1", agent_output)
        parsed = await redis_state.get_result("thread-1")
        assert AGENT_RESULT_REQUIRED_KEYS.issubset(parsed.keys())

    async def test_result_to_agent_result_model(self, redis_state):
        """Controller must be able to construct AgentResult from dict."""
        agent_output = {
            "branch": "df/test/x",
            "exit_code": 0,
            "commit_count": 2,
            "stderr": "warning: something",
        }
        await redis_state.push_result("thread-2", agent_output)
        parsed = await redis_state.get_result("thread-2")

        # This is exactly what JobMonitor does:
        result = AgentResult(
            branch=parsed.get("branch", ""),
            exit_code=int(parsed.get("exit_code", 1)),
            commit_count=int(parsed.get("commit_count", 0)),
            stderr=parsed.get("stderr", ""),
        )
        assert result.branch == "df/test/x"
        assert result.exit_code == 0
        assert result.commit_count == 2

    async def test_result_with_string_numbers(self, redis_state):
        """Agent might write numbers as strings; controller must handle."""
        agent_output = {
            "branch": "df/test/y",
            "exit_code": "0",  # String, not int
            "commit_count": "3",  # String, not int
            "stderr": "",
        }
        await redis_state.push_result("thread-3", agent_output)
        parsed = await redis_state.get_result("thread-3")

        # Must not crash
        result = AgentResult(
            branch=parsed.get("branch", ""),
            exit_code=int(parsed.get("exit_code", 1)),
            commit_count=int(parsed.get("commit_count", 0)),
            stderr=parsed.get("stderr", ""),
        )
        assert result.exit_code == 0
        assert result.commit_count == 3

    async def test_result_with_non_numeric_strings_crashes(self, redis_state):
        """BUG: Agent writes non-numeric exit_code -> int() raises ValueError.
        This test documents the crash path. Fix: wrap int() in try/except."""
        agent_output = {
            "branch": "df/test/crash",
            "exit_code": "abc",  # Non-numeric string
            "commit_count": "",  # Empty string
            "stderr": "",
        }
        await redis_state.push_result("thread-crash", agent_output)
        parsed = await redis_state.get_result("thread-crash")

        with pytest.raises(ValueError):
            AgentResult(
                branch=parsed.get("branch", ""),
                exit_code=int(parsed.get("exit_code", 1)),
                commit_count=int(parsed.get("commit_count", 0)),
                stderr=parsed.get("stderr", ""),
            )

    async def test_result_with_extra_fields_ignored(self, redis_state):
        """Agent may add new fields; controller must not crash."""
        agent_output = {
            "branch": "df/test/z",
            "exit_code": 0,
            "commit_count": 1,
            "stderr": "",
            "new_field": "unexpected",
            "metrics": {"tokens": 1000},
        }
        await redis_state.push_result("thread-4", agent_output)
        parsed = await redis_state.get_result("thread-4")

        result = AgentResult(
            branch=parsed.get("branch", ""),
            exit_code=int(parsed.get("exit_code", 1)),
            commit_count=int(parsed.get("commit_count", 0)),
            stderr=parsed.get("stderr", ""),
        )
        assert result.branch == "df/test/z"

    async def test_missing_optional_field_defaults(self, redis_state):
        """If agent omits stderr, controller defaults gracefully."""
        agent_output = {
            "branch": "df/test/w",
            "exit_code": 1,
            "commit_count": 0,
            # No stderr
        }
        await redis_state.push_result("thread-5", agent_output)
        parsed = await redis_state.get_result("thread-5")

        result = AgentResult(
            branch=parsed.get("branch", ""),
            exit_code=int(parsed.get("exit_code", 1)),
            commit_count=int(parsed.get("commit_count", 0)),
            stderr=parsed.get("stderr", ""),
        )
        assert result.stderr == ""

    async def test_result_ttl(self, redis_state):
        """Result must have a TTL to prevent unbounded growth."""
        await redis_state.push_result("thread-ttl", {"branch": "b", "exit_code": 0, "commit_count": 0})
        ttl = await redis_state._redis.ttl("result:thread-ttl")
        assert ttl > 0
        assert ttl <= RESULT_TTL

    async def test_get_nonexistent_result_returns_none(self, redis_state):
        """Contract: missing key returns None."""
        result = await redis_state.get_result("nonexistent")
        assert result is None


class TestQueueContract:
    """Contract 11: Message queuing for follow-ups."""

    @pytest.fixture
    async def redis_state(self):
        redis = fakeredis.aioredis.FakeRedis()
        return RedisState(redis)

    async def test_fifo_ordering(self, redis_state):
        await redis_state.queue_message("t1", "first")
        await redis_state.queue_message("t1", "second")
        await redis_state.queue_message("t1", "third")
        messages = await redis_state.drain_messages("t1")
        assert messages == ["first", "second", "third"]

    async def test_drain_empties_queue(self, redis_state):
        await redis_state.queue_message("t2", "msg")
        await redis_state.drain_messages("t2")
        messages = await redis_state.drain_messages("t2")
        assert messages == []

    async def test_drain_empty_queue_returns_empty_list(self, redis_state):
        messages = await redis_state.drain_messages("nonexistent")
        assert messages == []
        assert isinstance(messages, list)

    async def test_queue_isolation_between_threads(self, redis_state):
        await redis_state.queue_message("t3", "for-t3")
        await redis_state.queue_message("t4", "for-t4")
        assert await redis_state.drain_messages("t3") == ["for-t3"]
        assert await redis_state.drain_messages("t4") == ["for-t4"]
