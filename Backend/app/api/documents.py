"""Policy documents: public listing/download, admin upload/delete."""

import asyncio
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from app.api.admin import require_admin
from app.schemas import DocumentOut, UploadResult
from app.services.documents import DocumentError, safe_filename, to_markdown
from app.services.knowledge_base import KnowledgeBase
from app.services.pii_guard import detect_sensitive_data

logger = logging.getLogger(__name__)

router = APIRouter(tags=["documents"])

_MAX_FILES_PER_UPLOAD = 20
_READ_CHUNK = 1024 * 1024
_INLINE_TYPES = {
    ".pdf": "application/pdf",
    ".md": "text/plain; charset=utf-8",
    ".markdown": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}
_DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _kb(request: Request) -> KnowledgeBase:
    return request.app.state.knowledge_base


def _existing_document(kb: KnowledgeBase, name: str) -> Path:
    try:
        safe = safe_filename(name)
    except DocumentError as exc:
        raise HTTPException(status_code=404, detail="Document not found.") from exc
    path = kb.policies_dir / safe
    if safe != name or not path.is_file():
        raise HTTPException(status_code=404, detail="Document not found.")
    return path


@router.get("/api/v1/documents", response_model=list[DocumentOut])
async def list_documents(request: Request) -> list[DocumentOut]:
    kb = _kb(request)
    await kb.ensure_fresh()
    return [DocumentOut(**vars(d)) for d in kb.documents]


@router.get("/api/v1/documents/{name}")
async def download_document(request: Request, name: str) -> FileResponse:
    path = _existing_document(_kb(request), name)
    ext = path.suffix.lower()
    return FileResponse(
        path,
        media_type=_INLINE_TYPES.get(ext, _DOCX_TYPE),
        filename=path.name,
        # PDFs and text open in the browser; Word files download.
        content_disposition_type="inline" if ext in _INLINE_TYPES else "attachment",
    )


async def _save_upload(upload: UploadFile, kb: KnowledgeBase, staging: Path, max_bytes: int) -> UploadResult:
    original = upload.filename or ""
    try:
        name = safe_filename(original)
    except DocumentError as exc:
        return UploadResult(name=original or "(unnamed)", ok=False, message=str(exc))

    tmp = staging / name
    size = 0
    with tmp.open("wb") as out:
        while chunk := await upload.read(_READ_CHUNK):
            size += len(chunk)
            if size > max_bytes:
                return UploadResult(name=name, ok=False, message=f"File is larger than {max_bytes // 2**20} MB.")
            out.write(chunk)

    try:
        markdown = await asyncio.to_thread(to_markdown, tmp)
    except DocumentError as exc:
        return UploadResult(name=name, ok=False, message=str(exc))

    findings = detect_sensitive_data(markdown)
    if findings:
        kinds = ", ".join(sorted({f.kind for f in findings}))
        return UploadResult(
            name=name, ok=False, message=f"Rejected: the document appears to contain sensitive personal data ({kinds})."
        )

    replaced = (kb.policies_dir / name).exists()
    tmp.replace(kb.policies_dir / name)
    return UploadResult(name=name, ok=True, message="Replaced." if replaced else "Added.")


@router.get("/api/v1/admin/check", status_code=204, tags=["admin"], dependencies=[Depends(require_admin)])
async def check_admin() -> None:
    """Lets the UI verify an admin token before showing upload controls."""


@router.post("/api/v1/admin/documents", response_model=list[UploadResult], dependencies=[Depends(require_admin)])
async def upload_documents(request: Request, files: list[UploadFile] = File(...)) -> list[UploadResult]:
    """Upload policy documents (.md, .txt, .pdf, .docx). Files with the same name are replaced."""
    if len(files) > _MAX_FILES_PER_UPLOAD:
        raise HTTPException(status_code=422, detail=f"Upload at most {_MAX_FILES_PER_UPLOAD} files at a time.")
    kb = _kb(request)
    max_bytes = request.app.state.settings.max_upload_mb * 2**20
    kb.policies_dir.mkdir(parents=True, exist_ok=True)

    # Stage inside the policies folder (subfolders aren't indexed) so the final move is an atomic
    # rename on the same filesystem, even when the folder is a mounted volume.
    with tempfile.TemporaryDirectory(dir=kb.policies_dir, prefix=".upload-") as staging:
        results = [await _save_upload(f, kb, Path(staging), max_bytes) for f in files]

    if any(r.ok for r in results):
        await kb.load()
        chunks = {d.name: d for d in kb.documents}
        for r in results:
            if r.ok and r.name in chunks:
                r.chunks = chunks[r.name].chunks
    logger.info(
        "Documents uploaded",
        extra={"accepted": sum(r.ok for r in results), "rejected": sum(not r.ok for r in results)},
    )
    return results


@router.delete("/api/v1/admin/documents/{name}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_document(request: Request, name: str) -> None:
    kb = _kb(request)
    path = _existing_document(kb, name)
    path.unlink()
    await kb.load()
    logger.info("Document deleted", extra={"document": name})
