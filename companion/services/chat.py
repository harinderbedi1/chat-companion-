"""Chat orchestrator — the only service the HTTP layer calls.

One method, :meth:`ChatService.handle`, runs the full pipeline for a
single request: authenticate, generate, safety-check, log, return.
The streaming variant emits Server-Sent Events.
"""

import time
from typing import AsyncIterator

import structlog
from fastapi import HTTPException, status

from companion.api.schemas import ReplyRequest, ReplyResponse
from companion.config import Settings
from companion.services.auth import AuthService
from companion.services.history import HistoryService
from companion.services.llm import LLMService
from companion.services.safety import SafetyService
from companion.services.stats import StatsService


log = structlog.get_logger()


class ChatService:
    """Coordinates one request from arrival to response."""

    def __init__(
        self,
        settings: Settings,
        auth: AuthService,
        history: HistoryService,
        llm: LLMService,
        safety: SafetyService,
        stats: StatsService,
    ) -> None:
        self._settings = settings
        self._auth = auth
        self._history = history
        self._llm = llm
        self._safety = safety
        self._stats = stats

    async def handle(
        self,
        req: ReplyRequest,
        client_ip: str,
        request_id: str,
    ) -> ReplyResponse:
        """Run the full request pipeline and return a validated response."""
        started_at = time.perf_counter()

        # 1. Authenticate — wrong key raises 401 immediately.
        self._auth.verify_body(req.authorization_key)

        # 2. Generate — LangChain loads & saves THIS user's history internally.
        # Summarization (if configured) runs inside LLMService before the call.
        try:
            reply_text, model_used = await self._llm.generate(req)
        except Exception as exc:
            log.error("chat.llm_failed", err=str(exc), user_id=req.user_id,
                      request_id=request_id)
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "ai_unavailable", "retry_after": 30},
            )

        # 3. Safety — drop the reply (don't send it) if any gate fails.
        safety_result = await self._safety.check(reply_text)
        if not safety_result.ok:
            await self._stats.record_safety_rejected()
            log.warning("chat.safety_failed", reason=safety_result.reason,
                        user_id=req.user_id, request_id=request_id)
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "reply_rejected", "reason": safety_result.reason},
            )

        # 4. Record success counters.
        await self._stats.record_message(req.user_id, req.category.id)

        latency_ms = int((time.perf_counter() - started_at) * 1000)
        log.info("chat.ok", user_id=req.user_id, model=model_used,
                 reply_len=len(reply_text), latency_ms=latency_ms,
                 category_id=req.category.id, category_title=req.category.title,
                 request_id=request_id)

        return ReplyResponse(
            user_id=req.user_id,
            reply=reply_text,
            model_used=model_used,
            prompt_version=self._settings.prompt_version,
            request_id=request_id,
        )

    async def stream(
        self,
        req: ReplyRequest,
        client_ip: str,
        request_id: str,
    ) -> AsyncIterator[str]:
        """Stream the reply as Server-Sent Events."""
        self._auth.verify_body(req.authorization_key)

        chunks: list[str] = []
        try:
            async for chunk in self._llm.stream(req):
                chunks.append(chunk)
                yield f"data: {chunk}\n\n"
        except Exception as exc:
            log.error("chat.stream_failed", err=str(exc), user_id=req.user_id,
                      request_id=request_id)
            yield 'data: {"error": "ai_unavailable"}\n\n'
            return

        # After streaming finishes, run safety on the assembled reply.
        full_reply = "".join(chunks)
        safety_result = await self._safety.check(full_reply)
        if not safety_result.ok:
            log.warning("chat.stream_safety_failed", reason=safety_result.reason,
                        user_id=req.user_id, request_id=request_id)
            yield f'data: {{"error":"reply_rejected","reason":"{safety_result.reason}"}}\n\n'
            return

        yield "data: [DONE]\n\n"
