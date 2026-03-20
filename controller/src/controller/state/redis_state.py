from __future__ import annotations
import json
from redis.asyncio import Redis

TASK_TTL = 3600
RESULT_TTL = 3600


class RedisState:
    def __init__(self, redis: Redis):
        self._redis = redis

    async def push_task(self, thread_id: str, task_context: dict) -> None:
        await self._redis.set(f"task:{thread_id}", json.dumps(task_context), ex=TASK_TTL)

    async def get_task(self, thread_id: str) -> dict | None:
        raw = await self._redis.get(f"task:{thread_id}")
        return json.loads(raw) if raw else None

    async def push_result(self, thread_id: str, result: dict) -> None:
        await self._redis.set(f"result:{thread_id}", json.dumps(result), ex=RESULT_TTL)

    async def get_result(self, thread_id: str) -> dict | None:
        raw = await self._redis.get(f"result:{thread_id}")
        return json.loads(raw) if raw else None

    async def queue_message(self, thread_id: str, message: str) -> None:
        await self._redis.rpush(f"queue:{thread_id}", message)

    async def drain_messages(self, thread_id: str) -> list[str]:
        key = f"queue:{thread_id}"
        pipe = self._redis.pipeline()
        pipe.lrange(key, 0, -1)
        pipe.delete(key)
        results = await pipe.execute()
        return [m.decode() if isinstance(m, bytes) else m for m in results[0]]

    async def append_stream_event(self, thread_id: str, event: str) -> None:
        await self._redis.xadd(f"agent:{thread_id}", {"event": event})

    async def read_stream(self, thread_id: str, last_id: str = "0") -> list[tuple[str, dict]]:
        entries = await self._redis.xrange(f"agent:{thread_id}", min=last_id)
        return [
            (
                eid.decode() if isinstance(eid, bytes) else eid,
                {
                    k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
                    for k, v in data.items()
                },
            )
            for eid, data in entries
        ]
