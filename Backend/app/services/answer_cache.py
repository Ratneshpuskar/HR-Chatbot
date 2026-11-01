"""Semantic cache for answers to standalone questions.

Most HR questions are repeats ("how many sick days do I get?"). Reusing an answer
skips the Gemini generation call entirely. Two questions match when either:
  - their normalised text is identical, or
  - their query embeddings are very similar AND retrieval picked the same policy
    sections for both (guards against near-miss topics such as sick vs annual leave).

Only questions without conversation history are cached, because follow-ups depend
on context. Entries are tied to the knowledge-base version, so editing a policy
invalidates every cached answer.
"""

import re
import time
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np

from app.schemas import Source

_NON_WORD = re.compile(r"[^a-z0-9]+")


def normalise(question: str) -> str:
    return _NON_WORD.sub(" ", question.lower()).strip()


@dataclass
class _Entry:
    vector: np.ndarray | None
    source_ids: frozenset[str]
    answer: str
    sources: list[Source]
    knowledge_gap: bool
    expires_at: float


@dataclass(frozen=True)
class CachedAnswer:
    answer: str
    sources: list[Source]
    # The original question wasn't covered by the policies; repeats still count towards HR's gap report.
    knowledge_gap: bool = False


class AnswerCache:
    def __init__(self, max_entries: int, ttl_seconds: int, similarity: float) -> None:
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        self._similarity = similarity
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._kb_version = ""
        self.hits = 0
        self.misses = 0

    def _sync_version(self, kb_version: str) -> None:
        if kb_version != self._kb_version:
            self._entries.clear()
            self._kb_version = kb_version

    def get_exact(self, question: str, kb_version: str) -> CachedAnswer | None:
        """Cheap lookup by normalised text only; lets exact repeats skip retrieval entirely."""
        if self._max_entries == 0:
            return None
        self._sync_version(kb_version)
        key = normalise(question)
        entry = self._entries.get(key)
        if entry is None or entry.expires_at < time.monotonic():
            return None
        self._entries.move_to_end(key)
        self.hits += 1
        return CachedAnswer(entry.answer, entry.sources, entry.knowledge_gap)

    def get(
        self, question: str, vector: np.ndarray | None, source_ids: frozenset[str], kb_version: str
    ) -> CachedAnswer | None:
        if self._max_entries == 0:
            return None
        self._sync_version(kb_version)
        now = time.monotonic()
        key = normalise(question)

        entry = self._entries.get(key)
        if entry is None and vector is not None and source_ids:
            best = 0.0
            for candidate_key, candidate in self._entries.items():
                if candidate.vector is None or candidate.source_ids != source_ids:
                    continue
                score = float(candidate.vector @ vector)
                if score >= self._similarity and score > best:
                    best, key, entry = score, candidate_key, candidate

        if entry is None or entry.expires_at < now:
            self.misses += 1
            return None
        self._entries.move_to_end(key)
        self.hits += 1
        return CachedAnswer(entry.answer, entry.sources, entry.knowledge_gap)

    def put(
        self,
        question: str,
        vector: np.ndarray | None,
        source_ids: frozenset[str],
        kb_version: str,
        answer: str,
        sources: list[Source],
        knowledge_gap: bool = False,
    ) -> None:
        if self._max_entries == 0:
            return
        self._sync_version(kb_version)
        key = normalise(question)
        self._entries[key] = _Entry(
            vector, source_ids, answer, sources, knowledge_gap, time.monotonic() + self._ttl
        )
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
