"""Per-user conversation history backed by SQLite.

This is the file that isolates one user's chat from another. Every
message row is scoped by ``session_id``; nothing in the codebase ever
reads across users.

We define a tiny ``BaseChatMessageHistory`` subclass that LangChain's
``RunnableWithMessageHistory`` can use directly — no SQLAlchemy
required.
"""

import json
import sqlite3
import time
from typing import List

from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import (
    BaseMessage,
    messages_from_dict,
    messages_to_dict,
)

from companion.config import Settings


class SqliteChatMessageHistory(BaseChatMessageHistory):
    """LangChain-compatible chat history backed by one SQLite table."""

    def __init__(self, session_id: str, db_path: str) -> None:
        self.session_id = session_id
        self._db_path = db_path

    @property
    def messages(self) -> List[BaseMessage]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT message_json FROM chat_messages "
                "WHERE session_id = ? ORDER BY id",
                (self.session_id,),
            ).fetchall()
        if not rows:
            return []
        dicts = [json.loads(r[0]) for r in rows]
        return messages_from_dict(dicts)

    def add_message(self, message: BaseMessage) -> None:
        self.add_messages([message])

    def add_messages(self, messages: List[BaseMessage]) -> None:
        rows = [
            (self.session_id, json.dumps(d), int(time.time()))
            for d in messages_to_dict(messages)
        ]
        with sqlite3.connect(self._db_path) as conn:
            conn.executemany(
                "INSERT INTO chat_messages (session_id, message_json, created_at) "
                "VALUES (?, ?, ?)",
                rows,
            )
            conn.commit()

    def clear(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "DELETE FROM chat_messages WHERE session_id = ?",
                (self.session_id,),
            )
            conn.commit()


class HistoryService:
    """Owns conversation-history reads, writes, and per-user deletion."""

    def __init__(self, db_path: str, settings: Settings) -> None:
        self._db_path = db_path
        # TTL is not enforced inside SQLite. If a rolling TTL is needed,
        # run a periodic cleanup job that deletes rows older than N seconds.

    @staticmethod
    def session_id(user_id: str) -> str:
        """Return the session key for a user.

        This is the single function responsible for keeping each user's
        history isolated. If/when the platform adds a conversation_id,
        extend this method to combine the two.
        """
        return user_id

    def for_session(self, session_id: str) -> BaseChatMessageHistory:
        """Return a LangChain history object scoped to one session only."""
        return SqliteChatMessageHistory(session_id=session_id, db_path=self._db_path)

    async def delete_for_user(self, user_id: str) -> int:
        """Delete every message belonging to ``user_id``. Returns the row count."""
        session = self.session_id(user_id)
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                "DELETE FROM chat_messages WHERE session_id = ?",
                (session,),
            )
            conn.commit()
            return cur.rowcount
