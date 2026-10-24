"""HTTP adapters for Ollama, OpenAI, and Anthropic."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from jobmatch_mcp.core.errors import LLMOutputError
from jobmatch_mcp.llm.base import LLMResponse


def _estimate_cost(
    tokens_in: int,
    tokens_out: int,
    input_cost_per_million: float,
    output_cost_per_million: float,
) -> float:
    return (
        tokens_in / 1_000_000 * input_cost_per_million
        + tokens_out / 1_000_000 * output_cost_per_million
    )


def _json_content(content: str) -> Any:
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LLMOutputError("Provider returned invalid JSON") from exc


class OllamaProvider:
    name = "ollama"
    hosted = False

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float,
        cost_per_million_tokens: float = 0.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._cost_per_million_tokens = cost_per_million_tokens
        self._client = client
        self._owns_client = client is None

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self.model)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_seconds)
        return self._client

    async def generate_structured(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> LLMResponse:
        del schema_name
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": schema,
            "options": {"temperature": 0},
        }
        started = time.perf_counter()
        response = await self._http().post(f"{self._base_url}/api/chat", json=payload)
        response.raise_for_status()
        latency_ms = (time.perf_counter() - started) * 1000
        body = response.json()
        message = body.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise LLMOutputError("Ollama response did not contain message content")
        tokens_in = int(body.get("prompt_eval_count") or 0)
        tokens_out = int(body.get("eval_count") or 0)
        return LLMResponse(
            data=_json_content(content),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cost_estimate=_estimate_cost(
                tokens_in,
                tokens_out,
                self._cost_per_million_tokens,
                self._cost_per_million_tokens,
            ),
        )

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


class OpenAIProvider:
    name = "openai"
    hosted = True

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        model: str,
        timeout_seconds: float,
        input_cost_per_million: float,
        output_cost_per_million: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._input_cost = input_cost_per_million
        self._output_cost = output_cost_per_million
        self._client = client
        self._owns_client = client is None

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_seconds)
        return self._client

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key or ''}"}

    async def generate_structured(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> LLMResponse:
        del schema_name
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        started = time.perf_counter()
        response = await self._http().post(
            f"{self._base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
        )
        response.raise_for_status()
        latency_ms = (time.perf_counter() - started) * 1000
        body = response.json()
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMOutputError("OpenAI response did not contain choices")
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise LLMOutputError("OpenAI response did not contain message content")
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        tokens_in = int(usage.get("prompt_tokens") or 0)
        tokens_out = int(usage.get("completion_tokens") or 0)
        return LLMResponse(
            data=_json_content(content),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cost_estimate=_estimate_cost(
                tokens_in, tokens_out, self._input_cost, self._output_cost
            ),
        )

    async def health(self) -> bool:
        try:
            response = await self._http().get(
                f"{self._base_url}/models", headers=self._headers()
            )
        except httpx.HTTPError:
            return False
        return response.is_success

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None


class AnthropicProvider:
    name = "anthropic"
    hosted = True

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_output_tokens: int,
        input_cost_per_million: float,
        output_cost_per_million: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._input_cost = input_cost_per_million
        self._output_cost = output_cost_per_million
        self._client = client
        self._owns_client = client is None

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_seconds)
        return self._client

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key or "",
            "anthropic-version": "2023-06-01",
        }

    async def generate_structured(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> LLMResponse:
        payload = {
            "model": self.model,
            "max_tokens": self._max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "tools": [
                {
                    "name": schema_name,
                    "description": "Return the structured re-rank decisions.",
                    "input_schema": schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": schema_name},
        }
        started = time.perf_counter()
        response = await self._http().post(
            f"{self._base_url}/messages",
            headers=self._headers(),
            json=payload,
        )
        response.raise_for_status()
        latency_ms = (time.perf_counter() - started) * 1000
        body = response.json()
        blocks = body.get("content")
        if not isinstance(blocks, list):
            raise LLMOutputError("Anthropic response did not contain content")
        tool_input: Any = None
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_input = block.get("input")
                break
        if tool_input is None:
            raise LLMOutputError("Anthropic response did not contain a tool result")
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        tokens_in = int(usage.get("input_tokens") or 0)
        tokens_out = int(usage.get("output_tokens") or 0)
        return LLMResponse(
            data=tool_input,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cost_estimate=_estimate_cost(
                tokens_in, tokens_out, self._input_cost, self._output_cost
            ),
        )

    async def health(self) -> bool:
        try:
            response = await self._http().get(
                f"{self._base_url}/models", headers=self._headers()
            )
        except httpx.HTTPError:
            return False
        return response.is_success

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
