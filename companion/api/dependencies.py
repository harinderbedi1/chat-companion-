"""FastAPI dependency factories.

Services live in ``app.state`` (constructed once in
:mod:`companion.api.main` ``lifespan``). These factory functions hand them to
route handlers via ``Depends()``. Centralized here so swapping a service
for a test stub is one line in a test fixture.
"""

from fastapi import Request

from companion.services.auth import AuthService
from companion.services.chat import ChatService
from companion.services.history import HistoryService


def get_chat_service(request: Request) -> ChatService:
    """Return the per-process ChatService."""
    return request.app.state.chat_service


def get_auth_service(request: Request) -> AuthService:
    """Return the per-process AuthService."""
    return request.app.state.auth_service


def get_history_service(request: Request) -> HistoryService:
    """Return the per-process HistoryService."""
    return request.app.state.history_service
