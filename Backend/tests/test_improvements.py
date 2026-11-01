"""Answer cache, conversation memory, knowledge-gap logging, feedback and HR insights."""

import numpy as np
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas import Message
from app.services.answer_cache import AnswerCache
from app.services.chat import ChatService
from app.services.llm import ChatTurn

ADMIN = {"X-Admin-Token": "admin-secret"}


def _ask(client, message, history=None):
    return client.post("/api/v1/chat", json={"message": message, "history": history or []}).json()


# --- Answer cache ---------------------------------------------------------------------------


def test_repeated_question_is_served_from_cache(client, fake_llm) -> None:
    first = _ask(client, "How much sick leave do I get?")
    second = _ask(client, "how much SICK leave do I get")  # same question, different casing/punctuation
    assert first["cached"] is False and second["cached"] is True
    assert second["answer"] == first["answer"]
    assert second["sources"] == first["sources"]
    assert len(fake_llm.calls) == 1


def test_reworded_question_with_same_sources_hits_cache(client, fake_llm) -> None:
    _ask(client, "how much sick leave do I get")
    assert _ask(client, "sick leave: how much do I get")["cached"] is True
    assert len(fake_llm.calls) == 1


def test_different_topic_is_not_served_from_cache(client, fake_llm) -> None:
    _ask(client, "How much sick leave do I get?")
    assert _ask(client, "What health insurance do we have?")["cached"] is False
    assert len(fake_llm.calls) == 2


def test_follow_up_questions_bypass_cache(client, fake_llm) -> None:
    _ask(client, "How much sick leave do I get?")
    history = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello!"}]
    assert _ask(client, "How much sick leave do I get?", history)["cached"] is False
    assert len(fake_llm.calls) == 2


def test_stream_caches_completed_answer(client, fake_llm) -> None:
    client.post("/api/v1/chat/stream", json={"message": "parental leave length?"})
    text = client.post("/api/v1/chat/stream", json={"message": "parental leave length?"}).text
    assert '"cached": true' in text
    assert "You get 20 days of leave." in text
    assert len(fake_llm.calls) == 1


def test_cache_is_invalidated_when_policies_change() -> None:
    cache = AnswerCache(max_entries=10, ttl_seconds=60, similarity=0.9)
    vec = np.array([1.0, 0.0], dtype=np.float32)
    cache.put("q", vec, frozenset({"a"}), "v1", "answer", [])
    assert cache.get("q", vec, frozenset({"a"}), "v1") is not None
    assert cache.get("q", vec, frozenset({"a"}), "v2") is None


def test_cache_evicts_least_recently_used() -> None:
    cache = AnswerCache(max_entries=2, ttl_seconds=60, similarity=0.9)
    for q in ("one", "two", "three"):
        cache.put(q, None, frozenset(), "v", q, [])
    assert cache.get("one", None, frozenset(), "v") is None
    assert cache.get("three", None, frozenset(), "v").answer == "three"


# --- Conversation memory ----------------------------------------------------------------------


def test_follow_up_retrieval_includes_previous_question() -> None:
    turns = [ChatTurn("user", "How many days of annual leave do I get?"), ChatTurn("model", "20 days.")]
    query = ChatService._retrieval_query("And how many can I carry forward?", turns)
    assert "annual leave" in query and "carry forward" in query
    assert ChatService._retrieval_query("standalone", []) == "standalone"


