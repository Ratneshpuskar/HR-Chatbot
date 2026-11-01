"""Policy document listing, download, upload (md/txt/pdf/docx), delete and auto-reindexing."""

import io

from docx import Document as DocxDocument

ADMIN = {"X-Admin-Token": "admin-secret"}


def _pdf_bytes(pages: list[str]) -> bytes:
    """Build a minimal valid text PDF (one line of Helvetica per page)."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", ""]
    page_ids = []
    for text in pages:
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(f"<< /Length {len(content)} >>\nstream\n{content.decode()}\nendstream")
        content_id = len(objects)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
            "/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>"
        )
        page_ids.append(len(objects))
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{i} 0 R' for i in page_ids)}] /Count {len(page_ids)} >>"

    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{body}\nendobj\n".encode())
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def _docx_bytes() -> bytes:
    doc = DocxDocument()
    doc.add_heading("Travel Policy", level=0)
    doc.add_heading("Per Diem", level=1)
    doc.add_paragraph("Employees travelling for work receive a daily allowance of USD 75.")
    doc.add_heading("Booking", level=1)
    doc.add_paragraph("Book flights through the travel portal at least 14 days in advance.")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _upload(client, *files, headers=ADMIN):
    return client.post(
        "/api/v1/admin/documents",
        files=[("files", (name, data, "application/octet-stream")) for name, data in files],
        headers=headers,
    )


def _names(client) -> set[str]:
    return {d["name"] for d in client.get("/api/v1/documents").json()}


def test_lists_documents_with_chunk_counts(client) -> None:
    docs = {d["name"]: d for d in client.get("/api/v1/documents").json()}
    assert "leave_policy.md" in docs
    assert docs["leave_policy.md"]["title"] == "Leave Policy"
    assert docs["leave_policy.md"]["chunks"] > 0


def test_download_document_and_reject_traversal(client) -> None:
    r = client.get("/api/v1/documents/leave_policy.md")
    assert r.status_code == 200
    assert "Annual Leave" in r.text
    assert "inline" in r.headers["content-disposition"]
    assert client.get("/api/v1/documents/..%2F..%2F.env").status_code == 404
    assert client.get("/api/v1/documents/missing.md").status_code == 404


def test_upload_requires_admin_token(client) -> None:
    assert _upload(client, ("x.md", b"# X\n\nhi"), headers={}).status_code == 401
    assert _upload(client, ("x.md", b"# X\n\nhi"), headers={"X-Admin-Token": "nope"}).status_code == 401


def test_upload_markdown_pdf_and_docx_are_indexed_and_searchable(client, fake_llm) -> None:
    results = _upload(
        client,
        ("Relocation.md", b"# Relocation\n\n## Allowance\n\nRelocating employees get a one-time relocation grant."),
        ("Handbook.pdf", _pdf_bytes(["Welcome to the company handbook", "Insurance claims go to the insurer portal"])),
        ("Travel.docx", _docx_bytes()),
    ).json()
    assert [r["ok"] for r in results] == [True, True, True], results
    assert all(r["chunks"] > 0 for r in results)

    docs = {d["name"]: d for d in client.get("/api/v1/documents").json()}
    assert docs["Handbook.pdf"]["chunks"] == 2  # one section per page
    assert docs["Travel.docx"]["title"] == "Travel Policy"

    answer = client.post("/api/v1/chat", json={"message": "insurance claims portal"}).json()
    sections = {(s["source"], s["section"]) for s in answer["sources"]}
    assert ("Handbook.pdf", "Page 2") in sections


def test_upload_rejects_bad_files_without_touching_the_index(client) -> None:
    before = _names(client)
    results = _upload(
        client,
        ("notes.exe", b"MZ..."),
        ("empty.txt", b"   "),
        ("scan.pdf", b"not really a pdf"),
        ("staff.md", b"# Staff\n\nJohn's SSN is 123-45-6789"),
    ).json()
    assert [r["ok"] for r in results] == [False, False, False, False]
    assert "Unsupported file type" in results[0]["message"]
    assert "sensitive personal data" in results[3]["message"]
    assert _names(client) == before


def test_upload_rejects_oversized_files(client, settings) -> None:
    big = b"a" * (settings.max_upload_mb * 2**20 + 1)
    [result] = _upload(client, ("big.txt", big)).json()
    assert result["ok"] is False and "larger than" in result["message"]


def test_upload_sanitises_file_names(client) -> None:
    [result] = _upload(client, ("../../evil name!.md", b"# Evil\n\nSafe content")).json()
    assert result["ok"] and result["name"] == "evil name.md"
    assert "evil name.md" in _names(client)


def test_reupload_replaces_and_delete_removes(client) -> None:
    _upload(client, ("perks.md", b"# Perks\n\nFree coffee."))
    [again] = _upload(client, ("perks.md", b"# Perks\n\nFree coffee and snacks.")).json()
    assert again["message"] == "Replaced."
    assert client.get("/api/v1/documents/perks.md").text.endswith("snacks.")

    assert client.delete("/api/v1/admin/documents/perks.md", headers=ADMIN).status_code == 204
    assert "perks.md" not in _names(client)
    assert client.delete("/api/v1/admin/documents/perks.md", headers=ADMIN).status_code == 404


def test_files_added_to_folder_are_picked_up_without_restart(client, settings) -> None:
    (settings.policies_dir / "parking.md").write_text("# Parking\n\nStaff parking is free.", encoding="utf-8")
    assert "parking.md" in _names(client)


def test_only_new_sections_are_embedded(client, fake_llm) -> None:
    embedded: list[int] = []
    original = fake_llm.embed

    async def counting(texts, task_type):
        if task_type == "RETRIEVAL_DOCUMENT":
            embedded.append(len(texts))
        return await original(texts, task_type)

    fake_llm.embed = counting
    _upload(client, ("perks.md", b"# Perks\n\n## Coffee\n\nFree coffee.\n\n## Gym\n\nGym access."))
    assert embedded == [2]  # existing policies reused from cache; only the 2 new sections embedded


def test_admin_check(client) -> None:
    assert client.get("/api/v1/admin/check", headers=ADMIN).status_code == 204
    assert client.get("/api/v1/admin/check", headers={"X-Admin-Token": "x"}).status_code == 401
