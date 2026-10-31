"""Ordered provider router with circuit breakers and call accounting."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from opentelemetry import trace

from jobmatch_mcp.core.errors import LLMOutputError, LLMUnavailableError, describe_error
from jobmatch_mcp.core.telemetry import record_fallback, record_llm_call
from jobmatch_mcp.llm.base import LLMProvider, LLMResponse
from jobmatch_mcp.llm.circuit import CircuitBreaker
from jobmatch_mcp.schemas import LLMCallRecord, ProviderStatus


@dataclass(frozen=True)
class LLMRouteResult:
    data: Any
    provider: str
    model: str
    calls: list[LLMCallRecord]


def _failure_reason(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    return describe_error(exc)


class LLMRouter:
    def __init__(
        self,
        providers: Sequence[LLMProvider],
        *,
        hosted_fallback_enabled: bool,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self._providers = list(providers)
        self._hosted_fallback_enabled = hosted_fallback_enabled
        self._breakers = {
            provider.name: CircuitBreaker(
                failure_threshold=failure_threshold,
                cooldown_seconds=cooldown_seconds,
            )
            for provider in providers
        }
        self._last_success: dict[str, datetime] = {}
        self._last_failure: dict[str, datetime] = {}
        self._last_error: dict[str, str] = {}
        self._last_latency: dict[str, float] = {}
        self._tracer = trace.get_tracer(__name__)

    @property
    def providers(self) -> list[LLMProvider]:
        return list(self._providers)

    def _allowed(self, provider: LLMProvider) -> bool:
        if not provider.configured:
            return False
        return not provider.hosted or self._hosted_fallback_enabled

    def _allowed_providers(self) -> list[LLMProvider]:
        return [provider for provider in self._providers if self._allowed(provider)]

    async def _call_provider(
        self,
        provider: LLMProvider,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> LLMResponse:
        with self._tracer.start_as_current_span("llm.generate_structured") as span:
            span.set_attribute("llm.provider", provider.name)
            span.set_attribute("llm.model", provider.model)
            span.set_attribute("llm.hosted", provider.hosted)
            span.set_attribute("llm.schema", schema_name)
            started = time.perf_counter()
            try:
                response = await provider.generate_structured(
                    system=system,
                    user=user,
                    schema=schema,
                    schema_name=schema_name,
                )
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - started) * 1000
                span.record_exception(exc)
                span.set_status(trace.Status(trace.StatusCode.ERROR, type(exc).__name__))
                record_llm_call(
                    provider.name,
                    provider.model,
                    "error",
                    elapsed_ms,
                    0,
                    0,
                    0.0,
                )
                raise
            span.set_status(trace.Status(trace.StatusCode.OK))
            record_llm_call(
                provider.name,
                provider.model,
                "success",
                response.latency_ms,
                response.tokens_in,
                response.tokens_out,
                response.cost_estimate,
            )
            return response

    async def generate_structured(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> LLMRouteResult:
        allowed = self._allowed_providers()
        if not allowed:
            raise LLMUnavailableError(
                "No LLM provider is configured. Start Ollama or enable hosted fallback.",
                calls=[],
            )

        calls: list[LLMCallRecord] = []
        reasons: list[str] = []
        for provider in allowed:
            breaker = self._breakers[provider.name]
            if not breaker.allow_request():
                reason = "circuit_open"
                record_fallback(provider.name, reason)
                calls.append(self._call_record(provider, status="skipped", reason=reason))
                reasons.append(f"{provider.name}: {reason}")
                continue

            for attempt in range(2):
                try:
                    response = await self._call_provider(
                        provider,
                        system=system,
                        user=user,
                        schema=schema,
                        schema_name=schema_name,
                    )
                except asyncio.CancelledError:
                    raise
                except LLMOutputError as exc:
                    reason = str(exc)
                    breaker.record_failure()
                    self._record_failure(provider.name, reason)
                    record_fallback(provider.name, "invalid_output")
                    calls.append(self._call_record(provider, status="error", reason=reason))
                    reasons.append(f"{provider.name}: {reason}")
                    if attempt == 0:
                        continue
                    break
                except Exception as exc:
                    reason = _failure_reason(exc)
                    breaker.record_failure()
                    self._record_failure(provider.name, reason)
                    record_fallback(provider.name, reason)
                    calls.append(self._call_record(provider, status="error", reason=reason))
                    reasons.append(f"{provider.name}: {reason}")
                    break
                else:
                    breaker.record_success()
                    self._record_success(provider.name, response.latency_ms)
                    calls.append(
                        LLMCallRecord(
                            provider=provider.name,
                            model=provider.model,
                            latency_ms=response.latency_ms,
                            tokens_in=response.tokens_in,
                            tokens_out=response.tokens_out,
                            cost_estimate=response.cost_estimate,
                            status="success",
                            fallback_reason=None,
                        )
                    )
                    return LLMRouteResult(
                        data=response.data,
                        provider=provider.name,
                        model=provider.model,
                        calls=calls,
                    )

        raise LLMUnavailableError(
            "All LLM providers failed: " + "; ".join(reasons),
            calls=calls,
        )

    async def statuses(self, *, probe: bool = True) -> list[ProviderStatus]:
        health: dict[str, bool] = {}
        if probe:
            probed = [provider for provider in self._providers if self._allowed(provider)]
            results = await asyncio.gather(
                *(provider.health() for provider in probed),
                return_exceptions=True,
            )
            for provider, result in zip(probed, results, strict=True):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                health[provider.name] = not isinstance(result, BaseException) and result

        statuses: list[ProviderStatus] = []
        for index, provider in enumerate(self._providers):
            breaker = self._breakers[provider.name]
            configured = provider.configured
            allowed = self._allowed(provider)
            circuit_state = breaker.state.value
            available = allowed and circuit_state != "open" and health.get(provider.name, True)
            if not configured:
                detail = "not configured"
            elif provider.hosted and not self._hosted_fallback_enabled:
                detail = "hosted fallback disabled"
            elif circuit_state == "open":
                detail = "circuit open"
            elif probe and allowed and not health.get(provider.name, True):
                detail = "health check failed"
            else:
                detail = ""
            statuses.append(
                ProviderStatus(
                    name=provider.name,
                    role="primary" if index == 0 else "fallback",
                    configured=configured,
                    available=available,
                    circuit_state=circuit_state,
                    detail=detail,
                    consecutive_failures=breaker.consecutive_failures,
                    last_success_at=self._last_success.get(provider.name),
                    last_failure_at=self._last_failure.get(provider.name),
                    last_error=self._last_error.get(provider.name),
                    last_latency_ms=self._last_latency.get(provider.name),
                )
            )
        return statuses

    def _record_success(self, provider: str, latency_ms: float) -> None:
        self._last_success[provider] = datetime.now(UTC)
        self._last_latency[provider] = latency_ms
        self._last_error.pop(provider, None)

    def _record_failure(self, provider: str, reason: str) -> None:
        self._last_failure[provider] = datetime.now(UTC)
        self._last_error[provider] = reason

    @staticmethod
    def _call_record(
        provider: LLMProvider,
        *,
        status: Literal["success", "error", "skipped"],
        reason: str | None,
    ) -> LLMCallRecord:
        return LLMCallRecord(
            provider=provider.name,
            model=provider.model,
            latency_ms=0.0,
            tokens_in=0,
            tokens_out=0,
            cost_estimate=0.0,
            status=status,
            fallback_reason=reason,
        )

    async def close(self) -> None:
        for provider in self._providers:
            await provider.close()
