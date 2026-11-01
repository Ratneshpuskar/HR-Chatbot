import json

from app.services.llm import LLMError


def _sse_events(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_health(client) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    ready = client.get("/readyz").json()
    assert ready["status"] == "ok"
    assert ready["knowledge_base_chunks"] > 0
    assert ready["retrieval_mode"] == "embeddings"


def test_chat_returns_answer_with_sources(client, fake_llm) -> None:
    response = client.post("/api/v1/chat", json={"message": "How much sick leave do I get?"})
    assert response.status_code == 200
    data = response.json()
    assert "20 days" in data["answer"]
    assert data["blocked"] is False
    assert any(s["source"] == "leave_policy.md" for s in data["sources"])

    system_prompt, turns = fake_llm.calls[0]
    assert "HR Assistant" in system_prompt
    assert turns[-1].role == "user"
    assert "OUR HR POLICIES" in turns[-1].text


def test_response_has_request_id_and_security_headers(client) -> None:
    response = client.post("/api/v1/chat", json={"message": "hi"}, headers={"X-Request-ID": "abc123"})
    assert response.headers["X-Request-ID"] == "abc123"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_stream_emits_sources_tokens_done(client) -> None:
    response = client.post("/api/v1/chat/stream", json={"message": "parental leave?"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(response.text)
    assert events[0][0] == "sources"
    assert "".join(d["text"] for e, d in events if e == "token") == "You get 20 days of leave."
    assert events[-1][0] == "done"


def test_sensitive_data_is_blocked_and_never_sent_to_llm(client, fake_llm) -> None:
    response = client.post("/api/v1/chat", json={"message": "My SSN is 123-45-6789, update my file"})
    assert response.status_code == 200
    data = response.json()
    assert data["blocked"] is True
    assert "Social Security Number" in data["answer"]
    assert fake_llm.calls == []


def test_history_is_normalised_for_gemini(client, fake_llm) -> None:
    history = [
        {"role": "assistant", "content": "Hello! How can I help?"},  # leading model turn dropped
        {"role": "user", "content": "What about leave?"},
        {"role": "assistant", "content": "You get 20 days."},
        {"role": "user", "content": "PAN ABCPE1234F"},  # sensitive turn dropped
        {"role": "assistant", "content": "I can't process that."},
    ]
    client.post("/api/v1/chat", json={"message": "And sick leave?", "history": history})
    _, turns = fake_llm.calls[0]
    roles = [t.role for t in turns]
    assert roles[0] == "user"
    assert all(a != b for a, b in zip(roles, roles[1:]))
    assert not any("ABCPE1234F" in t.text for t in turns)


def test_rejects_empty_and_oversized_messages(client, settings) -> None:
    assert client.post("/api/v1/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/v1/chat", json={"message": "   "}).status_code == 422
    too_long = "x" * (settings.max_message_chars + 1)
    assert client.post("/api/v1/chat", json={"message": too_long}).status_code == 422


def test_llm_errors_map_to_http_status(client, fake_llm) -> None:
    fake_llm.fail_with = LLMError("busy", retryable=True)
    assert client.post("/api/v1/chat", json={"message": "leave?"}).status_code == 503

    events = _sse_events(client.post("/api/v1/chat/stream", json={"message": "leave?"}).text)
    assert events[-1] == ("error", {"message": "busy", "retryable": True})


def test_keyword_fallback_when_embeddings_fail(settings, fake_llm) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    fake_llm.embed_fails = True
    with TestClient(create_app(settings, llm=fake_llm)) as client:
        assert client.get("/readyz").json()["retrieval_mode"] == "keyword"
        data = client.post("/api/v1/chat", json={"message": "harassment reporting"}).json()
        assert data["sources"][0]["source"] == "code_of_conduct.md"


def test_network_errors_are_retryable_llm_errors() -> None:
    import httpx

    from app.services.llm import _is_retryable, _to_llm_error

    exc = httpx.ConnectError("certificate verify failed")
    assert _is_retryable(exc)
    assert _to_llm_error(exc).retryable
