"""Retrieval over HR policy documents (Markdown files in POLICIES_DIR).

Documents are split by heading, embedded with Gemini, and cached on disk keyed by
a content hash so restarts don't re-embed unchanged policies. If embeddings are
unavailable, retrieval falls back to keyword scoring so the service stays up.
"""

import asyncio
import hashlib
import json
import logging
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from app.services.llm import GeminiClient

logger = logging.getLogger(__name__)

_MAX_CHUNK_CHARS = 1500
_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")
_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i if in is it my of on or "
    "the to what when where which who will with you your".split()
)


@dataclass(frozen=True)
class Chunk:
    id: str
    source: str
    title: str
    section: str
    text: str


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: Chunk
    score: float


def _split_markdown(path: Path) -> list[Chunk]:
    raw = path.read_text(encoding="utf-8")
    doc_title = path.stem.replace("_", " ").replace("-", " ").title()
    sections: list[tuple[str, list[str]]] = []
    current_heading, buffer = doc_title, []

    for line in raw.splitlines():
        match = _HEADING.match(line)
        if match:
            if match.group(1) == "#":
                doc_title = match.group(2).strip()
            if buffer:
                sections.append((current_heading, buffer))
            current_heading, buffer = match.group(2).strip(), []
        else:
            buffer.append(line)
    if buffer:
        sections.append((current_heading, buffer))

    chunks: list[Chunk] = []
    for heading, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        # Split long sections on paragraph boundaries.
        parts, current = [], ""
        for para in body.split("\n\n"):
            if current and len(current) + len(para) > _MAX_CHUNK_CHARS:
                parts.append(current)
                current = ""
            current = f"{current}\n\n{para}" if current else para
        if current:
            parts.append(current)
        for i, part in enumerate(parts):
            chunks.append(
                Chunk(
                    id=f"{path.name}#{len(chunks)}-{i}",
                    source=path.name,
                    title=doc_title,
                    section=heading,
                    text=part.strip(),
                )
            )
    return chunks


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


class KnowledgeBase:
    def __init__(self, policies_dir: Path, cache_dir: Path, llm: GeminiClient) -> None:
        self._policies_dir = policies_dir
        self._cache_dir = cache_dir
        self._llm = llm
        self._chunks: list[Chunk] = []
        self._matrix: np.ndarray | None = None  # L2-normalised embeddings, one row per chunk
        self._doc_freq: Counter[str] = Counter()
        self._lock = asyncio.Lock()

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    @property
    def uses_embeddings(self) -> bool:
        return self._matrix is not None

    async def load(self) -> None:
        async with self._lock:
            files = sorted(self._policies_dir.glob("*.md"))
            chunks = [c for f in files for c in _split_markdown(f)]
            self._chunks = chunks
            self._doc_freq = Counter(t for c in chunks for t in set(_tokenize(c.section + " " + c.text)))
            self._matrix = None
            if not chunks:
                logger.warning("No policy documents found", extra={"dir": str(self._policies_dir)})
                return
            try:
                self._matrix = await self._load_or_build_embeddings(chunks)
            except Exception:
                logger.exception("Embedding index build failed; using keyword retrieval")
            logger.info(
                "Knowledge base loaded",
                extra={"files": len(files), "chunks": len(chunks), "embeddings": self.uses_embeddings},
            )

    async def _load_or_build_embeddings(self, chunks: list[Chunk]) -> np.ndarray:
        digest = hashlib.sha256(
            json.dumps([asdict(c) for c in chunks], sort_keys=True).encode()
            + self._llm.embedding_model.encode()
        ).hexdigest()[:16]
        cache_file = self._cache_dir / f"embeddings-{digest}.npy"
        if cache_file.exists():
            return np.load(cache_file)

        texts = [f"{c.title} — {c.section}\n{c.text}" for c in chunks]
        vectors = await self._llm.embed(texts, task_type="RETRIEVAL_DOCUMENT")
        matrix = np.asarray(vectors, dtype=np.float32)
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-12

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        for stale in self._cache_dir.glob("embeddings-*.npy"):
            stale.unlink(missing_ok=True)
        np.save(cache_file, matrix)
        return matrix

    async def search(self, query: str, top_k: int, min_score: float) -> list[RetrievedChunk]:
        if not self._chunks:
            return []
        if self._matrix is not None:
            try:
                [vector] = await self._llm.embed([query], task_type="RETRIEVAL_QUERY")
                q = np.asarray(vector, dtype=np.float32)
                q /= np.linalg.norm(q) + 1e-12
                scores = self._matrix @ q
                order = np.argsort(-scores)[:top_k]
                return [
                    RetrievedChunk(self._chunks[i], float(scores[i]))
                    for i in order
                    if scores[i] >= min_score
                ]
            except Exception:
                logger.warning("Query embedding failed; using keyword retrieval", exc_info=True)
        return self._keyword_search(query, top_k)

    def _keyword_search(self, query: str, top_k: int) -> list[RetrievedChunk]:
        terms = set(_tokenize(query))
        if not terms:
            return []
        n = len(self._chunks)
        results = []
        for chunk in self._chunks:
            counts = Counter(_tokenize(chunk.section + " " + chunk.text))
            score = sum(
                (1 + math.log(counts[t])) * math.log(1 + n / self._doc_freq[t])
                for t in terms
                if counts[t]
            )
            if score > 0:
                results.append(RetrievedChunk(chunk, score))
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]
