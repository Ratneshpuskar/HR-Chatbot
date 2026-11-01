"""Retrieval over HR policy documents (Markdown, text, PDF and Word files in POLICIES_DIR).

Documents are split by heading and embedded with Gemini. Embeddings are cached on
disk per chunk, so adding or editing one document only embeds that document's
changed sections. The index reloads automatically when files in the folder change
(uploads, deletes, manual edits), which also keeps multiple workers in sync. If
embeddings are unavailable, retrieval falls back to keyword scoring.
"""

import asyncio
import hashlib
import json
import logging
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from app.services.documents import SUPPORTED_EXTENSIONS, DocumentError, to_markdown
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


@dataclass(frozen=True)
class SearchResult:
    chunks: list[RetrievedChunk]
    # Normalised query embedding (None in keyword mode); reused by the semantic answer cache.
    query_vector: np.ndarray | None = None
    # Similarity of the best chunk before min-score filtering; low values mean the policies don't cover the question.
    top_score: float | None = None


@dataclass(frozen=True)
class DocumentInfo:
    name: str
    title: str
    size_bytes: int
    updated_at: float
    chunks: int
    error: str | None = None


@dataclass
class _Index:
    """Everything a search needs, swapped in atomically on reload."""

    chunks: list[Chunk] = field(default_factory=list)
    matrix: np.ndarray | None = None  # L2-normalised embeddings, one row per chunk
    doc_freq: Counter[str] = field(default_factory=Counter)
    documents: list[DocumentInfo] = field(default_factory=list)
    version: str = ""
    signature: tuple = ()


def _split_long(text: str) -> list[str]:
    """Split text into pieces under the chunk limit, preferring paragraph, then line boundaries."""
    pieces: list[str] = []
    for para in text.split("\n\n"):
        if len(para) <= _MAX_CHUNK_CHARS:
            pieces.append(para)
            continue
        current = ""
        for line in para.split("\n"):
            while len(line) > _MAX_CHUNK_CHARS:  # a single huge line (common in PDFs)
                cut = line.rfind(" ", 0, _MAX_CHUNK_CHARS)
                cut = cut if cut > 0 else _MAX_CHUNK_CHARS
                if current:
                    pieces.append(current)
                    current = ""
                pieces.append(line[:cut])
                line = line[cut:].lstrip()
            if current and len(current) + len(line) + 1 > _MAX_CHUNK_CHARS:
                pieces.append(current)
                current = ""
            current = f"{current}\n{line}" if current else line
        if current:
            pieces.append(current)
    return pieces


def split_markdown(raw: str, filename: str) -> tuple[str, list[Chunk]]:
    """Split a Markdown document into chunks. Returns (document title, chunks)."""
    doc_title = Path(filename).stem.replace("_", " ").replace("-", " ").title()
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
        # Group pieces on paragraph boundaries up to the chunk size.
        parts, current = [], ""
        for piece in _split_long(body):
            if current and len(current) + len(piece) > _MAX_CHUNK_CHARS:
                parts.append(current)
                current = ""
            current = f"{current}\n\n{piece}" if current else piece
        if current:
            parts.append(current)
        for i, part in enumerate(parts):
            chunks.append(
                Chunk(
                    id=f"{filename}#{len(chunks)}-{i}",
                    source=filename,
                    title=doc_title,
                    section=heading,
                    text=part.strip(),
                )
            )
    return doc_title, chunks


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


def _embedding_text(c: Chunk) -> str:
    return f"{c.title} — {c.section}\n{c.text}"


