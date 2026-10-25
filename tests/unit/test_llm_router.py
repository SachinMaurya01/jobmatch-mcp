"""Router ordering, fallback, retries, and circuit behavior."""

from __future__ import annotations

from typing import Any

import pytest

from jobmatch_mcp.core.errors import LLMOutputError, LLMUnavailableError
from jobmatch_mcp.llm.base import LLMResponse
from jobmatch_mcp.llm.router import LLMRouter

SCHEMA: dict[str, Any] = {"type": "object"}


class FakeProvider:
    def __init__(
        self,
        name: str,
        *,
        hosted: bool = False,
        configured: bool = True,
        responses: list[LLMResponse | BaseException] | None = None,
    ) -> None:
        self.name = name
        self.model = f"{name}-model"
        self.hosted = hosted
        self._configured = configured
        self.responses = responses or []
        self.calls = 0

    @property
    def configured(self) -> bool:
        return self._configured

    async def generate_structured(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> LLMResponse:
        del system, user, schema, schema_name
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def health(self) -> bool:
        return True

    async def close(self) -> None:
        return None


def response(provider: str = "ok") -> LLMResponse:
    return LLMResponse(
        data={"decisions": []},
        tokens_in=10,
        tokens_out=5,
        latency_ms=12.0,
        cost_estimate=0.001,
    )


def make_router(primary: FakeProvider, fallback: FakeProvider | None = None) -> LLMRouter:
    providers = [primary] if fallback is None else [primary, fallback]
    return LLMRouter(
        providers,
        hosted_fallback_enabled=True,
        failure_threshold=2,
        cooldown_seconds=60,
    )


async def test_falls_back_to_next_provider() -> None:
    primary = FakeProvider("primary", responses=[RuntimeError("down")])
    fallback = FakeProvider("fallback", hosted=True, responses=[response()])
    router = make_router(primary, fallback)

    result = await router.generate_structured(
        system="s", user="u", schema=SCHEMA, schema_name="test"
    )

    assert result.provider == "fallback"
    assert [call.status for call in result.calls] == ["error", "success"]
    assert result.calls[0].fallback_reason == "RuntimeError"
    assert primary.calls == 1
    assert fallback.calls == 1


async def test_hosted_fallback_disabled_blocks_hosted_provider() -> None:
    primary = FakeProvider("primary", responses=[RuntimeError("down")])
    fallback = FakeProvider("fallback", hosted=True, responses=[response()])
    router = LLMRouter(
        [primary, fallback],
        hosted_fallback_enabled=False,
    )

    with pytest.raises(LLMUnavailableError) as exc_info:
        await router.generate_structured(system="s", user="u", schema=SCHEMA, schema_name="test")

    assert fallback.calls == 0
    assert exc_info.value.calls[0].provider == "primary"
    statuses = {status.name: status for status in await router.statuses(probe=False)}
    assert statuses["fallback"].available is False
    assert statuses["fallback"].detail == "hosted fallback disabled"


async def test_retries_once_on_invalid_output() -> None:
    primary = FakeProvider(
        "primary",
        responses=[LLMOutputError("bad json"), response()],
    )
    router = make_router(primary)

    result = await router.generate_structured(
        system="s", user="u", schema=SCHEMA, schema_name="test"
    )

    assert result.provider == "primary"
    assert [call.status for call in result.calls] == ["error", "success"]
    assert primary.calls == 2


async def test_circuit_opens_and_skips_provider() -> None:
    primary = FakeProvider(
        "primary",
        responses=[RuntimeError("down"), RuntimeError("down")],
    )
    router = LLMRouter(
        [primary],
        hosted_fallback_enabled=False,
        failure_threshold=2,
        cooldown_seconds=60,
    )

    for _ in range(2):
        with pytest.raises(LLMUnavailableError):
            await router.generate_structured(
                system="s", user="u", schema=SCHEMA, schema_name="test"
            )

    with pytest.raises(LLMUnavailableError) as exc_info:
        await router.generate_structured(system="s", user="u", schema=SCHEMA, schema_name="test")

    assert exc_info.value.calls[-1].status == "skipped"
    assert exc_info.value.calls[-1].fallback_reason == "circuit_open"
    assert primary.calls == 2
    statuses = await router.statuses(probe=False)
    assert statuses[0].circuit_state == "open"
