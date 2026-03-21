"""In-memory LRU cache for task embeddings to reduce API calls."""

from __future__ import annotations

import hashlib
from collections import OrderedDict


class EmbeddingCache:
    """LRU cache for task embeddings to reduce API calls."""

    def __init__(self, max_size: int = 1000):
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._max_size = max_size

    def _key(self, text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def get(self, text: str) -> list[float] | None:
        key = self._key(text)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, text: str, embedding: list[float]) -> None:
        key = self._key(text)
        self._cache[key] = embedding
        self._cache.move_to_end(key)
        if len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)
