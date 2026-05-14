"""Pre-return safety checks on the generated reply.

Three gates, cheap → expensive:

1. Length bounds.
2. Forbidden-phrase substring match.
3. Content moderation (OpenAI by default; ``none`` for tests).

If any gate fails, the orchestrator returns 422 with the reason.
"""

from dataclasses import dataclass
from typing import Optional

from openai import AsyncOpenAI

from companion.config import Settings


@dataclass(frozen=True)
class SafetyResult:
    """Outcome of a safety check. ``ok=False`` carries a machine-readable ``reason``."""

    ok: bool
    reason: Optional[str] = None


class SafetyService:
    """Checks a generated reply before it is returned to the platform."""

    FORBIDDEN_PHRASES: tuple[str, ...] = (
        "as an ai language model",
        "i am unable to provide medical advice, but here",
        "i am unable to provide legal advice, but here",
        "i am unable to provide financial advice, but here",
    )

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._openai_client: Optional[AsyncOpenAI] = (
            AsyncOpenAI() if settings.moderation_provider == "openai" else None
        )

    async def check(self, text: str) -> SafetyResult:
        """Run all gates in order. Return on the first failure."""
        # Gate 1 — length.
        if not (self._settings.min_reply_chars <= len(text) <= self._settings.max_reply_chars):
            return SafetyResult(False, "length_bounds")

        # Gate 2 — forbidden phrases.
        lowered = text.lower()
        for phrase in self.FORBIDDEN_PHRASES:
            if phrase in lowered:
                return SafetyResult(False, "forbidden_phrase")

        # Gate 3 — moderation provider.
        return await self._moderate(text)

    async def _moderate(self, text: str) -> SafetyResult:
        provider = self._settings.moderation_provider
        if provider == "none":
            return SafetyResult(True)
        if provider == "openai":
            return await self._moderate_openai(text)
        if provider == "llama_guard":
            # Hook for self-hosted Llama Guard 3; not implemented in this build.
            return SafetyResult(True)
        return SafetyResult(False, f"moderation_provider_unknown:{provider}")

    async def _moderate_openai(self, text: str) -> SafetyResult:
        assert self._openai_client is not None
        result = await self._openai_client.moderations.create(
            model="omni-moderation-latest",
            input=text,
        )
        flagged = result.results[0]
        if flagged.flagged:
            scores = flagged.category_scores.model_dump()
            worst_category = max(scores.items(), key=lambda kv: kv[1])[0]
            return SafetyResult(False, f"moderation:{worst_category}")
        return SafetyResult(True)
