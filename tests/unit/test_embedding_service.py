"""Embedding orchestration and degradation."""

from __future__ import annotations

from collections.abc import Sequence

from jobmatch_mcp.matching.dedupe import normalize_dedupe_key
from jobmatch_mcp.schemas import RawJob
from jobmatch_mcp.services.embedding_service import EmbeddingService


class FakeEmbeddingProvider:
    def __init__(self, vectors: list[list[float]] | None = None) -> None:
        self.model = "fake-embed"
        self.vectors = vectors or []
        self.error: Exception | None = None
        self.calls: list[list[str]] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.error is not None:
            raise self.error
        vectors = self.vectors[: len(texts)]
        self.vectors = self.vectors[len(texts) :]
        return vectors

    async def health(self) -> bool:
        return True

    async def close(self) -> None:
        return None


def make_job(external_id: str) -> RawJob:
    title = f"Engineer {external_id}"
    return RawJob(
        source="test",
        external_id=external_id,
        title=title,
        company="Acme",
        location="Remote",
        remote=True,
        description="Python and PostgreSQL.",
        url=f"https://example.com/{external_id}",
        dedupe_key=normalize_dedupe_key(title, "Acme", "Remote"),
    )


async def test_embed_resume_and_jobs() -> None:
    provider = FakeEmbeddingProvider(vectors=[[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
    service = EmbeddingService(provider)
    jobs = [make_job("1"), make_job("2")]

    resume, warnings = await service.embed_resume("resume text")
    outcome = await service.embed_jobs(jobs)

    assert warnings == []
    assert resume == [1.0, 0.0]
    assert outcome.model == "fake-embed"
    assert outcome.embeddings[jobs[0].dedupe_key] == [0.0, 1.0]
    assert outcome.embeddings[jobs[1].dedupe_key] == [1.0, 1.0]


async def test_embed_jobs_respects_limit() -> None:
    provider = FakeEmbeddingProvider(vectors=[[1.0], [2.0]])
    service = EmbeddingService(provider, max_jobs=2)
    outcome = await service.embed_jobs([make_job(str(index)) for index in range(3)])

    assert len(outcome.embeddings) == 2
    assert any("first 2 of 3" in warning for warning in outcome.warnings)


async def test_embedding_failure_degrades_with_warning() -> None:
    provider = FakeEmbeddingProvider()
    provider.error = RuntimeError("offline")
    service = EmbeddingService(provider)

    resume, warnings = await service.embed_resume("resume text")
    outcome = await service.embed_jobs([make_job("1")])

    assert resume is None
    assert any("structured scoring" in warning for warning in warnings)
    assert outcome.embeddings == {}
    assert any("structured scoring" in warning for warning in outcome.warnings)
