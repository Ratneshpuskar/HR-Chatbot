"""Thin async wrapper around the Google Gen AI SDK (Gemini)."""

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

import httpx
from google import genai
from google.genai import errors, types
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

_EMBED_BATCH_SIZE = 100
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_PING_CACHE_SECONDS = 60
_DEFAULT_QUOTA_COOLDOWN_SECONDS = 60.0
_MAX_QUOTA_COOLDOWN_SECONDS = 3600.0


class LLMError(Exception):
    """Raised when Gemini fails in a way the caller should surface to the user."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def _empty_answer_error() -> LLMError:
    # Gemini can return no text, e.g. when a safety filter blocks the reply or thinking exhausts the token budget.
    return LLMError(
        "I couldn't generate an answer to that. Please rephrase your question or contact HR directly.",
        retryable=True,
    )


@dataclass(frozen=True)
class ChatTurn:
    role: str  # "user" | "model"
    text: str
    # (mime_type, bytes) of images attached by the employee; sent as image parts after the text.
    images: tuple[tuple[str, bytes], ...] = ()


# Failures we translate into LLMError: HTTP errors from Gemini, and network/TLS/timeout errors.
_UPSTREAM_ERRORS = (errors.APIError, httpx.TransportError)


def _is_retryable(exc: BaseException) -> bool:
    # A timed-out request already took GEMINI_TIMEOUT_SECONDS; retrying would just stall the user.
    if isinstance(exc, httpx.TimeoutException):
        return False
    if isinstance(exc, httpx.TransportError):
        return True
    # 429 = quota exhausted. Backing off for seconds won't help; we switch to a fallback model instead.
    return isinstance(exc, errors.APIError) and exc.code in _RETRYABLE_STATUS and exc.code != 429


def _quota_details(exc: errors.APIError) -> tuple[float, bool]:
    """Return (seconds until the quota resets, whether it is a daily quota) from a 429 response."""
    delay, daily = _DEFAULT_QUOTA_COOLDOWN_SECONDS, False
    details = exc.details.get("error", {}).get("details", []) if isinstance(exc.details, dict) else []
    for detail in details:
        if detail.get("@type", "").endswith("RetryInfo"):
            try:
                delay = float(str(detail.get("retryDelay", "")).rstrip("s"))
            except ValueError:
                pass
        for violation in detail.get("violations", []):
            if "PerDay" in violation.get("quotaId", ""):
                daily = True
    if daily:
        delay = max(delay, _MAX_QUOTA_COOLDOWN_SECONDS)
    return min(delay, _MAX_QUOTA_COOLDOWN_SECONDS), daily


def _to_llm_error(exc: BaseException) -> LLMError:
    if isinstance(exc, httpx.TimeoutException):
        return LLMError("The AI service took too long to respond. Please try again.", retryable=True)
    if isinstance(exc, httpx.TransportError):
        return LLMError("The AI service can't be reached right now. Please try again.", retryable=True)
    assert isinstance(exc, errors.APIError)
    if exc.code == 429:
        if _quota_details(exc)[1]:
            return LLMError(
                "The assistant has reached its daily AI usage limit. Please try again later or contact HR directly.",
                retryable=True,
            )
        return LLMError("The assistant is receiving too many requests. Please try again in a minute.", retryable=True)
    if exc.code in (401, 403) or "API key" in str(exc):
        return LLMError("The assistant is misconfigured (invalid Gemini API key). Please contact support.")
    if exc.code in _RETRYABLE_STATUS:
        return LLMError("The AI service is temporarily unavailable. Please try again.", retryable=True)
    return LLMError("The assistant could not process this request.")


def _error_fields(exc: BaseException) -> dict[str, object]:
    return {"status": getattr(exc, "code", None), "error_type": type(exc).__name__}


def _finish_fields(response: types.GenerateContentResponse | None) -> dict[str, object]:
    candidates = (response.candidates if response else None) or []
    reason = candidates[0].finish_reason if candidates else None
    return {"finish_reason": str(reason) if reason else None}


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        embedding_model: str,
        temperature: float,
        max_output_tokens: int,
        timeout_seconds: int,
        fallback_models: list[str] | None = None,
    ) -> None:
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=timeout_seconds * 1000),
        )
        self.model = model
        # Tried in order when a model's quota is exhausted (each Gemini model has its own quota).
        self.models = [model, *(m for m in fallback_models or [] if m != model)]
        self._quota_reset_at: dict[str, float] = {}
        self.embedding_model = embedding_model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._ping_cache: tuple[float, bool] | None = None

    def _retrying(self) -> AsyncRetrying:
        return AsyncRetrying(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=1, max=8),
            reraise=True,
        )

    def _config(self, system_prompt: str) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=self._temperature,
            max_output_tokens=self._max_output_tokens,
        )

    async def _with_model_fallback(self, call: Callable[[str], Awaitable[T]]) -> T:
        """Run `call(model)` on the first model whose quota isn't exhausted.

        Transient errors are retried on the same model; a 429 marks that model as
        exhausted until its quota resets and moves on to the next one.
        """
        now = time.monotonic()
        candidates = [m for m in self.models if self._quota_reset_at.get(m, 0) <= now]
        if not candidates:
            raise LLMError("The assistant has reached its AI usage limit. Please try again later.", retryable=True)

        quota_error: errors.APIError | None = None
        for model in candidates:
            try:
                async for attempt in self._retrying():
                    with attempt:
                        return await call(model)
            except errors.APIError as exc:
                if exc.code != 429:
                    raise
                delay, daily = _quota_details(exc)
                self._quota_reset_at[model] = time.monotonic() + delay
                logger.warning(
                    "Gemini quota exhausted for model",
                    extra={"model": model, "daily": daily, "cooldown_s": round(delay)},
                )
                quota_error = exc
        assert quota_error is not None
        raise quota_error

    @staticmethod
    def _contents(turns: list[ChatTurn]) -> list[types.Content]:
        return [
            types.Content(
                role=t.role,
                parts=[types.Part(text=t.text)]
                + [types.Part.from_bytes(data=data, mime_type=mime) for mime, data in t.images],
            )
            for t in turns
        ]

    async def generate(self, system_prompt: str, turns: list[ChatTurn]) -> str:
        async def call(model: str) -> types.GenerateContentResponse:
            return await self._client.aio.models.generate_content(
                model=model, contents=self._contents(turns), config=self._config(system_prompt)
            )

        try:
            response = await self._with_model_fallback(call)
        except _UPSTREAM_ERRORS as exc:
            logger.error("Gemini generate failed", extra=_error_fields(exc))
            raise _to_llm_error(exc) from exc
        if not response.text:
            logger.warning("Gemini returned no text", extra=_finish_fields(response))
            raise _empty_answer_error()
        return response.text

    async def stream(self, system_prompt: str, turns: list[ChatTurn]) -> AsyncIterator[str]:
        """Yield text deltas. Retries and model fallback only apply before the first token is sent."""

        async def start(model: str) -> tuple[AsyncIterator[types.GenerateContentResponse], types.GenerateContentResponse | None]:
            stream = await self._client.aio.models.generate_content_stream(
                model=model, contents=self._contents(turns), config=self._config(system_prompt)
            )
            iterator = aiter(stream)
            return iterator, await anext(iterator, None)

        try:
            iterator, first = await self._with_model_fallback(start)
            produced = False
            last = first
            if first is not None and first.text:
                produced = True
                yield first.text
            async for chunk in iterator:
                last = chunk
                if chunk.text:
                    produced = True
                    yield chunk.text
        except _UPSTREAM_ERRORS as exc:
            logger.error("Gemini stream failed", extra=_error_fields(exc))
            raise _to_llm_error(exc) from exc
        if not produced:
            logger.warning("Gemini returned no text", extra=_finish_fields(last))
            raise _empty_answer_error()

    async def embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _EMBED_BATCH_SIZE):
            batch = texts[start : start + _EMBED_BATCH_SIZE]
            async for attempt in self._retrying():
                with attempt:
                    result = await self._client.aio.models.embed_content(
                        model=self.embedding_model,
                        contents=batch,
                        config=types.EmbedContentConfig(task_type=task_type),
                    )
            vectors.extend(e.values for e in result.embeddings)
        return vectors

    async def ping(self) -> bool:
        """Cheap connectivity/auth check used by the readiness probe (cached briefly)."""
        now = time.monotonic()
        if self._ping_cache and now - self._ping_cache[0] < _PING_CACHE_SECONDS:
            return self._ping_cache[1]
        try:
            await self._client.aio.models.get(model=self.model)
            ok = True
        except Exception:
            logger.warning("Gemini ping failed", exc_info=True)
            ok = False
        self._ping_cache = (now, ok)
        return ok
