"""HTTP adapter parsing for LLM and embedding providers."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from jobmatch_mcp.core.errors import EmbeddingError, LLMOutputError
from jobmatch_mcp.llm.embeddings import OllamaEmbeddingProvider
from jobmatch_mcp.llm.providers import AnthropicProvider, OllamaProvider, OpenAIProvider

SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


def client_for(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_ollama_structured_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        body = json.loads(request.content)
        assert body["model"] == "qwen"
        assert body["format"] == SCHEMA
        return httpx.Response(
            200,
            json={
                "message": {"content": json.dumps({"decisions": []})},
                "prompt_eval_count": 10,
                "eval_count": 5,
            },
        )

    client = client_for(handler)
    provider = OllamaProvider(
        base_url="http://ollama", model="qwen", timeout_seconds=1, client=client
    )
    result = await provider.generate_structured(
        system="s", user="u", schema=SCHEMA, schema_name="test"
    )
    assert result.data == {"decisions": []}
    assert result.tokens_in == 10
    assert result.tokens_out == 5
    assert result.cost_estimate == 0.0
    await client.aclose()


async def test_ollama_invalid_json_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "not json"}})

    client = client_for(handler)
    provider = OllamaProvider(
        base_url="http://ollama", model="qwen", timeout_seconds=1, client=client
    )
    with pytest.raises(LLMOutputError):
        await provider.generate_structured(system="s", user="u", schema=SCHEMA, schema_name="test")
    await client.aclose()


async def test_openai_structured_response_and_cost() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer key"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"decisions": []})}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    client = client_for(handler)
    provider = OpenAIProvider(
        api_key="key",
        base_url="http://openai/v1",
        model="gpt",
        timeout_seconds=1,
        input_cost_per_million=1.0,
        output_cost_per_million=2.0,
        client=client,
    )
    result = await provider.generate_structured(
        system="s", user="u", schema=SCHEMA, schema_name="test"
    )
    assert result.data == {"decisions": []}
    assert result.cost_estimate == pytest.approx(0.0002)
    await client.aclose()


async def test_anthropic_tool_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "key"
        body = json.loads(request.content)
        assert body["tool_choice"] == {"type": "tool", "name": "rerank"}
        return httpx.Response(
            200,
            json={
                "content": [{"type": "tool_use", "input": {"decisions": []}}],
                "usage": {"input_tokens": 20, "output_tokens": 10},
            },
        )

    client = client_for(handler)
    provider = AnthropicProvider(
        api_key="key",
        base_url="http://anthropic/v1",
        model="claude",
        timeout_seconds=1,
        max_output_tokens=100,
        input_cost_per_million=1.0,
        output_cost_per_million=2.0,
        client=client,
    )
    result = await provider.generate_structured(
        system="s", user="u", schema=SCHEMA, schema_name="rerank"
    )
    assert result.data == {"decisions": []}
    assert result.cost_estimate == pytest.approx(0.00004)
    await client.aclose()


async def test_ollama_embedding_dimensions_and_mapping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/embed"
        body = json.loads(request.content)
        assert body["input"] == ["a", "b"]
        return httpx.Response(200, json={"embeddings": [[0.1] * 768, [0.2] * 768]})

    client = client_for(handler)
    provider = OllamaEmbeddingProvider(
        base_url="http://ollama",
        model="nomic-embed-text",
        timeout_seconds=1,
        client=client,
    )
    vectors = await provider.embed(["a", "b"])
    assert len(vectors) == 2
    assert len(vectors[0]) == 768
    await client.aclose()


async def test_ollama_embedding_rejects_wrong_dimensions() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})

    client = client_for(handler)
    provider = OllamaEmbeddingProvider(
        base_url="http://ollama",
        model="nomic-embed-text",
        timeout_seconds=1,
        client=client,
    )
    with pytest.raises(EmbeddingError):
        await provider.embed(["a"])
    await client.aclose()
