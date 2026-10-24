"""Local embedding provider backed by Ollama."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import httpx

from jobmatch_mcp.core.errors import EmbeddingError
from jobmatch_mcp.db.models import EMBEDDING_DIMENSIONS


class EmbeddingProvider(Protocol):
    model: str

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def health(self) -> bool: ...

    async def close(self) -> None: ...


class OllamaEmbeddingProvider:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float,
        dimensions: int = EMBEDDING_DIMENSIONS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._dimensions = dimensions
        self._client = client
        self._owns_client = client is None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_seconds)
        return self._client

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {"model": self.model, "input": list(texts)}
        response = await self._http().post(f"{self._base_url}/api/embed", json=payload)
        response.raise_for_status()
        body = response.json()
        raw = body.get("embeddings")
        if not isinstance(raw, list) or len(raw) != len(texts):
            raise EmbeddingError("Embedding provider returned an unexpected result count")
        embeddings: list[list[float]] = []
        for vector in raw:
            if not isinstance(vector, list) or len(vector) != self._dimensions:
                raise EmbeddingError(
                    f"Embedding provider returned a vector that is not {self._dimensions} wide"
                )
            embeddings.append([float(value) for value in vector])
        return embeddings

    async def health(self) -> bool:
        try:
            response = await self._http().get(f"{self._base_url}/api/tags")
        except httpx.HTTPError:
            return False
        return response.is_success

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
