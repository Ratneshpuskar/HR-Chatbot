"""Orchestrates a chat turn: validation → PII guard → retrieval → Gemini."""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.config import Settings
from app.schemas import Message, Source
from app.services.knowledge_base import KnowledgeBase
from app.services.llm import ChatTurn, GeminiClient
from app.services.pii_guard import detect_sensitive_data, rejection_message
from app.services.prompts import build_system_prompt, build_user_turn

logger = logging.getLogger(__name__)


class InvalidRequest(Exception):
    pass


@dataclass(frozen=True)
class PreparedTurn:
    turns: list[ChatTurn]
    sources: list[Source]
    blocked_reply: str | None = None


class ChatService:
    def __init__(self, settings: Settings, llm: GeminiClient, kb: KnowledgeBase) -> None:
        self._settings = settings
        self._llm = llm
        self._kb = kb
        self._system_prompt = build_system_prompt(settings.company_name, settings.hr_contact_email)

    async def prepare(self, message: str, history: list[Message]) -> PreparedTurn:
        message = message.strip()
        if not message:
            raise InvalidRequest("Message must not be empty.")
        if len(message) > self._settings.max_message_chars:
            raise InvalidRequest(f"Message exceeds {self._settings.max_message_chars} characters.")

        findings = detect_sensitive_data(message)
        if findings:
            logger.warning("Blocked message containing sensitive data", extra={"kinds": [f.kind for f in findings]})
            return PreparedTurn([], [], rejection_message(findings, self._settings.hr_contact_email))

        context = await self._kb.search(
            message, self._settings.retrieval_top_k, self._settings.retrieval_min_score
        )
        sources = [
            Source(title=r.chunk.title, section=r.chunk.section, source=r.chunk.source, score=round(r.score, 3))
            for r in context
        ]

        turns = self._history_turns(history)
        turns.append(ChatTurn("user", build_user_turn(message, context)))
        return PreparedTurn(turns, sources)

    def _history_turns(self, history: list[Message]) -> list[ChatTurn]:
        limit = self._settings.max_history_messages
        recent = history[-limit:] if limit else []
        turns: list[ChatTurn] = []
        for m in recent:
            role = "user" if m.role == "user" else "model"
            # Drop earlier turns that contained sensitive data, and keep roles alternating
            # (Gemini requires the conversation to start with a user turn).
            if role == "user" and detect_sensitive_data(m.content):
                continue
            if not turns and role == "model":
                continue
            if turns and turns[-1].role == role:
                turns[-1] = ChatTurn(role, turns[-1].text + "\n\n" + m.content)
            else:
                turns.append(ChatTurn(role, m.content))
        # The new user turn is appended next, so history must end on a model turn.
        if turns and turns[-1].role == "user":
            turns.pop()
        return turns

    async def answer(self, prepared: PreparedTurn) -> str:
        if prepared.blocked_reply:
            return prepared.blocked_reply
        return await self._llm.generate(self._system_prompt, prepared.turns)

    async def stream(self, prepared: PreparedTurn) -> AsyncIterator[str]:
        if prepared.blocked_reply:
            yield prepared.blocked_reply
            return
        async for delta in self._llm.stream(self._system_prompt, prepared.turns):
            yield delta