def test_history_is_trimmed_to_character_budget(client, fake_llm, settings) -> None:
    service = client.app.state.chat_service
    big = "x" * (settings.max_history_chars // 2)
    history = [
        Message(role="user", content="oldest question"),
        Message(role="assistant", content=big),
        Message(role="user", content="middle question"),
        Message(role="assistant", content=big),
        Message(role="user", content="latest question"),
        Message(role="assistant", content="short answer"),
    ]
    turns = service._history_turns(history)
    text = " ".join(t.text for t in turns)
    assert "oldest question" not in text
    assert "latest question" in text
    assert sum(len(t.text) for t in turns) <= settings.max_history_chars
    assert turns[0].role == "user"


# --- Knowledge gaps, feedback and insights -------------------------------------------------


def test_insights_endpoint_requires_admin_token(client) -> None:
    assert client.get("/api/v1/admin/insights").status_code == 401
    assert client.get("/api/v1/admin/insights", headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.get("/api/v1/admin/insights", headers=ADMIN).status_code == 200


def test_insights_disabled_without_token(settings, fake_llm) -> None:
    settings.admin_api_token = None
    with TestClient(create_app(settings, llm=fake_llm)) as c:
        assert c.get("/api/v1/admin/insights", headers=ADMIN).status_code == 404


def test_uncovered_questions_are_logged_for_hr(settings, fake_llm) -> None:
    settings.knowledge_gap_score = 1.01  # treat every question as poorly covered
    with TestClient(create_app(settings, llm=fake_llm)) as c:
        _ask(c, "Do we get pet insurance for dogs?")
        _ask(c, "Do we get pet insurance for dogs?")
        _ask(c, "thanks!")  # too short to be a meaningful gap
        _ask(c, "My PAN is ABCPE1234F, is it on file?")  # blocked: never stored
        gaps = c.get("/api/v1/admin/insights", headers=ADMIN).json()["unanswered_questions"]
    assert [(g["question"], g["times_asked"]) for g in gaps] == [("Do we get pet insurance for dogs?", 2)]


def test_feedback_is_recorded_and_summarised(client) -> None:
    base = {"question": "How much sick leave?", "answer": "10 days.", "sources": ["leave_policy.md#Sick Leave"]}
    assert client.post("/api/v1/feedback", json={**base, "rating": 1}).status_code == 204
    assert client.post("/api/v1/feedback", json={**base, "rating": -1, "comment": "Too vague"}).status_code == 204
    data = client.get("/api/v1/admin/insights", headers=ADMIN).json()
    assert data["feedback"] == {"helpful": 1, "not_helpful": 1}
    assert data["negative_feedback"][0]["comment"] == "Too vague"
    assert data["negative_feedback"][0]["sources"] == ["leave_policy.md#Sick Leave"]


def test_feedback_with_sensitive_data_is_not_stored(client) -> None:
    body = {"question": "SSN 123-45-6789", "answer": "x", "rating": -1}
    assert client.post("/api/v1/feedback", json=body).status_code == 204
    assert client.get("/api/v1/admin/insights", headers=ADMIN).json()["feedback"]["not_helpful"] == 0


def test_feedback_rejects_invalid_rating(client) -> None:
    body = {"question": "q", "answer": "a", "rating": 5}
    assert client.post("/api/v1/feedback", json=body).status_code == 422


def test_public_config_exposes_hr_contact(client) -> None:
    data = client.get("/api/v1/config").json()
    assert data["company_name"] == "Acme Corp"
    assert data["hr_contact_email"] == "hr@example.com"
    assert data["feedback_enabled"] is True and data["admin_enabled"] is True


def test_exact_repeat_skips_retrieval_embedding(client, fake_llm) -> None:
    embed_calls = 0
    original = fake_llm.embed

    async def counting_embed(texts, task_type):
        nonlocal embed_calls
        embed_calls += 1
        return await original(texts, task_type)

    fake_llm.embed = counting_embed
    _ask(client, "How much sick leave do I get?")
    assert embed_calls == 1
    assert _ask(client, "How much sick leave do I get?")["cached"] is True
    assert embed_calls == 1  # exact repeat answered without another embedding call


def test_cached_repeats_of_uncovered_questions_still_count_as_gaps(settings, fake_llm) -> None:
    settings.knowledge_gap_score = 1.01
    with TestClient(create_app(settings, llm=fake_llm)) as c:
        _ask(c, "Do we get pet insurance for dogs?")
        assert _ask(c, "Do we get pet insurance for dogs?")["cached"] is True
        gaps = c.get("/api/v1/admin/insights", headers=ADMIN).json()["unanswered_questions"]
    assert gaps[0]["times_asked"] == 2
