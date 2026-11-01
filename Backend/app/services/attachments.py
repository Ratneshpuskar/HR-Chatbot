"""Files an employee attaches to their own conversation (offer letters, payslip screenshots, forms...).

Unlike policy documents, attachments are private to the conversation: they are never
indexed, can't be downloaded back, and are deleted after ATTACHMENT_TTL_MINUTES.

Every attachment's text is checked by the sensitive-data guard *before* it is stored
or sent to Gemini. Images and scanned PDFs are read with local OCR for that check, so
a photo of an ID card is rejected without ever leaving this server.
"""

import asyncio
import io
import json
import logging
import re
import secrets
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.services.documents import DocumentError, to_markdown
from app.services.pii_guard import detect_sensitive_data

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
TEXT_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | TEXT_EXTENSIONS

_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_MAX_IMAGE_SIDE = 2048
_MAX_OCR_PAGES = 10
_SCANNED_PDF_MIN_CHARS = 50

_ocr_engine = None
_ocr_lock = threading.Lock()


class AttachmentError(ValueError):
    """The attachment was rejected; the message is safe to show to the employee."""


@dataclass(frozen=True)
class Attachment:
    id: str
    name: str
    kind: str  # "image" | "document"
    size_bytes: int
    created_at: float
    text: str = ""  # extracted text (documents) or OCR text (images, used only for the guard)
    truncated: bool = False
    mime_type: str | None = None  # images only

    def public(self) -> dict:
        """Metadata safe to return to the browser (never the extracted text)."""
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "truncated": self.truncated,
        }


def _ocr(image: Image.Image) -> str:
    """Run local OCR (RapidOCR / ONNX). The engine loads lazily and isn't thread-safe, hence the lock."""
    global _ocr_engine
    import numpy as np

    with _ocr_lock:
        if _ocr_engine is None:
            from rapidocr_onnxruntime import RapidOCR

            _ocr_engine = RapidOCR()
        result, _ = _ocr_engine(np.asarray(image.convert("RGB")))
    return " ".join(line[1] for line in result or [])


def _process_image(data: bytes) -> tuple[bytes, str, str]:
    """Validate, downscale and re-encode an image (dropping EXIF/GPS metadata). Returns (bytes, mime, ocr_text)."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise AttachmentError("The image could not be read. Use PNG, JPEG or WebP.") from exc
    image.thumbnail((_MAX_IMAGE_SIDE, _MAX_IMAGE_SIDE))
    text = _ocr(image)

    out = io.BytesIO()
    if image.mode in ("RGBA", "LA", "P"):
        image.save(out, format="PNG", optimize=True)
        mime = "image/png"
    else:
        image.convert("RGB").save(out, format="JPEG", quality=88)
        mime = "image/jpeg"
    return out.getvalue(), mime, text


def _ocr_pdf(path: Path) -> str:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(path)
    except Exception as exc:
        raise AttachmentError("The PDF could not be read. It may be encrypted or corrupted.") from exc
    try:
        pages = []
        for i in range(min(len(pdf), _MAX_OCR_PAGES)):
            image = pdf[i].render(scale=2).to_pil()
            text = _ocr(image).strip()
            if text:
                pages.append(f"## Page {i + 1}\n\n{text}")
        if len(pdf) > _MAX_OCR_PAGES:
            pages.append(f"[Only the first {_MAX_OCR_PAGES} pages of this scanned PDF were read.]")
        return "\n\n".join(pages)
    finally:
        pdf.close()


def _extract_document_text(name: str, data: bytes) -> str:
    ext = Path(name).suffix.lower()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"upload{ext}"
        path.write_bytes(data)
        if ext == ".pdf":
            try:
                text = to_markdown(path)
            except DocumentError:
                text = ""  # no text layer: probably scanned, fall through to OCR
            if len(text.strip()) < _SCANNED_PDF_MIN_CHARS:
                text = _ocr_pdf(path)
        else:
            try:
                text = to_markdown(path)
            except DocumentError as exc:
                raise AttachmentError(str(exc)) from exc
    if not text.strip():
        raise AttachmentError("No readable text was found in this file.")
    return text


class AttachmentStore:
    def __init__(self, directory: Path, ttl_seconds: int, max_bytes: int, max_text_chars: int) -> None:
        self._dir = directory
        self._ttl = ttl_seconds
        self._max_bytes = max_bytes
        self._max_text_chars = max_text_chars

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def _paths(self, attachment_id: str) -> tuple[Path, Path]:
        return self._dir / f"{attachment_id}.json", self._dir / f"{attachment_id}.bin"

    def _process(self, filename: str, data: bytes) -> tuple[Attachment, bytes | None]:
        name = Path(filename.replace("\\", "/")).name[:120] or "attachment"
        ext = Path(name).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise AttachmentError(
                f"Unsupported file type '{ext or 'none'}'. Attach an image (PNG, JPEG, WebP), PDF, Word or text file."
            )

        image_bytes, mime = None, None
        if ext in IMAGE_EXTENSIONS:
            image_bytes, mime, text = _process_image(data)
            kind = "image"
        else:
            text = _extract_document_text(name, data)
            kind = "document"

        findings = detect_sensitive_data(text)
        if findings:
            kinds = ", ".join(sorted({f.kind for f in findings}))
            raise AttachmentError(
                f"This file appears to contain sensitive personal data ({kinds}), so it can't be used. "
                "Remove or cover that information and try again."
            )

        truncated = kind == "document" and len(text) > self._max_text_chars
        if truncated:
            text = text[: self._max_text_chars]
        attachment = Attachment(
            id=secrets.token_urlsafe(24),
            name=name,
            kind=kind,
            size_bytes=len(data),
            created_at=time.time(),
            text=text,
            truncated=truncated,
            mime_type=mime,
        )
        return attachment, image_bytes

    def _save(self, attachment: Attachment, image_bytes: bytes | None) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        meta_path, bin_path = self._paths(attachment.id)
        if image_bytes is not None:
            bin_path.write_bytes(image_bytes)
        meta_path.write_text(json.dumps(asdict(attachment)), encoding="utf-8")

    async def add(self, filename: str, data: bytes) -> Attachment:
        if len(data) > self._max_bytes:
            raise AttachmentError(f"File is larger than {self._max_bytes // 2**20} MB.")
        if not data:
            raise AttachmentError("The file is empty.")
        attachment, image_bytes = await asyncio.to_thread(self._process, filename, data)
        await asyncio.to_thread(self._save, attachment, image_bytes)
        await asyncio.to_thread(self.purge_expired)
        logger.info("Attachment added", extra={"kind": attachment.kind, "size_bytes": attachment.size_bytes})
        return attachment

    def get(self, attachment_id: str) -> tuple[Attachment, bytes | None] | None:
        if not _ID.match(attachment_id):
            return None
        meta_path, bin_path = self._paths(attachment_id)
        try:
            attachment = Attachment(**json.loads(meta_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return None
        if time.time() - attachment.created_at > self._ttl:
            self._delete(attachment_id)
            return None
        image = bin_path.read_bytes() if attachment.kind == "image" and bin_path.exists() else None
        return attachment, image

    def _delete(self, attachment_id: str) -> None:
        for path in self._paths(attachment_id):
            path.unlink(missing_ok=True)

    def purge_expired(self) -> None:
        if not self._dir.is_dir():
            return
        cutoff = time.time() - self._ttl
        for meta in self._dir.glob("*.json"):
            try:
                if meta.stat().st_mtime < cutoff:
                    self._delete(meta.stem)
            except OSError:
                pass
