"""HTTP routes for the admin dashboard.

All admin routes are gated by the ``Authorization`` header (Bearer
token style). The token is set via ``BOT_ADMIN_TOKEN`` and is
deliberately separate from the platform's shared secret — they are not
the same audience.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import HTMLResponse

from companion.services.admin import AdminAuth, AdminService
from companion.services.stats import StatsService


router = APIRouter(prefix="/admin", tags=["admin"])


# ── Dependencies ─────────────────────────────────────────────────────────


def get_admin_auth(request: Request) -> AdminAuth:
    return request.app.state.admin_auth


def get_admin_service(request: Request) -> AdminService:
    return request.app.state.admin_service


def get_stats_service(request: Request) -> StatsService:
    return request.app.state.stats_service


# ── HTML page ────────────────────────────────────────────────────────────


_ADMIN_HTML_PATH = Path(__file__).resolve().parent.parent / "admin" / "index.html"


@router.get("", response_class=HTMLResponse, include_in_schema=False)
async def admin_index() -> HTMLResponse:
    """Serve the single-page admin UI. No auth here — auth is on API calls."""
    return HTMLResponse(_ADMIN_HTML_PATH.read_text())


# ── JSON API ─────────────────────────────────────────────────────────────


@router.get("/stats")
async def admin_stats(
    authorization: str = Header(...),
    auth: AdminAuth = Depends(get_admin_auth),
    stats: StatsService = Depends(get_stats_service),
) -> dict:
    """Aggregate counters for the dashboard cards."""
    auth.verify(authorization)
    return await stats.aggregate()


@router.get("/users")
async def admin_list_users(
    authorization: str = Header(...),
    auth: AdminAuth = Depends(get_admin_auth),
    admin: AdminService = Depends(get_admin_service),
) -> dict:
    """List of users (most recently active first)."""
    auth.verify(authorization)
    users = await admin.list_users()
    return {"users": users, "count": len(users)}


@router.get("/users/{user_id}")
async def admin_user_detail(
    user_id: str,
    authorization: str = Header(...),
    auth: AdminAuth = Depends(get_admin_auth),
    admin: AdminService = Depends(get_admin_service),
) -> dict:
    """Full chat history + stats for one user."""
    auth.verify(authorization)
    return await admin.get_user_detail(user_id)
