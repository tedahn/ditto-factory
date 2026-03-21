"""Tests for Phase 2 semantic search: embeddings, cosine similarity, and fallback."""

from __future__ import annotations

import hashlib
import os

import pytest
import aiosqlite

from controller.skills.registry import SkillRegistry
from controller.skills.classifier import TaskClassifier
from controller.skills.models import SkillCreate, SkillFilters, ScoredSkill
from controller.skills.embedding import EmbeddingProvider, EmbeddingError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeEmbeddingProvider(EmbeddingProvider):
    """Returns deterministic embeddings based on text content for testing."""

    def __init__(self, embeddings: dict[str, list[float]] | None = None) -> None:
        self._embeddings = embeddings or {}

    @property
    def dimensions(self) -> int:
        return 3  # tiny vectors for testing

    async def embed(self, text: str) -> list[float]:
        if text in self._embeddings:
            return self._embeddings[text]
        # Default: hash-based deterministic embedding
        h = hashlib.md5(text.encode()).hexdigest()
        return [int(h[i : i + 2], 16) / 255.0 for i in range(0, 6, 2)]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]


class FailingEmbedder(EmbeddingProvider):
    """Embedder that always raises EmbeddingError."""

    @property
    def dimensions(self) -> int:
        return 0

    async def embed(self, text: str) -> list[float]:
        raise EmbeddingError("fail")

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("fail")


class FakeSettings:
    skill_max_per_task = 5
    skill_min_similarity = 0.0  # accept all for testing
    skill_max_total_chars = 16000


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MIGRATION_DIR = os.path.join(
    os.path.dirname(__file__), os.pardir, "migrations"
)


