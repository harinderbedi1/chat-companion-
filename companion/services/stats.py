"""Lightweight counters for the admin dashboard, stored in SQLite.

Increments are best-effort — counter failures must never break a real
chat request.
"""

import sqlite3
from datetime import datetime, timezone
from typing import Optional

import structlog

from companion.config import Settings


log = structlog.get_logger()


class StatsService:
    """Counters for lifetime, daily, per-user, and per-category tallies."""

    KEY_TOTAL            = "messages:total"
    KEY_DAY_PREFIX       = "messages:day:"
    KEY_USER_PREFIX      = "messages:user:"
    KEY_CATEGORY_PREFIX  = "messages:category:"
    KEY_SAFETY_REJECTED  = "safety_rejected:total"

    def __init__(self, db_path: str, settings: Settings) -> None:
        self._db_path = db_path

    # ── Increment helpers ────────────────────────────────────────────────

    async def record_message(self, user_id: str,
                              category_id: Optional[str] = None) -> None:
        """Bump total, today's, the user's, and (if given) the category's counters."""
        try:
            now = datetime.now(timezone.utc)
            today_key    = f"{self.KEY_DAY_PREFIX}{now.strftime('%Y-%m-%d')}"
            user_key     = f"{self.KEY_USER_PREFIX}{user_id}"
            category_key = f"{self.KEY_CATEGORY_PREFIX}{category_id}" if category_id else None

            with sqlite3.connect(self._db_path) as conn:
                for key in (self.KEY_TOTAL, today_key, user_key, category_key):
                    if key is None:
                        continue
                    conn.execute(
                        "INSERT INTO counters(key, value) VALUES(?, 1) "
                        "ON CONFLICT(key) DO UPDATE SET value = value + 1",
                        (key,),
                    )
                conn.execute(
                    "INSERT INTO last_seen(user_id, timestamp) VALUES(?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET timestamp = excluded.timestamp",
                    (user_id, int(now.timestamp())),
                )
                conn.commit()
        except Exception as exc:
            # Counters are best-effort; don't break the request on failure.
            log.warning("stats.record_message_failed", err=str(exc), user_id=user_id)

    async def record_safety_rejected(self) -> None:
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT INTO counters(key, value) VALUES(?, 1) "
                    "ON CONFLICT(key) DO UPDATE SET value = value + 1",
                    (self.KEY_SAFETY_REJECTED,),
                )
                conn.commit()
        except Exception as exc:
            log.warning("stats.record_safety_rejected_failed", err=str(exc))

    # ── Readers ──────────────────────────────────────────────────────────

    async def aggregate(self) -> dict:
        """Snapshot of headline counters for the admin overview."""
        now = datetime.now(timezone.utc)
        today_key = f"{self.KEY_DAY_PREFIX}{now.strftime('%Y-%m-%d')}"

        total = self._get_counter(self.KEY_TOTAL)
        today = self._get_counter(today_key)
        safety_rejected = self._get_counter(self.KEY_SAFETY_REJECTED)
        last_7d = self._sum_last_n_days(7)

        return {
            "total_messages":  total,
            "messages_today":  today,
            "messages_7d":     last_7d,
            "safety_rejected": safety_rejected,
        }

    def _get_counter(self, key: str) -> int:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT value FROM counters WHERE key = ?", (key,)
            ).fetchone()
        return int(row[0]) if row else 0

    def _sum_last_n_days(self, n: int) -> int:
        """Sum the per-day counters for the last `n` days (today inclusive)."""
        now = datetime.now(timezone.utc)
        total = 0
        for offset in range(n):
            day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            day = day.fromordinal(day.toordinal() - offset)
            key = f"{self.KEY_DAY_PREFIX}{day.strftime('%Y-%m-%d')}"
            total += self._get_counter(key)
        return total

    async def message_count_for_user(self, user_id: str) -> int:
        return self._get_counter(f"{self.KEY_USER_PREFIX}{user_id}")

    async def last_seen_for_user(self, user_id: str) -> Optional[int]:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT timestamp FROM last_seen WHERE user_id = ?", (user_id,)
            ).fetchone()
        return int(row[0]) if row else None
