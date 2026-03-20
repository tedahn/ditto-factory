import json
import pytest

try:
    import fakeredis.aioredis
    HAS_FAKEREDIS = True
except ImportError:
    HAS_FAKEREDIS = False

pytestmark = pytest.mark.skipif(not HAS_FAKEREDIS, reason="fakeredis not installed")

from controller.state.redis_state import RedisState


@pytest.fixture
async def redis_state():
    fake = fakeredis.aioredis.FakeRedis()
    return RedisState(fake)


async def test_push_and_get_task(redis_state):
    await redis_state.push_task("t1", {"task": "fix bug", "system_prompt": "you are helpful"})
    got = await redis_state.get_task("t1")
    assert got["task"] == "fix bug"


async def test_push_and_get_result(redis_state):
    await redis_state.push_result("t1", {"branch": "df/t1/123", "exit_code": 0, "commit_count": 2})
    got = await redis_state.get_result("t1")
    assert got["commit_count"] == 2


async def test_queue_and_drain_messages(redis_state):
    await redis_state.queue_message("t1", "also fix the tests")
    await redis_state.queue_message("t1", "and update the docs")
    msgs = await redis_state.drain_messages("t1")
    assert len(msgs) == 2
    assert msgs[0] == "also fix the tests"
    assert await redis_state.drain_messages("t1") == []


async def test_task_ttl(redis_state):
    await redis_state.push_task("t1", {"task": "test"})
    ttl = await redis_state._redis.ttl("task:t1")
    assert 0 < ttl <= 3600
