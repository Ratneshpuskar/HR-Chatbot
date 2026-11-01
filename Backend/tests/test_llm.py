import asyncio
from types import SimpleNamespace

import pytest
from google.genai import errors

from app.services.llm import ChatTurn, GeminiClient, LLMError


def _client_returning(*chunks: str) -> GeminiClient:
    """A real GeminiClient whose SDK calls are stubbed to return the given text chunks."""
    client = GeminiClient("key", "model", "embed", 0.3, 256, 10)

    def response(text: str) -> SimpleNamespace:
        return SimpleNamespace(text=text, candidates=[SimpleNamespace(finish_reason="SAFETY")])

    async def generate_content(**_):
        return response("".join(chunks))

    async def generate_content_stream(**_):
        async def gen():
            for chunk in chunks:
                yield response(chunk)

        return gen()

    client._client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content=generate_content, generate_content_stream=generate_content_stream)
        )
    )
    return client


async def _collect(client: GeminiClient) -> list[str]:
    return [delta async for delta in client.stream("sys", [ChatTurn("user", "hi")])]


def test_stream_yields_text() -> None:
    assert asyncio.run(_collect(_client_returning("Hel", "", "lo"))) == ["Hel", "lo"]


def test_empty_stream_raises_instead_of_blank_answer() -> None:
    with pytest.raises(LLMError, match="couldn't generate"):
        asyncio.run(_collect(_client_returning("", "")))


def test_empty_generate_raises() -> None:
    with pytest.raises(LLMError, match="couldn't generate"):
        asyncio.run(_client_returning("").generate("sys", [ChatTurn("user", "hi")]))


def _quota_error(daily: bool) -> errors.ClientError:
    quota_id = "GenerateRequestsPerDayPerProjectPerModel-FreeTier" if daily else "GenerateRequestsPerMinute-FreeTier"
    return errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "47s"},
    ]}})


def _client_with_limited(limited: set[str], daily: bool = True) -> tuple[GeminiClient, list[str]]:
    client = GeminiClient("key", "primary", "embed", 0.3, 256, 10, fallback_models=["lite-a", "lite-b"])
    tried: list[str] = []

    async def generate_content(model, **_):
        tried.append(model)
        if model in limited:
            raise _quota_error(daily)
        return SimpleNamespace(text=f"answer from {model}", candidates=[])

    async def generate_content_stream(model, **_):
        tried.append(model)
        if model in limited:
            raise _quota_error(daily)

        async def gen():
            yield SimpleNamespace(text=f"streamed by {model}", candidates=[])

        return gen()

    client._client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(
        generate_content=generate_content, generate_content_stream=generate_content_stream)))
    return client, tried


def test_falls_back_to_next_model_when_quota_exhausted() -> None:
    client, tried = _client_with_limited({"primary"})
    assert asyncio.run(client.generate("sys", [ChatTurn("user", "hi")])) == "answer from lite-a"
    assert asyncio.run(_collect(client)) == ["streamed by lite-a"]
    # The exhausted model is skipped on later requests instead of being hit again.
    assert tried == ["primary", "lite-a", "lite-a"]


def test_all_models_exhausted_reports_daily_limit() -> None:
    client, tried = _client_with_limited({"primary", "lite-a", "lite-b"}, daily=True)
    with pytest.raises(LLMError, match="daily AI usage limit"):
        asyncio.run(client.generate("sys", [ChatTurn("user", "hi")]))
    assert tried == ["primary", "lite-a", "lite-b"]
    # Once everything is cooling down, fail fast without calling Gemini again.
    with pytest.raises(LLMError, match="usage limit"):
        asyncio.run(client.generate("sys", [ChatTurn("user", "hi")]))
    assert len(tried) == 3


def test_per_minute_limit_message() -> None:
    client, _ = _client_with_limited({"primary", "lite-a", "lite-b"}, daily=False)
    with pytest.raises(LLMError, match="try again in a minute"):
        asyncio.run(client.generate("sys", [ChatTurn("user", "hi")]))
