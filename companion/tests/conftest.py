"""Shared pytest fixtures.

Tests run entirely in-process with no external services:

* A temp SQLite file is used for storage (auto-deleted at teardown).
* The real HistoryService, StatsService, and AdminService run against it.
* A fake LLMService that deterministically echoes input + prior history
  is swapped in so tests don't hit a real AI provider.
"""

import os
import tempfile
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import HumanMessage

from companion.api.schemas import ReplyRequest


# ── Env defaults — autouse so every test starts clean ───────────────────


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    """Set required env vars before any test reads Settings()."""
    # Each test gets its own DB file under pytest's tmp_path.
    db_path = str(tmp_path / "test_companion.db")
    monkeypatch.setenv("BOT_DB_PATH", db_path)
    monkeypatch.setenv("BOT_SHARED_SECRET", "test_secret_long_enough")
    monkeypatch.setenv("BOT_LLM_PRIMARY", "openai:gpt-4o-mini")
    monkeypatch.setenv("BOT_LLM_FALLBACK", "openai:gpt-4o-mini")
    # "none" lets us skip the real OpenAI Moderation call during tests.
    monkeypatch.setenv("BOT_MODERATION_PROVIDER", "none")
    monkeypatch.setenv("BOT_ADMIN_TOKEN", "admin_test_token")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


# ── Fake LLM that echoes input + prior history ──────────────────────────


class _FakeLLMService:
    """Deterministic stand-in for LLMService.

    Reads/writes through the real HistoryService so tests cover the
    actual SQLite storage path.
    """

    def __init__(self, settings, history_service, summary=None) -> None:
        self._settings = settings
        self._history = history_service
        self._summary = summary

    async def generate(self, req: ReplyRequest) -> tuple[str, str]:
        # Honor the same summarization hook the real LLMService uses.
        if self._summary is not None:
            await self._summary.maybe_compact(
                self._history.session_id(req.user_id)
            )
        session_id = self._history.session_id(req.user_id)
        history = self._history.for_session(session_id)
        prior_user_texts = [
            m.content for m in history.messages if isinstance(m, HumanMessage)
        ]
        recall = ", ".join(prior_user_texts[-3:]) if prior_user_texts else ""
        reply = f"echo:{req.text}"
        if recall:
            reply += f" | prior:{recall}"
        history.add_user_message(req.text)
        history.add_ai_message(reply)
        return reply, "fake:primary"

    async def stream(self, req: ReplyRequest):
        text, _ = await self.generate(req)
        for word in text.split(" "):
            yield word + " "


# ── The client fixture ───────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client(_env) -> AsyncIterator[AsyncClient]:
    """Build a test client running against a temp SQLite file."""
    # Defer imports until env is set so Settings reads our test values.
    from companion.api.main import create_app
    from companion.config import Settings
    from companion.infra.db import init_db
    from companion.services.admin import AdminAuth, AdminService
    from companion.services.auth import AuthService
    from companion.services.chat import ChatService
    from companion.services.history import HistoryService
    from companion.services.safety import SafetyService
    from companion.services.stats import StatsService

    settings = Settings()
    init_db(settings.db_path)

    history_service = HistoryService(settings.db_path, settings)
    stats = StatsService(settings.db_path, settings)
    auth = AuthService(settings)

    app = create_app()
    app.state.settings = settings
    app.state.db_path = settings.db_path
    app.state.auth_service = auth
    app.state.history_service = history_service
    app.state.stats_service = stats
    app.state.admin_auth = AdminAuth(settings)
    app.state.admin_service = AdminService(settings.db_path, stats)
    app.state.chat_service = ChatService(
        settings=settings,
        auth=auth,
        history=history_service,
        llm=_FakeLLMService(settings, history_service),  # type: ignore[arg-type]
        safety=SafetyService(settings),
        stats=stats,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
