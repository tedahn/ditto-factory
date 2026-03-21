"""Embedding provider abstraction with Voyage-3 as primary implementation."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class EmbeddingError(Exception):
    """Raised when embedding generation fails."""

    pass


class EmbeddingProvider(ABC):
    """Abstract base for embedding providers."""

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        ...

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        ...

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Return the dimensionality of embeddings."""
        ...


class VoyageEmbeddingProvider(EmbeddingProvider):
    """Voyage-3 embedding provider using httpx."""

    VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"

    def __init__(self, api_key: str, model: str = "voyage-3"):
        self._api_key = api_key
        self._model = model
        self._dimensions = 1024  # voyage-3 output dim

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, text: str) -> list[float]:
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        import httpx

        if not self._api_key:
            raise EmbeddingError("Voyage API key not configured")

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.VOYAGE_API_URL,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "input": texts,
                        "model": self._model,
                        "input_type": "document",
                    },
                )
                response.raise_for_status()
                data = response.json()
                return [item["embedding"] for item in data["data"]]
        except httpx.HTTPStatusError as e:
            raise EmbeddingError(
                f"Voyage API error: {e.response.status_code}"
            ) from e
        except httpx.RequestError as e:
            raise EmbeddingError(f"Voyage API request failed: {e}") from e
        except (KeyError, IndexError) as e:
            raise EmbeddingError(
                f"Unexpected Voyage API response format: {e}"
            ) from e


class NoOpEmbeddingProvider(EmbeddingProvider):
    """No-op provider for Phase 1 (tag-based only) or when embeddings are disabled."""

    @property
    def dimensions(self) -> int:
        return 0

    async def embed(self, text: str) -> list[float]:
        raise EmbeddingError("Embeddings not configured (provider=none)")

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("Embeddings not configured (provider=none)")


def create_embedding_provider(settings) -> EmbeddingProvider:
    """Factory function to create the appropriate embedding provider."""
    provider = getattr(settings, "skill_embedding_provider", "none")
    if provider == "voyage":
        api_key = getattr(settings, "voyage_api_key", "")
        model = getattr(settings, "skill_embedding_model", "voyage-3")
        if not api_key:
            logger.warning(
                "Voyage API key not set, falling back to NoOp provider"
            )
            return NoOpEmbeddingProvider()
        return VoyageEmbeddingProvider(api_key=api_key, model=model)
    else:
        return NoOpEmbeddingProvider()
