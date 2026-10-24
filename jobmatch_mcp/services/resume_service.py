"""Resume ingestion: read, parse, and persist a profile."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from jobmatch_mcp.core.errors import InvalidInputError
from jobmatch_mcp.db.models import Resume
from jobmatch_mcp.db.repositories import create_resume, get_or_create_default_user
from jobmatch_mcp.schemas import ResumeProfile
from jobmatch_mcp.services.embedding_service import EmbeddingService
from jobmatch_mcp.services.resume_parser import parse_resume_text, read_resume_text


class ResumeService:
    def __init__(
        self,
        max_resume_bytes: int,
        *,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        self._max_resume_bytes = max_resume_bytes
        self._embedding_service = embedding_service

    async def parse_and_store(
        self,
        session: AsyncSession,
        *,
        path: str | None = None,
        text: str | None = None,
    ) -> tuple[Resume, ResumeProfile, list[str]]:
        if (path is None) == (text is None):
            raise InvalidInputError("Provide exactly one of 'path' or 'text'.")

        raw_text = (
            read_resume_text(Path(path), self._max_resume_bytes)
            if path is not None
            else (text or "")
        )
        if not raw_text.strip():
            raise InvalidInputError("Resume text is empty")

        profile = parse_resume_text(raw_text)
        embedding: list[float] | None = None
        embedding_model: str | None = None
        warnings: list[str] = []
        if self._embedding_service is not None:
            embedding, warnings = await self._embedding_service.embed_resume(raw_text)
            if embedding is not None:
                embedding_model = self._embedding_service.model

        user = await get_or_create_default_user(session)
        resume = await create_resume(
            session,
            user.id,
            raw_text,
            profile,
            embedding=embedding,
            embedding_model=embedding_model,
        )
        return resume, profile, warnings
