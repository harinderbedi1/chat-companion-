"""Admin-side reads — list users, read one user's chat history.

Reads SQLite directly so this module is decoupled from the LangChain
``BaseChatMessageHistory`` interface and won't break across LangChain
upgrades.
"""

import hmac
import json
import sqlite3
from typing import Optional

import structlog
from fastapi import HTTPException, status

from companion.config import Settings
from companion.services.stats import StatsService


log = structlog.get_logger()


class AdminAuth:
    """Verifies the admin bearer token (separate from the platform secret)."""

    def __init__(self, settings: Settings) -> None:
        self._token: Optional[bytes] = (
            settings.admin_token.encode() if settings.admin_token else None
        )

    def verify(self, authorization: str) -> None:
        """Accept either ``Bearer <token>`` or the bare token."""
        if self._token is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "admin_disabled"},
            )
        provided = authorization
        if provided.startswith("Bearer "):
            provided = provided[len("Bearer "):]
        if not hmac.compare_digest(provided.encode(), self._token):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail={"error": "unauthorized"},
            )


class AdminService:
    """Read-only views over user history and stats."""

    def __init__(self, db_path: str, stats: StatsService) -> None:
        self._db_path = db_path
        self._stats = stats

    async def list_users(self, limit: int = 500) -> list[dict]:
        """Return up to ``limit`` users, sorted by last_seen descending."""
        with sqlite3.connect(self._db_path) as conn:
            session_rows = conn.execute(
                "SELECT DISTINCT session_id FROM chat_messages"
            ).fetchall()
        user_ids = [r[0] for r in session_rows if ":" not in r[0]]

        results: list[dict] = []
        for user_id in user_ids:
            results.append({
                "user_id":       user_id,
                "message_count": await self._stats.message_count_for_user(user_id),
                "last_seen":     await self._stats.last_seen_for_user(user_id),
            })
        results.sort(key=lambda r: (r["last_seen"] or 0), reverse=True)
        return results[:limit]

    async def get_user_detail(self, user_id: str) -> dict:
        """Full chat history + stats for one user."""
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT message_json FROM chat_messages "
                "WHERE session_id = ? ORDER BY id",
                (user_id,),
            ).fetchall()

        messages = [self._parse_message(r[0]) for r in rows]
        messages = [m for m in messages if m is not None]

        return {
            "user_id":       user_id,
            "message_count": await self._stats.message_count_for_user(user_id),
            "last_seen":     await self._stats.last_seen_for_user(user_id),
            "messages":      messages,
        }

    @staticmethod
    def _parse_message(raw: str) -> Optional[dict]:
        """Decode one stored message into ``{role, content}`` for display."""
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        role = obj.get("type") or obj.get("role")
        data = obj.get("data") or obj
        content = data.get("content") if isinstance(data, dict) else None
        if not role or content is None:
            return None
        return {"role": role, "content": content}
