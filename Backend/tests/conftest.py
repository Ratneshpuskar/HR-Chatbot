import os
import shutil

os.environ.setdefault("GEMINI_API_KEY", "test-key")

from collections.abc import AsyncIterator, Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import BASE_DIR, Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.rate_limit import limiter  # noqa: E402
from app.services.llm import ChatTurn, LLMError  # noqa: E402


class FakeLLM:
    """Stands in for GeminiClient. Embeds by keyword presence so retrieval is deterministic."""

    model = "fake-model"
    embedding_model = "fake-embedding"
    _VOCAB = ["leave", "sick", "parental", "insurance", "health", "remote", "salary", "harassment", "notice"]

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[ChatTurn]]] = []
        self.fail_with: LLMError | None = None
        self.embed_fails = False

    async def embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        if self.embed_fails:
            raise RuntimeError("embedding unavailable")
        return [[float(text.lower().count(w)) + 0.01 for w in self._VOCAB] for text in texts]

    async def generate(self, system_prompt: str, turns: list[ChatTurn]) -> str:
        self.calls.append((system_prompt, turns))
        if self.fail_with:
            raise self.fail_with
        return "You get 20 days of annual leave. [Leave Policy — Annual Leave]"

    async def stream(self, system_prompt: str, turns: list[ChatTurn]) -> AsyncIterator[str]:
        self.calls.append((system_prompt, turns))
        if self.fail_with:
            raise self.fail_with
        for piece in ["You get ", "20 days ", "of leave."]:
            yield piece

    async def ping(self) -> bool:
        return True


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def settings(tmp_path) -> Settings:
    # Work on a copy of the sample policies so upload/delete tests never touch the real folder.
    policies = tmp_path / "policies"
    shutil.copytree(BASE_DIR / "data" / "policies", policies)
    return Settings(
        _env_file=None,  # tests must not depend on the developer's local .env
        gemini_api_key="test-key",
        policies_dir=policies,
        index_cache_dir=tmp_path / "index",
        insights_db_path=tmp_path / "insights.db",
        attachments_dir=tmp_path / "attachments",
        admin_api_token="admin-secret",
        rate_limit="1000/minute",
        retrieval_min_score=0.0,
        use_os_trust_store=False,
    )


@pytest.fixture
def client(settings: Settings, fake_llm: FakeLLM) -> Iterator[TestClient]:
    limiter.reset()
    with TestClient(create_app(settings, llm=fake_llm)) as test_client:
        yield test_client
