"""Process-wide dependencies shared by MCP tools."""

from __future__ import annotations

from functools import lru_cache

from jobmatch_mcp.connectors.registry import ConnectorRegistry
from jobmatch_mcp.core.config import get_settings
from jobmatch_mcp.db.session import dispose_engine
from jobmatch_mcp.llm.embeddings import OllamaEmbeddingProvider
from jobmatch_mcp.llm.providers import AnthropicProvider, OllamaProvider, OpenAIProvider
from jobmatch_mcp.llm.router import LLMRouter
from jobmatch_mcp.services.embedding_service import EmbeddingService
from jobmatch_mcp.services.rerank_service import RerankService


@lru_cache(maxsize=1)
def get_registry() -> ConnectorRegistry:
    return ConnectorRegistry.from_settings(get_settings())


@lru_cache(maxsize=1)
def get_embedding_service() -> EmbeddingService:
    settings = get_settings()
    return EmbeddingService(
        OllamaEmbeddingProvider(
            base_url=settings.embedding_base_url,
            model=settings.embedding_model,
            timeout_seconds=settings.embedding_timeout_seconds,
        ),
        max_jobs=settings.embedding_max_jobs,
    )


@lru_cache(maxsize=1)
def get_llm_router() -> LLMRouter:
    settings = get_settings()
    return LLMRouter(
        [
            OllamaProvider(
                base_url=settings.ollama_base_url,
                model=settings.ollama_model,
                timeout_seconds=settings.ollama_timeout_seconds,
                cost_per_million_tokens=settings.ollama_cost_per_million_tokens,
            ),
            OpenAIProvider(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                model=settings.openai_model,
                timeout_seconds=settings.openai_timeout_seconds,
                input_cost_per_million=settings.openai_input_cost_per_million,
                output_cost_per_million=settings.openai_output_cost_per_million,
            ),
            AnthropicProvider(
                api_key=settings.anthropic_api_key,
                base_url=settings.anthropic_base_url,
                model=settings.anthropic_model,
                timeout_seconds=settings.anthropic_timeout_seconds,
                max_output_tokens=settings.llm_max_output_tokens,
                input_cost_per_million=settings.anthropic_input_cost_per_million,
                output_cost_per_million=settings.anthropic_output_cost_per_million,
            ),
        ],
        hosted_fallback_enabled=settings.hosted_fallback_enabled,
        failure_threshold=settings.llm_max_consecutive_failures,
        cooldown_seconds=settings.llm_circuit_cooldown_seconds,
    )


@lru_cache(maxsize=1)
def get_rerank_service() -> RerankService:
    settings = get_settings()
    return RerankService(
        get_llm_router(),
        top_n=settings.rerank_top_n,
        max_adjustment=settings.rerank_max_adjustment,
        min_score=settings.min_match_score,
    )


async def close_dependencies() -> None:
    registry = get_registry()
    await registry.close()
    get_registry.cache_clear()

    embedding_service = get_embedding_service()
    await embedding_service.close()
    get_embedding_service.cache_clear()

    router = get_llm_router()
    await router.close()
    get_llm_router.cache_clear()
    get_rerank_service.cache_clear()

    await dispose_engine()
