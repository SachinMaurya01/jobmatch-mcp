"""Local embedding orchestration with graceful degradation."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field

from jobmatch_mcp.core.errors import describe_error
from jobmatch_mcp.core.logging import get_logger
from jobmatch_mcp.llm.embeddings import EmbeddingProvider
from jobmatch_mcp.schemas import RawJob

logger = get_logger(__name__)


@dataclass(frozen=True)
class EmbeddingOutcome:
    embeddings: dict[str, list[float]] = field(default_factory=dict)
    model: str | None = None
    warnings: list[str] = field(default_factory=list)


class EmbeddingService:
    def __init__(
        self,
        provider: EmbeddingProvider,
        *,
        max_jobs: int = 100,
        max_chars: int = 6000,
    ) -> None:
        self._provider = provider
        self._max_jobs = max_jobs
        self._max_chars = max_chars

    @property
    def model(self) -> str:
        return self._provider.model

    async def embed_resume(self, text: str) -> tuple[list[float] | None, list[str]]:
        try:
            vectors = await self._provider.embed([text[: self._max_chars]])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            detail = describe_error(exc)
            logger.warning(
                "embeddings.resume_failed",
                extra={"fields": {"model": self.model, "error": detail}},
            )
            return None, [f"Resume embedding unavailable ({detail}); using structured scoring."]
        if len(vectors) != 1:
            return None, ["Resume embedding provider returned an unexpected result."]
        return vectors[0], []

    async def embed_jobs(self, jobs: Sequence[RawJob]) -> EmbeddingOutcome:
        if not jobs:
            return EmbeddingOutcome(model=self.model)
        selected = list(jobs[: self._max_jobs])
        warnings: list[str] = []
        if len(jobs) > len(selected):
            warnings.append(
                f"Embedded the first {len(selected)} of {len(jobs)} postings; "
                "the rest use structured scoring only."
            )
        texts = [self._job_text(job) for job in selected]
        try:
            vectors = await self._provider.embed(texts)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            detail = describe_error(exc)
            logger.warning(
                "embeddings.jobs_failed",
                extra={"fields": {"model": self.model, "error": detail}},
            )
            return EmbeddingOutcome(
                model=self.model,
                warnings=[
                    f"Posting embeddings unavailable ({detail}); using structured scoring."
                ],
            )
        if len(vectors) != len(selected):
            return EmbeddingOutcome(
                model=self.model,
                warnings=["Embedding provider returned an unexpected result count."],
            )
        return EmbeddingOutcome(
            embeddings={
                job.dedupe_key: vector for job, vector in zip(selected, vectors, strict=True)
            },
            model=self.model,
            warnings=warnings,
        )

    def _job_text(self, job: RawJob) -> str:
        parts = [job.title, job.company, job.location or "", job.description[: self._max_chars]]
        return "\n".join(part for part in parts if part)

    async def close(self) -> None:
        await self._provider.close()
