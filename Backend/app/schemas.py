"""Request / response models for the public API."""

from typing import Literal

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, description="The employee's new question.")
    history: list[Message] = Field(default_factory=list, description="Prior turns, oldest first.")
    attachment_ids: list[str] = Field(
        default_factory=list, max_length=10, description="IDs from POST /api/v1/attachments for this conversation."
    )


class AttachmentOut(BaseModel):
    id: str
    name: str
    kind: Literal["image", "document"]
    size_bytes: int
    truncated: bool


class Source(BaseModel):
    title: str
    section: str
    source: str
    score: float


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    blocked: bool = False
    cached: bool = False


class FeedbackRequest(BaseModel):
    rating: Literal[1, -1] = Field(description="1 = helpful, -1 = not helpful")
    question: str = Field(min_length=1, max_length=4000)
    answer: str = Field(min_length=1, max_length=20_000)
    sources: list[str] = Field(default_factory=list, max_length=20)
    comment: str | None = Field(default=None, max_length=1000)


class PublicConfig(BaseModel):
    company_name: str
    hr_contact_email: str
    feedback_enabled: bool
    admin_enabled: bool
    max_upload_mb: int
    attachments_enabled: bool
    max_attachment_mb: int
    max_attachments_per_message: int


class DocumentOut(BaseModel):
    name: str
    title: str
    size_bytes: int
    updated_at: float
    chunks: int
    error: str | None = None


class UploadResult(BaseModel):
    name: str
    ok: bool
    message: str
    chunks: int = 0


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    knowledge_base_chunks: int
    retrieval_mode: Literal["embeddings", "keyword"]
    gemini_reachable: bool | None = None
    answer_cache_hits: int = 0
    answer_cache_misses: int = 0
