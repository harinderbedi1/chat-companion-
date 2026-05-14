"""FastAPI application entry point.

Builds the app, wires middleware, mounts routes, and uses ``lifespan``
to construct services once at startup (and tear them down on
shutdown). Services are stashed on ``app.state`` so the dependency
factories in :mod:`companion.api.dependencies` can hand them to each request.
"""

from contextlib import asynccontextmanager
from typing import Optional

import structlog
from dotenv import load_dotenv
from fastapi import FastAPI

# Push .env values into os.environ so AI provider SDKs (which read keys
# from the process environment) can find CEREBRAS_API_KEY / OPENAI_API_KEY
# / ANTHROPIC_API_KEY etc. pydantic-settings reads .env too, but only into
# the Settings object — external SDKs need os.environ.
load_dotenv()

from companion.api.admin_routes import router as admin_router
from companion.api.middleware import RequestIdMiddleware
from companion.api.routes import router
from companion.config import Settings
from companion.infra.db import init_db, healthcheck
from companion.infra.logging import configure_logging
from companion.services.admin import AdminAuth, AdminService
from companion.services.auth import AuthService
from companion.services.chat import ChatService
from companion.services.history import HistoryService
from companion.services.llm import LLMService, build_chat_model
from companion.services.safety import SafetyService
from companion.services.stats import StatsService
from companion.services.summary import SummaryService


log = structlog.get_logger()


def _maybe_load_langfuse_handler(settings: Settings) -> Optional[type]:
    """Return Langfuse's CallbackHandler class if configured, else None."""
    if not settings.langfuse_host:
        return None
    try:
        from langfuse.langchain import CallbackHandler
        return CallbackHandler
    except ImportError:
        log.warning("langfuse.import_failed", host=settings.langfuse_host)
        return None


def _build_summarizer_factory(settings: Settings):
    """Return a zero-arg callable that builds a model for summarization."""
    def make() -> object:
        kwargs = {
            "temperature": 0.2,                  # crisp, deterministic summaries
            "max_tokens": 300,
            "timeout": settings.llm_timeout_seconds,
        }
        base = settings.base_url("primary")
        if base:
            kwargs["base_url"] = base
        return build_chat_model(settings.model_spec("primary"), **kwargs)
    return make


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build all services at startup; tear them down on shutdown."""
    # ── Startup ──────────────────────────────────────────────────────────
    settings = Settings()
    configure_logging(settings.log_level)
    log.info(
        "startup",
        db_path=settings.db_path,
        llm_primary=settings.llm_primary,
        llm_fallback=settings.llm_fallback,
        moderation=settings.moderation_provider,
        summary_threshold=settings.history_summarize_threshold,
    )

    # Open the SQLite file and create tables if they don't exist.
    init_db(settings.db_path)
    healthcheck(settings.db_path)

    # Build services once; they live for the process lifetime.
    auth = AuthService(settings)
    history = HistoryService(settings.db_path, settings)
    safety = SafetyService(settings)
    stats = StatsService(settings.db_path, settings)

    summary = SummaryService(settings, history, _build_summarizer_factory(settings))

    langfuse_factory = _maybe_load_langfuse_handler(settings)
    llm = LLMService(
        settings,
        history,
        summary=summary,
        langfuse_handler_factory=langfuse_factory,
    )
    chat = ChatService(settings, auth, history, llm, safety, stats)

    # Admin layer — separate token, read-only views.
    admin_auth = AdminAuth(settings)
    admin = AdminService(settings.db_path, stats)

    app.state.settings = settings
    app.state.db_path = settings.db_path
    app.state.auth_service = auth
    app.state.history_service = history
    app.state.chat_service = chat
    app.state.stats_service = stats
    app.state.admin_auth = admin_auth
    app.state.admin_service = admin

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────
    log.info("shutdown")


def create_app() -> FastAPI:
    """Application factory. Used by uvicorn and by tests."""
    app = FastAPI(
        title="Bot",
        version="0.1.0",
        description="Provider-agnostic chat bot with per-user history.",
        lifespan=lifespan,
    )
    app.add_middleware(RequestIdMiddleware)
    app.include_router(router)
    app.include_router(admin_router)
    return app


app = create_app()
