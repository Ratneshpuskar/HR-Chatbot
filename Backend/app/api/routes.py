"""HTTP routes: chat (JSON + Server-Sent Events streaming), feedback, HR insights and health probes."""

import json
import logging
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from app import __version__
from app.api.admin import require_admin
from app.rate_limit import chat_rate_limit, limiter
from app.schemas import AttachmentOut, ChatRequest, ChatResponse, FeedbackRequest, HealthResponse, PublicConfig
from app.services.attachments import AttachmentError
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
        return await service.prepare(body.message, body.history, body.attachment_ids)
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
    return ChatResponse(
        answer=answer,
        sources=prepared.sources,
        blocked=prepared.blocked_reply is not None,
        cached=prepared.cached_answer is not None,
    )


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
            "cached": prepared.cached_answer is not None,
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


@router.get("/api/v1/config", response_model=PublicConfig, tags=["chat"])
async def public_config(request: Request) -> PublicConfig:
    settings = request.app.state.settings
    return PublicConfig(
        company_name=settings.company_name,
        hr_contact_email=settings.hr_contact_email,
        feedback_enabled=settings.insights_enabled,
        admin_enabled=settings.admin_api_token is not None,
        max_upload_mb=settings.max_upload_mb,
        attachments_enabled=settings.attachments_enabled,
        max_attachment_mb=settings.max_attachment_mb,
        max_attachments_per_message=settings.max_attachments_per_message,
    )


@router.post("/api/v1/attachments", response_model=AttachmentOut, tags=["chat"])
@limiter.limit(chat_rate_limit)
async def upload_attachment(request: Request, file: UploadFile = File(...)) -> AttachmentOut:
    """Attach an image, PDF, Word or text file to the employee's conversation (private, short-lived)."""
    store = request.app.state.attachments
    if store is None:
        raise HTTPException(status_code=404, detail="File attachments are disabled.")
    data = await file.read(store.max_bytes + 1)
    try:
        attachment = await store.add(file.filename or "attachment", data)
    except AttachmentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AttachmentOut(**attachment.public())


@router.post("/api/v1/feedback", status_code=204, tags=["chat"])
@limiter.limit(chat_rate_limit)
async def feedback(request: Request, body: FeedbackRequest) -> None:
    await _chat_service(request).record_feedback(
        body.rating, body.question, body.answer, body.sources, body.comment
    )


@router.get("/api/v1/admin/insights", tags=["admin"], dependencies=[Depends(require_admin)])
async def insights(request: Request, days: int = Query(default=30, ge=1, le=365)) -> dict:
    """Unanswered questions and feedback, for HR to find gaps in policy documentation."""
    store = request.app.state.insights
    if store is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return await store.summary(days=days)


@router.get("/healthz", tags=["ops"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", response_model=HealthResponse, tags=["ops"])
async def readiness(request: Request) -> HealthResponse:
    kb = request.app.state.knowledge_base
    reachable = await request.app.state.llm.ping()
    cache = request.app.state.answer_cache
    return HealthResponse(
        status="ok" if reachable and kb.chunk_count else "degraded",
        version=__version__,
        knowledge_base_chunks=kb.chunk_count,
        retrieval_mode="embeddings" if kb.uses_embeddings else "keyword",
        gemini_reachable=reachable,
        answer_cache_hits=cache.hits if cache else 0,
        answer_cache_misses=cache.misses if cache else 0,
    )
