"""Provider interfaces shared by the LLM router and embedding service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class LLMResponse:
    data: Any
    tokens_in: int
    tokens_out: int
    latency_ms: float
    cost_estimate: float


class LLMProvider(Protocol):
    name: str
    model: str
    hosted: bool

    @property
    def configured(self) -> bool: ...

    async def generate_structured(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> LLMResponse: ...

    async def health(self) -> bool: ...

    async def close(self) -> None: ...
