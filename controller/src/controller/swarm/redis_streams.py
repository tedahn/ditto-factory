"""Redis Streams wrapper for swarm inter-agent communication."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from redis.asyncio import Redis
from redis.exceptions import ResponseError

logger = logging.getLogger(__name__)


class SwarmRedisStreams:
    """Manages Redis Streams for swarm agent communication."""

    def __init__(self, redis: Redis, maxlen: int = 10000):
        self._redis = redis
        self._maxlen = maxlen

    # --- Stream lifecycle ---

    async def create_group(self, group_id: str, agent_ids: list[str]) -> None:
        """Create streams and per-agent consumer groups for a swarm group."""
        msg_stream = f"swarm:{group_id}:messages"
        ctl_stream = f"swarm:{group_id}:control"

        for agent_id in agent_ids:
            group_name = f"agent-{agent_id}"
            for stream in (msg_stream, ctl_stream):
                try:
                    await self._redis.xgroup_create(
                        name=stream, groupname=group_name,
                        id="$", mkstream=True,
                    )
                except ResponseError as e:
                    if "BUSYGROUP" not in str(e):
                        raise

    async def create_agent_registry(
        self, group_id: str, agents: list,
    ) -> None:
        """Initialize agent registry hash with all agents as pending."""
        key = f"swarm:{group_id}:agents"
        for agent in agents:
            entry = {
                "role": agent.role,
                "status": "pending",
                "task_assignment": agent.task_assignment,
                "started_at": "",
                "last_seen": "",
            }
            await self._redis.hset(key, agent.id, json.dumps(entry))

    async def cleanup(self, group_id: str, agent_ids: list[str]) -> None:
        """Delete all Redis keys for a swarm group."""
        keys = [
            f"swarm:{group_id}:messages",
            f"swarm:{group_id}:control",
            f"swarm:{group_id}:agents",
        ]
        await self._redis.delete(*keys)

    # --- Messages ---

    async def send_message(
        self,
        group_id: str,
        sender_id: str,
        message_type: str,
        payload: dict,
        signature: str,
        recipient_id: str | None = None,
        correlation_id: str | None = None,
    ) -> str:
        """Send a message to the swarm stream."""
        message = {
            "id": uuid.uuid4().hex,
            "group_id": group_id,
            "sender_id": sender_id,
            "recipient_id": recipient_id,
            "message_type": message_type,
            "correlation_id": correlation_id,
            "payload": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "signature": signature,
        }
        stream_id = await self._redis.xadd(
            name=f"swarm:{group_id}:messages",
            fields={"data": json.dumps(message)},
            maxlen=self._maxlen,
            approximate=True,
        )
        return stream_id

    async def read_messages(
        self,
        group_id: str,
        agent_id: str,
        count: int = 10,
        block: int = 1000,
    ) -> list[dict]:
        """Read new messages for an agent from the stream."""
        group_name = f"agent-{agent_id}"
        stream_key = f"swarm:{group_id}:messages"

        entries = await self._safe_xreadgroup(
            group_name, agent_id, stream_key, count, block,
        )
        messages = []
        if entries:
            for stream, stream_entries in entries:
                ack_ids = []
                for entry_id, fields in stream_entries:
                    raw = fields.get(b"data") or fields.get("data")
                    if raw:
                        if isinstance(raw, bytes):
                            raw = raw.decode()
                        messages.append(json.loads(raw))
                    ack_ids.append(entry_id)
                if ack_ids:
                    await self._redis.xack(stream_key, group_name, *ack_ids)
        return messages

    async def read_control(
        self,
        group_id: str,
        agent_id: str,
        count: int = 10,
        block: int = 100,
    ) -> list[dict]:
        """Read control messages for an agent."""
        group_name = f"agent-{agent_id}"
        stream_key = f"swarm:{group_id}:control"
        entries = await self._safe_xreadgroup(
            group_name, agent_id, stream_key, count, block,
        )
        messages = []
        if entries:
            for stream, stream_entries in entries:
                ack_ids = []
                for entry_id, fields in stream_entries:
                    raw = fields.get(b"data") or fields.get("data")
                    if raw:
                        if isinstance(raw, bytes):
                            raw = raw.decode()
                        messages.append(json.loads(raw))
                    ack_ids.append(entry_id)
                if ack_ids:
                    await self._redis.xack(stream_key, group_name, *ack_ids)
        return messages

    async def send_control(
        self, group_id: str, message: dict,
    ) -> str:
        """Send a control message (controller -> agents)."""
        return await self._redis.xadd(
            name=f"swarm:{group_id}:control",
            fields={"data": json.dumps(message)},
            maxlen=self._maxlen,
            approximate=True,
        )

    # --- Agent registry ---

    async def update_agent_status(
        self, group_id: str, agent_id: str, status: str,
        **extra_fields,
    ) -> None:
        """Update an agent's status in the registry hash."""
        key = f"swarm:{group_id}:agents"
        raw = await self._redis.hget(key, agent_id)
        if raw:
            entry = json.loads(raw if isinstance(raw, str) else raw.decode())
        else:
            entry = {}
        entry["status"] = status
        if status == "active" and not entry.get("started_at"):
            entry["started_at"] = datetime.now(timezone.utc).isoformat()
        entry["last_seen"] = datetime.now(timezone.utc).isoformat()
        entry.update(extra_fields)
        await self._redis.hset(key, agent_id, json.dumps(entry))

    async def get_agent_registry(self, group_id: str) -> dict[str, dict]:
        """Get all agent entries from the registry hash."""
        key = f"swarm:{group_id}:agents"
        raw = await self._redis.hgetall(key)
        result = {}
        for k, v in raw.items():
            agent_id = k.decode() if isinstance(k, bytes) else k
            entry = json.loads(v.decode() if isinstance(v, bytes) else v)
            result[agent_id] = entry
        return result

    # --- Error recovery ---

    async def _safe_xreadgroup(
        self,
        group_name: str,
        consumer_name: str,
        stream_key: str,
        count: int = 10,
        block: int = 1000,
    ) -> list:
        """XREADGROUP with NOGROUP error recovery."""
        try:
            return await self._redis.xreadgroup(
                groupname=group_name,
                consumername=consumer_name,
                streams={stream_key: ">"},
                count=count,
                block=block,
            )
        except ResponseError as exc:
            if "NOGROUP" in str(exc):
                logger.warning(
                    "Consumer group %s not found for %s, recreating from 0",
                    group_name, stream_key,
                )
                await self._ensure_consumer_group(stream_key, group_name, "0")
                return await self._redis.xreadgroup(
                    groupname=group_name,
                    consumername=consumer_name,
                    streams={stream_key: ">"},
                    count=count,
                    block=block,
                )
            raise

    async def _ensure_consumer_group(
        self, stream_key: str, group_name: str, start_id: str = "$",
    ) -> None:
        """Create consumer group if it doesn't exist."""
        try:
            await self._redis.xgroup_create(
                name=stream_key, groupname=group_name,
                id=start_id, mkstream=True,
            )
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    # --- Full stream read (for audit trail) ---

    async def read_full_stream(self, group_id: str) -> list[dict]:
        """Read entire message stream (for audit trail at teardown)."""
        stream_key = f"swarm:{group_id}:messages"
        entries = await self._redis.xrange(stream_key)
        messages = []
        for entry_id, fields in entries:
            raw = fields.get(b"data") or fields.get("data")
            if raw:
                if isinstance(raw, bytes):
                    raw = raw.decode()
                messages.append(json.loads(raw))
        return messages

    async def set_ttl(self, group_id: str, ttl_seconds: int) -> None:
        """Set TTL on all swarm keys."""
        keys = [
            f"swarm:{group_id}:messages",
            f"swarm:{group_id}:control",
            f"swarm:{group_id}:agents",
        ]
        for key in keys:
            await self._redis.expire(key, ttl_seconds)
