import pytest

from controller.skills.embedding import (
    EmbeddingError,
    NoOpEmbeddingProvider,
    VoyageEmbeddingProvider,
    create_embedding_provider,
)
from controller.skills.embedding_cache import EmbeddingCache


class TestNoOpProvider:
    @pytest.mark.asyncio
    async def test_embed_raises(self):
        provider = NoOpEmbeddingProvider()
        with pytest.raises(EmbeddingError):
            await provider.embed("hello")

    @pytest.mark.asyncio
    async def test_embed_batch_raises(self):
        provider = NoOpEmbeddingProvider()
        with pytest.raises(EmbeddingError):
            await provider.embed_batch(["hello"])

    def test_dimensions_zero(self):
        provider = NoOpEmbeddingProvider()
        assert provider.dimensions == 0


class TestCreateProvider:
    def test_default_is_noop(self):
        class FakeSettings:
            skill_embedding_provider = "none"

        provider = create_embedding_provider(FakeSettings())
        assert isinstance(provider, NoOpEmbeddingProvider)

    def test_voyage_without_key_falls_back(self):
        class FakeSettings:
            skill_embedding_provider = "voyage"
            voyage_api_key = ""
            skill_embedding_model = "voyage-3"

        provider = create_embedding_provider(FakeSettings())
        assert isinstance(provider, NoOpEmbeddingProvider)

    def test_voyage_with_key(self):
        class FakeSettings:
            skill_embedding_provider = "voyage"
            voyage_api_key = "test-key"
            skill_embedding_model = "voyage-3"

        provider = create_embedding_provider(FakeSettings())
        assert isinstance(provider, VoyageEmbeddingProvider)


class TestEmbeddingCache:
    def test_put_and_get(self):
        cache = EmbeddingCache()
        cache.put("hello", [1.0, 2.0])
        assert cache.get("hello") == [1.0, 2.0]

    def test_miss(self):
        cache = EmbeddingCache()
        assert cache.get("missing") is None

    def test_lru_eviction(self):
        cache = EmbeddingCache(max_size=2)
        cache.put("a", [1.0])
        cache.put("b", [2.0])
        cache.put("c", [3.0])  # evicts "a"
        assert cache.get("a") is None
        assert cache.get("b") == [2.0]
        assert cache.get("c") == [3.0]

    def test_access_refreshes(self):
        cache = EmbeddingCache(max_size=2)
        cache.put("a", [1.0])
        cache.put("b", [2.0])
        cache.get("a")  # refresh "a"
        cache.put("c", [3.0])  # evicts "b", not "a"
        assert cache.get("a") == [1.0]
        assert cache.get("b") is None

    def test_clear(self):
        cache = EmbeddingCache()
        cache.put("a", [1.0])
        cache.clear()
        assert len(cache) == 0

    def test_len(self):
        cache = EmbeddingCache()
        cache.put("a", [1.0])
        cache.put("b", [2.0])
        assert len(cache) == 2