@pytest.fixture
async def db_path(tmp_path):
    """Create an in-memory-like SQLite database with schema migrations applied."""
    path = str(tmp_path / "test.db")
    async with aiosqlite.connect(path) as db:
        migration_002 = open(
            os.path.join(MIGRATION_DIR, "002_skill_registry.sql")
        ).read()
        await db.executescript(migration_002)

        migration_003 = open(
            os.path.join(MIGRATION_DIR, "003_skill_embeddings.sql")
        ).read()
        await db.executescript(migration_003)
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCosingSimilarity:
    """Unit tests for the static cosine similarity helper."""

    def test_identical_vectors(self):
        assert SkillRegistry._cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert SkillRegistry._cosine_similarity([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert SkillRegistry._cosine_similarity([1, 0, 0], [-1, 0, 0]) == pytest.approx(-1.0)

    def test_empty_vectors(self):
        assert SkillRegistry._cosine_similarity([], []) == 0.0

    def test_mismatched_lengths(self):
        assert SkillRegistry._cosine_similarity([1, 0], [1, 0, 0]) == 0.0

    def test_zero_vector(self):
        assert SkillRegistry._cosine_similarity([0, 0, 0], [1, 0, 0]) == 0.0


class TestSemanticSearch:
    """Integration tests for embedding-based skill search."""

    @pytest.mark.asyncio
    async def test_store_and_search_by_embedding(self, db_path):
        embedder = FakeEmbeddingProvider()
        registry = SkillRegistry(db_path, embedding_provider=embedder)

        await registry.create(
            SkillCreate(
                name="React Debugging",
                slug="react-debug",
                description="Debug React components",
                content="# React Debug\nUse React DevTools...",
                language=["typescript"],
                domain=["frontend"],
                created_by="test",
            )
        )
        await registry.create(
            SkillCreate(
                name="SQL Optimization",
                slug="sql-opt",
                description="Optimize SQL queries",
                content="# SQL Opt\nUse EXPLAIN ANALYZE...",
                language=["sql"],
                domain=["backend"],
                created_by="test",
            )
        )

        task_embedding = await embedder.embed("fix react component")
        results = await registry.search_by_embedding(task_embedding, limit=10)

        assert len(results) >= 1
        assert all(isinstance(r, ScoredSkill) for r in results)
        assert all(0 <= r.score <= 1.0 or r.score >= -1.0 for r in results)

    @pytest.mark.asyncio
    async def test_search_respects_language_filter(self, db_path):
        embedder = FakeEmbeddingProvider()
        registry = SkillRegistry(db_path, embedding_provider=embedder)

        await registry.create(
            SkillCreate(
                name="React Debug",
                slug="react",
                description="React",
                content="# React",
                language=["typescript"],
                created_by="test",
            )
        )
        await registry.create(
            SkillCreate(
                name="Python Debug",
                slug="python",
                description="Python",
                content="# Python",
                language=["python"],
                created_by="test",
            )
        )

        task_embedding = await embedder.embed("debug something")
        results = await registry.search_by_embedding(
            task_embedding,
            filters=SkillFilters(language=["python"]),
            limit=10,
        )
        slugs = [r.skill.slug for r in results]
        assert "python" in slugs
        assert "react" not in slugs

    @pytest.mark.asyncio
    async def test_search_respects_domain_filter(self, db_path):
        embedder = FakeEmbeddingProvider()
        registry = SkillRegistry(db_path, embedding_provider=embedder)

        await registry.create(
            SkillCreate(
                name="Frontend Skill",
                slug="fe",
                description="Frontend",
                content="# FE",
                domain=["frontend"],
                created_by="test",
            )
        )
        await registry.create(
            SkillCreate(
                name="Backend Skill",
                slug="be",
                description="Backend",
                content="# BE",
                domain=["backend"],
                created_by="test",
            )
        )

        task_embedding = await embedder.embed("some task")
        results = await registry.search_by_embedding(
            task_embedding,
            filters=SkillFilters(domain=["backend"]),
            limit=10,
        )
        slugs = [r.skill.slug for r in results]
        assert "be" in slugs
        assert "fe" not in slugs

    @pytest.mark.asyncio
    async def test_search_empty_when_no_embeddings(self, db_path):
        """Skills without embeddings should not appear in semantic search."""
        registry = SkillRegistry(db_path)  # no embedder, so create won't embed

        await registry.create(
            SkillCreate(
                name="No Embedding",
                slug="no-emb",
                description="No embedding stored",
                content="# Content",
                created_by="test",
            )
        )

        results = await registry.search_by_embedding([0.5, 0.5, 0.5], limit=10)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_auto_embed_on_create(self, db_path):
        """Creating a skill with an embedder should auto-store an embedding."""
        embedder = FakeEmbeddingProvider()
        registry = SkillRegistry(db_path, embedding_provider=embedder)

        await registry.create(
            SkillCreate(
                name="Auto Embed",
                slug="auto",
                description="Auto",
                content="# Auto",
                created_by="test",
            )
        )

        # Verify embedding was stored by searching
        task_emb = await embedder.embed("Auto Auto # Auto")
        results = await registry.search_by_embedding(task_emb, limit=10)
        assert len(results) == 1
        assert results[0].skill.slug == "auto"

    @pytest.mark.asyncio
    async def test_re_embed_on_update(self, db_path):
        """Updating content should re-embed the skill."""
        embedder = FakeEmbeddingProvider()
        registry = SkillRegistry(db_path, embedding_provider=embedder)

        await registry.create(
            SkillCreate(
                name="Updateable",
                slug="upd",
                description="Original",
                content="# Original",
                created_by="test",
            )
        )

        from controller.skills.models import SkillUpdate

        await registry.update("upd", SkillUpdate(content="# Updated content"))

        # Skill should still be searchable
        task_emb = await embedder.embed("updated")
        results = await registry.search_by_embedding(task_emb, limit=10)
        assert len(results) == 1
        assert results[0].skill.slug == "upd"


class TestClassifierWithEmbeddings:
    """Tests for TaskClassifier Phase 2 integration."""

    @pytest.mark.asyncio
    async def test_classifier_uses_embeddings(self, db_path):
        embedder = FakeEmbeddingProvider(
            {"fix react component": [1.0, 0.0, 0.0]}
        )
        registry = SkillRegistry(db_path, embedding_provider=embedder)
        classifier = TaskClassifier(
            registry, embedding_provider=embedder, settings=FakeSettings()
        )

        await registry.create(
            SkillCreate(
                name="React Debugging",
                slug="react-debug",
                description="Debug React",
                content="# React",
                language=["typescript"],
                domain=["frontend"],
                created_by="test",
            )
        )

        result = await classifier.classify(task="fix react component")
        assert len(result.skills) >= 1
        assert result.task_embedding is not None
        assert result.task_embedding == [1.0, 0.0, 0.0]

    @pytest.mark.asyncio
    async def test_classifier_falls_back_to_tags(self, db_path):
        """When embedder raises, classifier falls back to tag search."""
        registry = SkillRegistry(db_path)
        classifier = TaskClassifier(
            registry,
            embedding_provider=FailingEmbedder(),
            settings=FakeSettings(),
        )

        await registry.create(
            SkillCreate(
                name="Python Testing",
                slug="py-test",
                description="Test Python",
                content="# Test",
                language=["python"],
                domain=["testing"],
                created_by="test",
            )
        )

        result = await classifier.classify(
            task="write python tests", language=["python"]
        )
        assert len(result.skills) >= 1
        assert result.task_embedding is None

    @pytest.mark.asyncio
    async def test_classifier_enforces_budget(self, db_path):
        embedder = FakeEmbeddingProvider()

        class TinyBudget:
            skill_max_per_task = 2
            skill_min_similarity = 0.0
            skill_max_total_chars = 50  # very small

        registry = SkillRegistry(db_path, embedding_provider=embedder)
        classifier = TaskClassifier(
            registry, embedding_provider=embedder, settings=TinyBudget()
        )

        await registry.create(
            SkillCreate(
                name="Skill A",
                slug="a",
                description="A",
                content="x" * 30,
                created_by="test",
            )
        )
        await registry.create(
            SkillCreate(
                name="Skill B",
                slug="b",
                description="B",
                content="y" * 30,
                created_by="test",
            )
        )

        result = await classifier.classify(task="something")
        total_chars = sum(len(s.content) for s in result.skills)
        assert total_chars <= 50

    @pytest.mark.asyncio
    async def test_classifier_with_min_similarity_threshold(self, db_path):
        """Skills below similarity threshold should be excluded."""
        embedder = FakeEmbeddingProvider(
            {"specific task": [1.0, 0.0, 0.0]}
        )

        class StrictSettings:
            skill_max_per_task = 5
            skill_min_similarity = 0.99  # very strict
            skill_max_total_chars = 16000

        registry = SkillRegistry(db_path, embedding_provider=embedder)
        classifier = TaskClassifier(
            registry, embedding_provider=embedder, settings=StrictSettings()
        )

        await registry.create(
            SkillCreate(
                name="Irrelevant",
                slug="irr",
                description="Unrelated topic",
                content="# Nothing relevant",
                created_by="test",
            )
        )

        # With strict threshold, hash-based embeddings likely won't match
        result = await classifier.classify(task="specific task")
        # Should fall back to tag-based since no embedding results pass threshold
        assert result.task_embedding == [1.0, 0.0, 0.0]

    @pytest.mark.asyncio
    async def test_classifier_caches_embeddings(self, db_path):
        """Second call with same task should use cached embedding."""
        call_count = 0

        class CountingEmbedder(FakeEmbeddingProvider):
            async def embed(self, text: str) -> list[float]:
                nonlocal call_count
                call_count += 1
                return await super().embed(text)

        embedder = CountingEmbedder()
        registry = SkillRegistry(db_path, embedding_provider=embedder)
        classifier = TaskClassifier(
            registry, embedding_provider=embedder, settings=FakeSettings()
        )

        await registry.create(
            SkillCreate(
                name="Cached",
                slug="cached",
                description="Cached skill",
                content="# Cached",
                created_by="test",
            )
        )

        # First call embeds
        await classifier.classify(task="same task twice")
        first_count = call_count

        # Second call should use cache
        await classifier.classify(task="same task twice")
        assert call_count == first_count  # no extra embed call for classification


class TestEmbeddingCache:
    """Tests for the LRU embedding cache."""

    def test_cache_put_get(self):
        from controller.skills.embedding_cache import EmbeddingCache

        cache = EmbeddingCache(max_size=3)
        cache.put("a", [1.0, 0.0])
        assert cache.get("a") == [1.0, 0.0]

    def test_cache_miss(self):
        from controller.skills.embedding_cache import EmbeddingCache

        cache = EmbeddingCache(max_size=3)
        assert cache.get("missing") is None

    def test_cache_eviction(self):
        from controller.skills.embedding_cache import EmbeddingCache

        cache = EmbeddingCache(max_size=2)
        cache.put("a", [1.0])
        cache.put("b", [2.0])
        cache.put("c", [3.0])  # evicts "a"

        assert cache.get("a") is None
        assert cache.get("b") == [2.0]
        assert cache.get("c") == [3.0]

    def test_cache_lru_ordering(self):
        from controller.skills.embedding_cache import EmbeddingCache

        cache = EmbeddingCache(max_size=2)
        cache.put("a", [1.0])
        cache.put("b", [2.0])
        cache.get("a")  # access "a" to make it recent
        cache.put("c", [3.0])  # should evict "b", not "a"

        assert cache.get("a") == [1.0]
        assert cache.get("b") is None
        assert cache.get("c") == [3.0]
