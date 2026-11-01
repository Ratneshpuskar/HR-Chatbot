"""HTTP routes: chat (JSON + Server-Sent Events streaming) and health probes."""

import json
import logging
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app import __version__
from app.rate_limit import chat_rate_limit, limiter
from app.schemas import ChatRequest, ChatResponse, HealthResponse
from app.services.chat import ChatService, InvalidRequest, PreparedTurn
from app.services.llm import LLMError

logger = logging.getLogger(__name__)

router = APIRouter()


def _chat_service(request: Request) -> ChatService:
    return request.app.state.chat_service


def _sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _prepare(service: ChatService, body: ChatRequest) -> PreparedTurn:
    try:
        return await service.prepare(body.message, body.history)
    except InvalidRequest as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/v1/chat", response_model=ChatResponse, tags=["chat"])
@limiter.limit(chat_rate_limit)
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    service = _chat_service(request)
    prepared = await _prepare(service, body)
    try:
        answer = await service.answer(prepared)
    except LLMError as exc:
        raise HTTPException(status_code=503 if exc.retryable else 502, detail=str(exc)) from exc
    return ChatResponse(answer=answer, sources=prepared.sources, blocked=prepared.blocked_reply is not None)


@router.post("/api/v1/chat/stream", tags=["chat"])
@limiter.limit(chat_rate_limit)
async def chat_stream(request: Request, body: ChatRequest) -> StreamingResponse:
    """Streams the answer as SSE events: `sources`, then `token`* , then `done` or `error`."""
    service = _chat_service(request)
    prepared = await _prepare(service, body)

    async def events() -> AsyncIterator[str]:
        started = time.perf_counter()
        chars = 0
        yield _sse("sources", {
            "sources": [s.model_dump() for s in prepared.sources],
            "blocked": prepared.blocked_reply is not None,
        })
        try:
            async for delta in service.stream(prepared):
                if await request.is_disconnected():
                    logger.info("Client disconnected mid-stream")
                    return
                chars += len(delta)
                yield _sse("token", {"text": delta})
            yield _sse("done", {})
        except LLMError as exc:
            yield _sse("error", {"message": str(exc), "retryable": exc.retryable})
        except Exception:
            logger.exception("Unexpected streaming failure")
            yield _sse("error", {"message": "Something went wrong. Please try again.", "retryable": True})
        finally:
            logger.info(
                "Chat stream finished",
                extra={"answer_chars": chars, "duration_ms": round((time.perf_counter() - started) * 1000)},
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/healthz", tags=["ops"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", response_model=HealthResponse, tags=["ops"])
async def readiness(request: Request) -> HealthResponse:
    kb = request.app.state.knowledge_base
    reachable = await request.app.state.llm.ping()
    return HealthResponse(
        status="ok" if reachable and kb.chunk_count else "degraded",
        version=__version__,
        knowledge_base_chunks=kb.chunk_count,
        retrieval_mode="embeddings" if kb.uses_embeddings else "keyword",
        gemini_reachable=reachable,
    )
