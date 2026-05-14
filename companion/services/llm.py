"""LLM layer — provider-agnostic via LangChain's ``init_chat_model``.

Everywhere else, code just calls :meth:`LLMService.generate` or
:meth:`LLMService.stream`. This file is the only place that knows about
specific providers.

The chain is wrapped with :class:`RunnableWithMessageHistory`, so each
call automatically loads and saves the user's chat history. The
``session_id`` (derived from the user's id in
:class:`companion.services.history.HistoryService`) is what keeps each user's
history separate.
"""

from datetime import datetime, timezone
from typing import AsyncIterator, Callable, Optional

import structlog
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import Runnable
from langchain_core.runnables.history import RunnableWithMessageHistory

from companion.api.schemas import ReplyRequest
from companion.config import Settings
from companion.prompts.system import SYSTEM_PROMPT
from companion.services.history import HistoryService
from companion.services.summary import SummaryService


log = structlog.get_logger()


def build_chat_model(spec: str, **kwargs) -> BaseChatModel:
    """Build a LangChain chat model from a ``provider:model_name`` spec.

    Most providers are handled by LangChain's ``init_chat_model``.
    Cerebras isn't in its recognized list (as of langchain 0.3), so we
    import ``ChatCerebras`` directly when the spec begins with
    ``cerebras:``.
    """
    provider, _, model_name = spec.partition(":")
    if not model_name:
        # No colon — caller passed just a model name; let init_chat_model guess.
        return init_chat_model(provider, **kwargs)

    if provider == "cerebras":
        # ChatCerebras has its own constructor; it doesn't accept
        # model_provider or base_url.
        from langchain_cerebras import ChatCerebras
        kwargs.pop("base_url", None)
        return ChatCerebras(model=model_name, **kwargs)

    kwargs["model_provider"] = provider
    return init_chat_model(model_name, **kwargs)


class LLMService:
    """Builds and serves the provider-agnostic LangChain pipeline."""

    DEFAULT_LANGUAGE = "en"

    def __init__(
        self,
        settings: Settings,
        history: HistoryService,
        summary: Optional[SummaryService] = None,
        langfuse_handler_factory: Optional[Callable] = None,
    ) -> None:
        self._settings = settings
        self._history = history
        # Optional history compactor — only used if wired in.
        self._summary = summary
        # Optional Langfuse callback factory — only attached if configured.
        self._langfuse_handler_factory = langfuse_handler_factory
        self._prompt = self._build_prompt()
        # One chain per role; both share the prompt and history wiring.
        self._chains: dict[str, Runnable] = {
            "primary": self._build_chain("primary"),
            "fallback": self._build_chain("fallback"),
        }

    # ── chain assembly ────────────────────────────────────────────────────

    def _build_prompt(self) -> ChatPromptTemplate:
        return ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT),
            MessagesPlaceholder(variable_name="history"),
            ("human", "{user_text}"),
        ])

    def _build_model(self, role: str) -> BaseChatModel:
        # Parse "provider:model_name" and dispatch.
        spec = self._settings.model_spec(role)
        kwargs: dict = {
            "temperature": self._settings.temperature,
            "max_tokens": self._settings.max_output_tokens,
            "timeout": self._settings.llm_timeout_seconds,
        }
        base = self._settings.base_url(role)
        if base:
            kwargs["base_url"] = base
        return build_chat_model(spec, **kwargs)

    def _build_chain(self, role: str) -> Runnable:
        chain = self._prompt | self._build_model(role) | StrOutputParser()
        # RunnableWithMessageHistory loads/saves the per-user history around the call.
        return RunnableWithMessageHistory(
            chain,
            get_session_history=self._history.for_session,
            input_messages_key="user_text",
            history_messages_key="history",
        )

    # ── per-call config ───────────────────────────────────────────────────

    def _build_inputs(self, req: ReplyRequest) -> dict:
        # Read the language off the category if the platform put it there.
        category_language = getattr(req.category, "language", None)
        return {
            # Prefix the message with its timestamp so the AI sees temporal
            # context (and the prefixed form is what gets saved to history).
            "user_text": self._format_user_text(req.text, req.timestamp),
            "platform_name": self._settings.platform_name,
            "category_title": req.category.title,
            "category_extra": self._format_category_extras(req.category),
            "language": category_language or self.DEFAULT_LANGUAGE,
            "prompt_version": self._settings.prompt_version,
        }

    @staticmethod
    def _format_user_text(text: str, timestamp: Optional[int]) -> str:
        """Prepend a human-readable timestamp to the user message.

        The prefixed form is what RunnableWithMessageHistory stores, so
        each message in the history carries the time it was sent. The AI
        can then reason about gaps ("you asked this 3 days ago").
        """
        if timestamp is None:
            return text
        when = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return f"[{when.strftime('%Y-%m-%d %H:%M UTC')}] {text}"

    def _build_config(self, req: ReplyRequest) -> dict:
        callbacks: list = []
        if self._langfuse_handler_factory is not None:
            callbacks.append(self._langfuse_handler_factory(metadata={
                "user_id": req.user_id,
                "category": req.category.title,
                "prompt_version": self._settings.prompt_version,
            }))
        return {
            # session_id is what makes LangChain load THIS user's history.
            "configurable": {"session_id": HistoryService.session_id(req.user_id)},
            "callbacks": callbacks,
        }

    @staticmethod
    def _format_category_extras(category) -> str:
        # Pull through any extra fields the platform put on the category.
        extras = category.model_dump(exclude={"title"})
        items = [f"{k}={v}" for k, v in extras.items() if v not in (None, [], {})]
        if not items:
            return ""
        return "Additional category context: " + "; ".join(items)

    # ── public API ────────────────────────────────────────────────────────

    async def generate(self, req: ReplyRequest) -> tuple[str, str]:
        """Generate a reply. Returns ``(reply_text, model_spec_used)``.

        Tries primary; on any exception, falls back to the secondary model.
        The HTTP-level retry policy handles the case where both fail.
        """
        # Before generating, compact the stored history if it's too long.
        if self._summary is not None:
            await self._summary.maybe_compact(
                HistoryService.session_id(req.user_id)
            )

        inputs = self._build_inputs(req)
        config = self._build_config(req)
        try:
            text = await self._chains["primary"].ainvoke(inputs, config=config)
            return text, self._settings.model_spec("primary")
        except Exception as exc:
            log.warning("llm.primary_failed", err=str(exc), user_id=req.user_id)
            text = await self._chains["fallback"].ainvoke(inputs, config=config)
            return text, self._settings.model_spec("fallback")

    async def stream(self, req: ReplyRequest) -> AsyncIterator[str]:
        """Stream the reply token-by-token. Yields raw text chunks."""
        inputs = self._build_inputs(req)
        config = self._build_config(req)
        try:
            async for chunk in self._chains["primary"].astream(inputs, config=config):
                if chunk:
                    yield chunk
        except Exception as exc:
            log.warning("llm.primary_stream_failed", err=str(exc), user_id=req.user_id)
            async for chunk in self._chains["fallback"].astream(inputs, config=config):
                if chunk:
                    yield chunk
