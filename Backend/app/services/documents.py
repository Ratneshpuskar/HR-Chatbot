"""Converts uploaded policy files (Markdown, text, PDF, Word) to Markdown for indexing.

Headings become citation sections: Markdown/Word headings are kept, and PDFs get one
section per page so answers can cite "Page 3".
"""

import re
from pathlib import Path

from docx import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

SUPPORTED_EXTENSIONS = {".md", ".markdown", ".txt", ".pdf", ".docx"}

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")
_HEADING_STYLE = re.compile(r"^(?:Heading|Title)\s*(\d*)$", re.IGNORECASE)


class DocumentError(ValueError):
    """The file can't be used as a policy document; the message is safe to show to admins."""


def safe_filename(name: str) -> str:
    """Strip directories and unusual characters so uploads can't escape the policies folder."""
    base = Path(name.replace("\\", "/")).name
    stem, ext = Path(base).stem, Path(base).suffix.lower()
    stem = _SAFE_NAME.sub("", stem).strip(" .")[:100]
    if not stem:
        raise DocumentError("Invalid file name.")
    if ext not in SUPPORTED_EXTENSIONS:
        raise DocumentError(f"Unsupported file type '{ext or 'none'}'. Use {', '.join(sorted(SUPPORTED_EXTENSIONS))}.")
    return f"{stem}{ext}"


def _pdf_to_markdown(path: Path) -> str:
    try:
        reader = PdfReader(path)
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as exc:
        raise DocumentError("The PDF could not be read. It may be encrypted or corrupted.") from exc
    if not any(pages):
        raise DocumentError("The PDF has no extractable text (it may be a scanned image). Upload a text-based PDF.")
    return "\n\n".join(f"## Page {i}\n\n{text}" for i, text in enumerate(pages, start=1) if text)


def _docx_to_markdown(path: Path) -> str:
    try:
        doc = DocxDocument(str(path))
    except Exception as exc:
        raise DocumentError("The Word document could not be read. Save it as .docx and try again.") from exc

    lines: list[str] = []
    for block in doc.iter_inner_content():
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if not text:
                continue
            match = _HEADING_STYLE.match(block.style.name if block.style is not None else "")
            if match:
                level = 1 if block.style.name.lower().startswith("title") else min(int(match.group(1) or 1) + 1, 3)
                lines.append(f"{'#' * level} {text}")
            elif block.style is not None and "list" in block.style.name.lower():
                lines.append(f"- {text}")
            else:
                lines.append(text)
        elif isinstance(block, Table):
            for row in block.rows:
                cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                if any(cells):
                    lines.append(" | ".join(cells))
    if not lines:
        raise DocumentError("The Word document is empty.")
    return "\n\n".join(lines)


def to_markdown(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _pdf_to_markdown(path)
    if ext == ".docx":
        return _docx_to_markdown(path)
    if ext in SUPPORTED_EXTENSIONS:
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            raise DocumentError("The file is empty.")
        return text
    raise DocumentError(f"Unsupported file type '{ext}'.")
