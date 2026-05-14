"""Compact a user's chat history when it grows past a threshold.

When a user has more than ``BOT_HISTORY_SUMMARIZE_THRESHOLD`` messages
stored, the older ones are condensed into a single summary message and
the most recent ``BOT_HISTORY_KEEP_RECENT`` messages are kept raw.
This keeps prompts short (cheaper, faster) without losing the gist of
the conversation.

Best-effort: a failure here is logged and the request continues with
the full history.
"""

from typing import Callable

import structlog
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from companion.config import Settings
from companion.services.history import HistoryService


log = structlog.get_logger()


class SummaryService:
    """Compress long histories into ``[summary, ...most_recent]`` form."""

    SUMMARY_MARKER = "[Earlier conversation summary]"

    SUMMARIZE_PROMPT = (
        "Condense the following conversation between a user and an AI "
        "assistant into 2-3 concise sentences. Capture the topics the "
        "user has discussed, the emotional tone, and anything important "
        "they've shared. Do not use names or invent details.\n\n"
        "Conversation:\n{transcript}"
    )

    def __init__(
        self,
        settings: Settings,
        history: HistoryService,
        model_factory: Callable[[], BaseChatModel],
    ) -> None:
        self._history = history
        self._make_model = model_factory
        self._threshold = settings.history_summarize_threshold
        self._keep_recent = settings.history_keep_recent

    async def maybe_compact(self, session_id: str) -> None:
        """If this session's history is past the threshold, rewrite it."""
        history_obj = self._history.for_session(session_id)
        messages = history_obj.messages
        if len(messages) < self._threshold:
            return

        keep = max(0, self._keep_recent)
        to_summarize = messages[:-keep] if keep > 0 else list(messages)
        kept_tail = messages[-keep:] if keep > 0 else []

        if not to_summarize:
            return

        try:
            summary_text = await self._summarize(to_summarize)
        except Exception as exc:
            log.warning(
                "summary.failed",
                err=str(exc),
                session_id=session_id,
                before=len(messages),
            )
            return

        # Rewrite: clear, then add the summary + the kept tail.
        try:
            history_obj.clear()
            history_obj.add_message(
                SystemMessage(content=f"{self.SUMMARY_MARKER} {summary_text}")
            )
            for message in kept_tail:
                history_obj.add_message(message)
            log.info(
                "summary.compacted",
                session_id=session_id,
                before=len(messages),
                after=len(kept_tail) + 1,
            )
        except Exception as exc:
            log.warning(
                "summary.write_failed",
                err=str(exc),
                session_id=session_id,
            )

    async def _summarize(self, messages: list[BaseMessage]) -> str:
        transcript_lines: list[str] = []
        for message in messages:
            content = getattr(message, "content", str(message))
            if isinstance(message, SystemMessage):
                # An earlier summary — preserve it as context for the new one.
                transcript_lines.append(f"(prior summary) {content}")
            elif isinstance(message, HumanMessage):
                transcript_lines.append(f"USER: {content}")
            elif isinstance(message, AIMessage):
                transcript_lines.append(f"BOT: {content}")
            else:
                transcript_lines.append(str(content))

        prompt = self.SUMMARIZE_PROMPT.format(
            transcript="\n".join(transcript_lines)
        )
        model = self._make_model()
        response = await model.ainvoke(prompt)
        text = getattr(response, "content", str(response))
        return text.strip()
