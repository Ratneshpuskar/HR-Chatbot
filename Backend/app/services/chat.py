"""Orchestrates a chat turn: validation → PII guard → retrieval → cache → Gemini."""

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import numpy as np

from app.config import Settings
from app.schemas import Message, Source
from app.services.answer_cache import AnswerCache, CachedAnswer, normalise
from app.services.attachments import Attachment, AttachmentStore
from app.services.insights import InsightsStore
from app.services.knowledge_base import KnowledgeBase, RetrievedChunk, SearchResult
from app.services.llm import ChatTurn, GeminiClient
from app.services.pii_guard import detect_sensitive_data, rejection_message
from app.services.prompts import build_system_prompt, build_user_turn

logger = logging.getLogger(__name__)

# Messages shorter than this ("thanks!", "ok") aren't worth logging as knowledge gaps.
_MIN_GAP_WORDS = 3
# Attachment text used as an extra retrieval query, and extra policy sections allowed for comparisons.
_ATTACHMENT_QUERY_CHARS = 1500
_EXTRA_ATTACHMENT_CHUNKS = 2


class InvalidRequest(Exception):
    pass


@dataclass(frozen=True)
class _CacheSlot:
    question: str
    vector: np.ndarray | None
    source_ids: frozenset[str]
    kb_version: str
    knowledge_gap: bool


@dataclass(frozen=True)
class PreparedTurn:
    turns: list[ChatTurn]
    sources: list[Source]
    blocked_reply: str | None = None
    cached_answer: str | None = None
    # Set when the answer may be stored in the cache once generated (standalone questions only).
    cache_slot: _CacheSlot | None = field(default=None, repr=False)

    @property
    def canned_reply(self) -> str | None:
        """A reply that needs no Gemini call: a sensitive-data refusal or a cached answer."""
        return self.blocked_reply or self.cached_answer


