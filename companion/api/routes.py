"""HTTP route handlers — thin glue between FastAPI and the services.

No business logic lives here. Each handler unpacks the request, calls
the corresponding service, and returns the result.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from companion.api.dependencies import (
    get_auth_service,
    get_chat_service,
    get_history_service,
)
from companion.api.schemas import (
    DeleteHistoryResponse,
    HealthResponse,
    ReplyRequest,
    ReplyResponse,
)
from companion.services.auth import AuthService
from companion.services.chat import ChatService
from companion.services.history import HistoryService


router = APIRouter()


# ── Bot endpoints ────────────────────────────────────────────────────────


@router.post("/bot/reply", response_model=ReplyResponse)
async def bot_reply(
    req: ReplyRequest,
    request: Request,
    chat: ChatService = Depends(get_chat_service),
) -> ReplyResponse:
    """Main bot endpoint: one user message in, one reply out."""
    client_ip = request.client.host if request.client else "unknown"
    return await chat.handle(
        req,
        client_ip=client_ip,
        request_id=request.state.request_id,
    )


@router.post("/bot/reply/stream")
async def bot_reply_stream(
    req: ReplyRequest,
    request: Request,
    chat: ChatService = Depends(get_chat_service),
) -> StreamingResponse:
    """Streaming variant of the bot endpoint (Server-Sent Events)."""
    client_ip = request.client.host if request.client else "unknown"
    return StreamingResponse(
        chat.stream(
            req,
            client_ip=client_ip,
            request_id=request.state.request_id,
        ),
        media_type="text/event-stream",
        # Disable buffering on common reverse proxies.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/bot/history/{user_id}", response_model=DeleteHistoryResponse)
async def delete_bot_history(
    user_id: str,
    # Standard HTTP Authorization header — same secret as the JSON body uses.
    authorization: str = Header(...),
    auth: AuthService = Depends(get_auth_service),
    history: HistoryService = Depends(get_history_service),
) -> DeleteHistoryResponse:
    """Erase all chat history for a user (GDPR-style deletion)."""
    auth.verify_body(authorization)
    deleted = await history.delete_for_user(user_id)
    return DeleteHistoryResponse(user_id=user_id, deleted_keys=deleted)


# ── Operational probes ───────────────────────────────────────────────────


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe — returns 200 if the process is running."""
    return HealthResponse(status="ok")


@router.get("/ready", response_model=HealthResponse)
async def ready(request: Request) -> HealthResponse:
    """Readiness probe — returns 200 only if the SQLite DB is reachable."""
    from companion.infra.db import healthcheck
    try:
        healthcheck(request.app.state.db_path)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "db_unavailable", "info": str(exc)},
        )
    return HealthResponse(status="ready")
