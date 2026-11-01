"""Request / response models for the public API."""

from typing import Literal

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, description="The employee's new question.")
    history: list[Message] = Field(default_factory=list, description="Prior turns, oldest first.")


class Source(BaseModel):
    title: str
    section: str
    source: str
    score: float


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    blocked: bool = False


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    knowledge_base_chunks: int
    retrieval_mode: Literal["embeddings", "keyword"]
    gemini_reachable: bool | None = None