def _text_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class KnowledgeBase:
    def __init__(self, policies_dir: Path, cache_dir: Path, llm: GeminiClient) -> None:
        self._policies_dir = policies_dir
        self._cache_dir = cache_dir
        self._llm = llm
        self._index = _Index()
        self._lock = asyncio.Lock()

    @property
    def policies_dir(self) -> Path:
        return self._policies_dir

    @property
    def chunk_count(self) -> int:
        return len(self._index.chunks)

    @property
    def uses_embeddings(self) -> bool:
        return self._index.matrix is not None

    @property
    def version(self) -> str:
        """Content hash of the loaded policies; changes whenever a policy is edited."""
        return self._index.version

    @property
    def documents(self) -> list[DocumentInfo]:
        return self._index.documents

    def _files(self) -> list[Path]:
        if not self._policies_dir.is_dir():
            return []
        return sorted(
            p for p in self._policies_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        )

    def _signature(self) -> tuple:
        result = []
        for p in self._files():
            stat = p.stat()
            result.append((p.name, stat.st_size, stat.st_mtime_ns))
        return tuple(result)

    async def ensure_fresh(self) -> None:
        """Reload if policy files changed since the last load. Cheap: one directory listing."""
        if self._signature() != self._index.signature and not self._lock.locked():
            await self.load()

    async def load(self) -> None:
        async with self._lock:
            signature = self._signature()
            chunks: list[Chunk] = []
            documents: list[DocumentInfo] = []
            for path in self._files():
                stat = path.stat()
                try:
                    markdown = await asyncio.to_thread(to_markdown, path)
                    title, doc_chunks = split_markdown(markdown, path.name)
                    error = None if doc_chunks else "No text found."
                except DocumentError as exc:
                    title, doc_chunks, error = path.stem, [], str(exc)
                except Exception:
                    logger.exception("Failed to read policy document", extra={"file": path.name})
                    title, doc_chunks, error = path.stem, [], "The file could not be read."
                chunks.extend(doc_chunks)
                documents.append(DocumentInfo(path.name, title, stat.st_size, stat.st_mtime, len(doc_chunks), error))

            index = _Index(
                chunks=chunks,
                doc_freq=Counter(t for c in chunks for t in set(_tokenize(c.section + " " + c.text))),
                documents=documents,
                version=hashlib.sha256(
                    json.dumps([asdict(c) for c in chunks], sort_keys=True).encode()
                ).hexdigest()[:16],
                signature=signature,
            )
            if chunks:
                try:
                    index.matrix = await self._embed_chunks(chunks)
                except Exception:
                    logger.exception("Embedding index build failed; using keyword retrieval")
            else:
                logger.warning("No policy documents found", extra={"dir": str(self._policies_dir)})

            self._index = index
            logger.info(
                "Knowledge base loaded",
                extra={"files": len(documents), "chunks": len(chunks), "embeddings": index.matrix is not None},
            )

    def _cache_file(self) -> Path:
        model = re.sub(r"[^A-Za-z0-9._-]", "_", self._llm.embedding_model)
        return self._cache_dir / f"chunk-embeddings-{model}.npz"

    async def _embed_chunks(self, chunks: list[Chunk]) -> np.ndarray:
        """Embed chunks, reusing cached vectors for any chunk whose text hasn't changed."""
        cache_file = self._cache_file()
        cached: dict[str, np.ndarray] = {}
        if cache_file.exists():
            try:
                with np.load(cache_file) as data:
                    cached = dict(zip(data["keys"].tolist(), data["vectors"]))
            except Exception:
                logger.warning("Ignoring unreadable embedding cache", exc_info=True)

        texts = [_embedding_text(c) for c in chunks]
        keys = [_text_key(t) for t in texts]
        missing = [i for i, k in enumerate(keys) if k not in cached]
        if missing:
            vectors = await self._llm.embed([texts[i] for i in missing], task_type="RETRIEVAL_DOCUMENT")
            for i, vector in zip(missing, vectors):
                v = np.asarray(vector, dtype=np.float32)
                cached[keys[i]] = v / (np.linalg.norm(v) + 1e-12)
            logger.info("Embedded new policy sections", extra={"new": len(missing), "reused": len(keys) - len(missing)})

        matrix = np.stack([cached[k] for k in keys]).astype(np.float32)

        # Persist only vectors for current chunks so the cache doesn't grow forever.
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_suffix(".tmp.npz")
        np.savez(tmp, keys=np.array(keys), vectors=matrix)
        tmp.replace(cache_file)
        return matrix

    async def search(self, query: str, top_k: int, min_score: float) -> SearchResult:
        index = self._index
        if not index.chunks:
            return SearchResult([])
        if index.matrix is not None:
            try:
                [vector] = await self._llm.embed([query], task_type="RETRIEVAL_QUERY")
                q = np.asarray(vector, dtype=np.float32)
                q /= np.linalg.norm(q) + 1e-12
                scores = index.matrix @ q
                order = np.argsort(-scores)[:top_k]
                chunks = [
                    RetrievedChunk(index.chunks[i], float(scores[i]))
                    for i in order
                    if scores[i] >= min_score
                ]
                return SearchResult(chunks, q, float(scores[order[0]]))
            except Exception:
                logger.warning("Query embedding failed; using keyword retrieval", exc_info=True)
        return SearchResult(self._keyword_search(index, query, top_k))

    @staticmethod
    def _keyword_search(index: _Index, query: str, top_k: int) -> list[RetrievedChunk]:
        terms = set(_tokenize(query))
        if not terms:
            return []
        n = len(index.chunks)
        results = []
        for chunk in index.chunks:
            counts = Counter(_tokenize(chunk.section + " " + chunk.text))
            score = sum(
                (1 + math.log(counts[t])) * math.log(1 + n / index.doc_freq[t])
                for t in terms
                if counts[t]
            )
            if score > 0:
                results.append(RetrievedChunk(chunk, score))
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]
