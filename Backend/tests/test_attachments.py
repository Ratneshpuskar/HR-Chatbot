"""Employee attachments: images, PDFs (text and scanned), Word and text files in a conversation.

All identifiers below are synthetic, format-valid test values, not real data.
"""

import io
import time

from docx import Document as DocxDocument
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from app.main import create_app
from app.services.attachments import AttachmentStore
from test_documents import _pdf_bytes

ADMIN = {"X-Admin-Token": "admin-secret"}


def _text_image(lines: list[str], fmt: str = "PNG", exif: bytes | None = None) -> bytes:
    img = Image.new("RGB", (1000, 120 + 90 * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 44)
    except OSError:
        font = ImageFont.load_default(44)
    for i, line in enumerate(lines):
        draw.text((40, 50 + 90 * i), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format=fmt, **({"exif": exif} if exif else {}))
    return buf.getvalue()


def _docx(text: str) -> bytes:
    doc = DocxDocument()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _attach(client, name: str, data: bytes):
    return client.post("/api/v1/attachments", files={"file": (name, data, "application/octet-stream")})


def _ask(client, message: str, ids: list[str]):
    return client.post("/api/v1/chat", json={"message": message, "attachment_ids": ids})


def test_pdf_attachment_text_is_sent_with_question(client, fake_llm) -> None:
    r = _attach(client, "offer_letter.pdf", _pdf_bytes(["Offer Letter", "Your notice period is 90 days"]))
    assert r.status_code == 200, r.text
    meta = r.json()
    assert meta["kind"] == "document" and meta["name"] == "offer_letter.pdf"
    assert "text" not in meta  # extracted content is never echoed back to the browser

    assert _ask(client, "What notice period is in my offer letter?", [meta["id"]]).status_code == 200
    _, turns = fake_llm.calls[-1]
    assert "ATTACHED FILES" in turns[-1].text
    assert "notice period is 90 days" in turns[-1].text
    assert "OUR HR POLICIES" in turns[-1].text  # policies are still searched for comparison


def test_image_attachment_is_sent_as_image_part(client, fake_llm) -> None:
    meta = _attach(client, "screenshot.png", _text_image(["Leave balance: 12 days"])).json()
    assert meta["kind"] == "image"
    _ask(client, "Is this leave balance right?", [meta["id"]])
    _, turns = fake_llm.calls[-1]
    [(mime, data)] = turns[-1].images
    assert mime == "image/jpeg" and data[:2] == b"\xff\xd8"


def test_image_metadata_is_stripped(client, settings) -> None:
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # camera make; real photos also carry GPS here
    meta = _attach(client, "photo.jpg", _text_image(["Hello"], fmt="JPEG", exif=exif.tobytes())).json()
    stored = (settings.attachments_dir / f"{meta['id']}.bin").read_bytes()
    assert b"PhoneMaker" not in stored
    assert not Image.open(io.BytesIO(stored)).getexif()


def test_image_with_id_number_is_rejected_before_reaching_gemini(client, fake_llm, settings) -> None:
    r = _attach(client, "card.png", _text_image(["Sample ID Card", "2345 6789 0123"]))
    assert r.status_code == 422
    assert "sensitive personal data" in r.json()["detail"]
    assert fake_llm.calls == []
    stored = list(settings.attachments_dir.glob("*")) if settings.attachments_dir.exists() else []
    assert stored == []  # rejected files are never written to disk


def test_scanned_pdf_is_read_with_ocr(client, fake_llm) -> None:
    buf = io.BytesIO()
    Image.open(io.BytesIO(_text_image(["Relocation approved", "Start date 1 March"]))).save(buf, format="PDF")
    meta = _attach(client, "scan.pdf", buf.getvalue()).json()
    assert meta["kind"] == "document"
    _ask(client, "What does my scan say?", [meta["id"]])
    _, turns = fake_llm.calls[-1]
    assert "Relocation approved" in turns[-1].text


def test_word_attachment_with_sensitive_data_is_rejected(client) -> None:
    r = _attach(client, "form.docx", _docx("Employee PAN: ABCPE1234F"))
    assert r.status_code == 422 and "PAN number" in r.json()["detail"]


def test_rejects_unsupported_empty_and_oversized_files(client, settings) -> None:
    assert "Unsupported file type" in _attach(client, "tool.exe", b"MZ").json()["detail"]
    assert _attach(client, "empty.txt", b"").status_code == 422
    big = b"a" * (settings.max_attachment_mb * 2**20 + 1)
    assert "larger than" in _attach(client, "big.txt", big).json()["detail"]
    assert "could not be read" in _attach(client, "fake.png", b"not an image").json()["detail"]


def test_unknown_or_expired_attachment_is_a_clear_error(client) -> None:
    r = _ask(client, "What does it say?", ["A" * 32])
    assert r.status_code == 422 and "expired" in r.json()["detail"]


def test_attachments_expire(tmp_path) -> None:
    import asyncio

    store = AttachmentStore(tmp_path, ttl_seconds=0, max_bytes=2**20, max_text_chars=1000)
    attachment = asyncio.run(store.add("note.txt", b"Remember the team offsite on Friday."))
    time.sleep(0.01)
    assert store.get(attachment.id) is None
    assert not list(tmp_path.glob(f"{attachment.id}*"))


def test_long_documents_are_truncated(client, fake_llm, settings) -> None:
    text = ("Clause about benefits. " * 3000).encode()
    meta = _attach(client, "contract.txt", text).json()
    assert meta["truncated"] is True
    _ask(client, "Summarise my contract", [meta["id"]])
    _, turns = fake_llm.calls[-1]
    assert "only the beginning is included" in turns[-1].text


def test_attachment_questions_are_not_cached_or_logged_as_gaps(settings, fake_llm) -> None:
    settings.knowledge_gap_score = 1.01  # would otherwise log every question as a gap
    with TestClient(create_app(settings, llm=fake_llm)) as c:
        meta = _attach(c, "note.txt", b"My manager approved remote work on Fridays.").json()
        _ask(c, "Is this approval enough under policy?", [meta["id"]])
        _ask(c, "Is this approval enough under policy?", [meta["id"]])
        assert len(fake_llm.calls) == 2
        gaps = c.get("/api/v1/admin/insights", headers=ADMIN).json()["unanswered_questions"]
    assert gaps == []


def test_too_many_attachments(client, settings) -> None:
    ids = [_attach(client, f"n{i}.txt", b"note text").json()["id"] for i in range(settings.max_attachments_per_message + 1)]
    r = _ask(client, "Compare these", ids)
    assert r.status_code == 422 and "at most" in r.json()["detail"]


def test_attachment_text_also_drives_policy_retrieval(client, fake_llm) -> None:
    # The question alone says nothing about remote work; the attached file does.
    meta = _attach(client, "note.txt", b"Request to work remote from another country for two months.").json()
    data = _ask(client, "Is this request allowed?", [meta["id"]]).json()
    assert any(s["source"] == "remote_work.md" for s in data["sources"])
