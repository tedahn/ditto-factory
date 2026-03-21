"""Contract 14: Redis Stream Events.

Verifies the stream event contract: append_stream_event and read_stream
for real-time agent status tracking.
"""
import pytest
import fakeredis.aioredis
from controller.state.redis_state import RedisState


class TestStreamEventContract:
    """Contract 14: Stream events for real-time agent status."""

    @pytest.fixture
    async def redis_state(self):
        redis = fakeredis.aioredis.FakeRedis()
        return RedisState(redis)

    async def test_append_and_read_stream_event(self, redis_state):
        """Contract: events written via append_stream_event are readable via read_stream."""
        await redis_state.append_stream_event("t1", "started")
        await redis_state.append_stream_event("t1", "running tool: bash")
        await redis_state.append_stream_event("t1", "completed")

        events = await redis_state.read_stream("t1")
        assert len(events) == 3
        assert events[0][1]["event"] == "started"
        assert events[1][1]["event"] == "running tool: bash"
        assert events[2][1]["event"] == "completed"

    async def test_read_stream_empty(self, redis_state):
        """Contract: reading nonexistent stream returns empty list."""
        events = await redis_state.read_stream("nonexistent")
        assert events == []

    async def test_stream_isolation_between_threads(self, redis_state):
        """Contract: streams are isolated by thread_id."""
        await redis_state.append_stream_event("t1", "event-for-t1")
        await redis_state.append_stream_event("t2", "event-for-t2")
        t1_events = await redis_state.read_stream("t1")
        t2_events = await redis_state.read_stream("t2")
        assert len(t1_events) == 1
        assert len(t2_events) == 1
        assert t1_events[0][1]["event"] == "event-for-t1"
        assert t2_events[0][1]["event"] == "event-for-t2"

    async def test_read_stream_with_cursor(self, redis_state):
        """Contract: read_stream with last_id returns events from that ID onward."""
        await redis_state.append_stream_event("t1", "first")
        events = await redis_state.read_stream("t1")
        first_id = events[0][0]

        await redis_state.append_stream_event("t1", "second")
        newer = await redis_state.read_stream("t1", last_id=first_id)
        # XRANGE with min=last_id is inclusive, so we get first + second
        assert len(newer) == 2

    async def test_stream_event_values_are_strings(self, redis_state):
        """Contract: event values are decoded to strings."""
        await redis_state.append_stream_event("t1", "test-event")
        events = await redis_state.read_stream("t1")
        eid, data = events[0]
        assert isinstance(eid, str)
        assert isinstance(data["event"], str)

    async def test_stream_key_format(self, redis_state):
        """Contract: stream key is agent:{thread_id}."""
        await redis_state.append_stream_event("my-thread", "ev")
        # Verify the key exists in Redis
        exists = await redis_state._redis.exists("agent:my-thread")
        assert exists

    async def test_event_ordering(self, redis_state):
        """Contract: events are returned in insertion order."""
        for i in range(10):
            await redis_state.append_stream_event("t1", f"event-{i}")
        events = await redis_state.read_stream("t1")
        assert len(events) == 10
        for i, (eid, data) in enumerate(events):
            assert data["event"] == f"event-{i}"