class ChatService:
    def __init__(
        self,
        settings: Settings,
        llm: GeminiClient,
        kb: KnowledgeBase,
        cache: AnswerCache | None = None,
        insights: InsightsStore | None = None,
        attachments: AttachmentStore | None = None,
    ) -> None:
        self._settings = settings
        self._attachments = attachments
        self._llm = llm
        self._kb = kb
        self._cache = cache
        self._insights = insights
        self._system_prompt = build_system_prompt(settings.company_name, settings.hr_contact_email)

    def _validate(self, message: str) -> str:
        message = message.strip()
        if not message:
            raise InvalidRequest("Message must not be empty.")
        if len(message) > self._settings.max_message_chars:
            raise InvalidRequest(f"Message exceeds {self._settings.max_message_chars} characters.")
        return message

    def _load_attachments(self, attachment_ids: list[str]) -> tuple[list[Attachment], tuple[tuple[str, bytes], ...]]:
        if not attachment_ids:
            return [], ()
        if self._attachments is None:
            raise InvalidRequest("File attachments are disabled.")
        limit = self._settings.max_attachments_per_message
        if len(set(attachment_ids)) > limit:
            raise InvalidRequest(f"Attach at most {limit} files per conversation.")
        attachments, images = [], []
        for attachment_id in dict.fromkeys(attachment_ids):  # de-duplicate, keep order
            loaded = self._attachments.get(attachment_id)
            if loaded is None:
                raise InvalidRequest("An attached file has expired or is no longer available. Please attach it again.")
            attachment, image = loaded
            attachments.append(attachment)
            if image is not None and attachment.mime_type:
                images.append((attachment.mime_type, image))
        return attachments, tuple(images)

    async def prepare(
        self, message: str, history: list[Message], attachment_ids: list[str] | None = None
    ) -> PreparedTurn:
        message = self._validate(message)
        attachments, images = self._load_attachments(attachment_ids or [])

        findings = detect_sensitive_data(message)
        if findings:
            logger.warning("Blocked message containing sensitive data", extra={"kinds": [f.kind for f in findings]})
            return PreparedTurn([], [], blocked_reply=rejection_message(findings, self._settings.hr_contact_email))

        await self._kb.ensure_fresh()
        turns = self._history_turns(history)
        # Answers about an employee's own file are personal: never cached, never counted as policy gaps.
        cacheable = not turns and not attachments and self._cache is not None
        if cacheable:
            hit = self._cache.get_exact(message, self._kb.version)
            if hit:
                return await self._from_cache(message, hit)

        result = await self._search(self._retrieval_query(message, turns), attachments)
        sources = [
            Source(title=r.chunk.title, section=r.chunk.section, source=r.chunk.source, score=round(r.score, 3))
            for r in result.chunks
        ]
        knowledge_gap = not attachments and self._is_knowledge_gap(result)
        if knowledge_gap:
            await self._record_gap(message, result.top_score)

        slot = None
        if cacheable:
            slot = _CacheSlot(
                message,
                result.query_vector,
                frozenset(r.chunk.id for r in result.chunks),
                self._kb.version,
                knowledge_gap,
            )
            hit = self._cache.get(slot.question, slot.vector, slot.source_ids, slot.kb_version)
            if hit:
                logger.info("Answer cache hit", extra={"match": "semantic"})
                return PreparedTurn([], hit.sources, cached_answer=hit.answer)

        turns.append(ChatTurn("user", build_user_turn(message, result.chunks, attachments), images))
        return PreparedTurn(turns, sources, cache_slot=slot)

    async def _search(self, query: str, attachments: list[Attachment]) -> SearchResult:
        top_k, min_score = self._settings.retrieval_top_k, self._settings.retrieval_min_score
        if not attachments:
            return await self._kb.search(query, top_k, min_score)

        # Questions about a file ("does my offer letter match policy?") usually span several topics,
        # and the file's own text says which ones. Search with both and keep each chunk's best score.
        queries = [query] + [a.text[:_ATTACHMENT_QUERY_CHARS] for a in attachments if a.text.strip()]
        results = await asyncio.gather(*(self._kb.search(q, top_k, min_score) for q in queries))
        best: dict[str, RetrievedChunk] = {}
        for r in (c for res in results for c in res.chunks):
            if r.chunk.id not in best or r.score > best[r.chunk.id].score:
                best[r.chunk.id] = r
        merged = sorted(best.values(), key=lambda r: r.score, reverse=True)[: top_k + _EXTRA_ATTACHMENT_CHUNKS]
        return SearchResult(merged, results[0].query_vector, results[0].top_score)

    @staticmethod
    def _retrieval_query(message: str, turns: list[ChatTurn]) -> str:
        # Follow-ups ("and how many can I carry forward?") only make sense with the previous
        # question, so include it in the search query. History user turns are raw questions.
        previous = next((t.text for t in reversed(turns) if t.role == "user"), None)
        return f"{previous}\n{message}" if previous else message

    async def _from_cache(self, message: str, hit: CachedAnswer) -> PreparedTurn:
        logger.info("Answer cache hit", extra={"match": "exact"})
        if hit.knowledge_gap:
            await self._record_gap(message, None)
        return PreparedTurn([], hit.sources, cached_answer=hit.answer)

    def _is_knowledge_gap(self, result: SearchResult) -> bool:
        if not result.chunks:
            return True
        return result.top_score is not None and result.top_score < self._settings.knowledge_gap_score

    async def _record_gap(self, message: str, top_score: float | None) -> None:
        if self._insights is None or len(normalise(message).split()) < _MIN_GAP_WORDS:
            return
        try:
            await self._insights.record_gap(message, top_score)
        except Exception:
            logger.warning("Failed to record knowledge gap", exc_info=True)

    def _history_turns(self, history: list[Message]) -> list[ChatTurn]:
        limit = self._settings.max_history_messages
        recent = history[-limit:] if limit else []

        # Keep the newest messages that fit the character budget.
        budget = self._settings.max_history_chars
        kept: list[Message] = []
        for m in reversed(recent):
            budget -= len(m.content)
            if budget < 0:
                break
            kept.append(m)
        kept.reverse()

        turns: list[ChatTurn] = []
        for m in kept:
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

    def _remember(self, prepared: PreparedTurn, answer: str) -> None:
        slot = prepared.cache_slot
        if slot and self._cache is not None and answer:
            self._cache.put(
                slot.question, slot.vector, slot.source_ids, slot.kb_version, answer, prepared.sources, slot.knowledge_gap
            )

    async def answer(self, prepared: PreparedTurn) -> str:
        if prepared.canned_reply:
            return prepared.canned_reply
        answer = await self._llm.generate(self._system_prompt, prepared.turns)
        self._remember(prepared, answer)
        return answer

    async def stream(self, prepared: PreparedTurn) -> AsyncIterator[str]:
        if prepared.canned_reply:
            yield prepared.canned_reply
            return
        parts: list[str] = []
        async for delta in self._llm.stream(self._system_prompt, prepared.turns):
            parts.append(delta)
            yield delta
        # Only reached when the stream completes; stopped or failed answers are never cached.
        self._remember(prepared, "".join(parts))

    async def record_feedback(
        self, rating: int, question: str, answer: str, sources: list[str], comment: str | None
    ) -> bool:
        """Store feedback for HR review. Returns False when it was rejected or insights are off."""
        if self._insights is None:
            return False
        if any(detect_sensitive_data(text) for text in (question, comment or "")):
            logger.warning("Dropped feedback containing sensitive data")
            return False
        await self._insights.record_feedback(rating, question, answer, sources, comment)
        return True
