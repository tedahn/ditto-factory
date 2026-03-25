"""Tests for swarm Redis Streams wrapper."""
import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from controller.swarm.redis_streams import SwarmRedisStreams


class TestSwarmRedisStreams:
    """Test SwarmRedisStreams with mocked Redis.

    Note: fakeredis has incomplete Streams support. These tests mock
    Redis calls directly. Integration tests with real Redis are needed
    for production validation.
    """

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.xadd = AsyncMock(return_value=b"1234567890-0")
        redis.xreadgroup = AsyncMock(return_value=[])
        redis.xack = AsyncMock()
        redis.hset = AsyncMock()
        redis.hgetall = AsyncMock(return_value={})
        redis.delete = AsyncMock()
        redis.expire = AsyncMock()
        redis.pipeline = MagicMock()
        return redis

    @pytest.fixture
    def streams(self, mock_redis):
        return SwarmRedisStreams(mock_redis, maxlen=10000)

    async def test_create_group_creates_streams(self, streams, mock_redis):
        # xgroup_create should be called for each agent x each stream
        mock_redis.xgroup_create = AsyncMock()
        await streams.create_group("grp-1", ["a1", "a2"])
        # 2 agents x 2 streams (messages + control) = 4 calls
        assert mock_redis.xgroup_create.call_count == 4

    async def test_send_message_calls_xadd(self, streams, mock_redis):
        msg_id = await streams.send_message(
            group_id="grp-1",
            sender_id="a1",
            message_type="status",
            payload={"state": "working"},
            signature="sig123",
        )
        mock_redis.xadd.assert_called_once()
        call_args = mock_redis.xadd.call_args
        assert "swarm:grp-1:messages" in str(call_args)

    async def test_send_message_uses_maxlen(self, streams, mock_redis):
        await streams.send_message(
            group_id="grp-1", sender_id="a1",
            message_type="data", payload={}, signature="sig",
        )
        call_kwargs = mock_redis.xadd.call_args
        # Verify maxlen is passed
        assert call_kwargs[1].get("maxlen") == 10000 or 10000 in call_kwargs[0]

    async def test_create_agent_registry(self, streams, mock_redis):
        from controller.models import SwarmAgent, AgentStatus
        agents = [
            SwarmAgent(id="a1", group_id="grp-1", role="researcher",
                       agent_type="general", task_assignment="search"),
        ]
        await streams.create_agent_registry("grp-1", agents)
        mock_redis.hset.assert_called()

    async def test_update_agent_status(self, streams, mock_redis):
        mock_redis.hget = AsyncMock(return_value=json.dumps({
            "role": "researcher", "status": "pending",
            "task_assignment": "search", "last_seen": "",
        }).encode())
        await streams.update_agent_status("grp-1", "a1", "active")
        mock_redis.hset.assert_called()

    async def test_get_agent_registry(self, streams, mock_redis):
        mock_redis.hgetall = AsyncMock(return_value={
            b"a1": json.dumps({"role": "researcher", "status": "active"}).encode(),
        })
        registry = await streams.get_agent_registry("grp-1")
        assert "a1" in registry
        assert registry["a1"]["role"] == "researcher"

    async def test_cleanup_deletes_keys(self, streams, mock_redis):
        await streams.cleanup("grp-1", ["a1", "a2"])
        # Should delete: messages stream, control stream, agents hash
        mock_redis.delete.assert_called_once()
        deleted_keys = mock_redis.delete.call_args[0]
        assert len(deleted_keys) == 3

    async def test_send_message_with_recipient(self, streams, mock_redis):
        await streams.send_message(
            group_id="grp-1", sender_id="a1",
            message_type="request", payload={"need": "verify"},
            signature="sig", recipient_id="a2",
            correlation_id="corr-123",
        )
        call_args = mock_redis.xadd.call_args
        data = call_args[1].get("fields") or call_args[0][1]
        msg = json.loads(data["data"] if isinstance(data, dict) else data)
        assert msg.get("recipient_id") == "a2" or "a2" in str(call_args)
