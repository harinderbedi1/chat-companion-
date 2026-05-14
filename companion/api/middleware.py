"""ASGI middleware — request-ID injection and access logging.

Every request gets a unique ``X-Request-Id`` (echoed in the response
header) bound into structlog's per-request context, so every log line
emitted during the request carries the same ID. Makes end-to-end
tracing trivial.
"""

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


log = structlog.get_logger()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Generate (or honor) ``X-Request-Id`` and bind it to the log context."""

    HEADER_NAME = "X-Request-Id"

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(self.HEADER_NAME) or uuid.uuid4().hex
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers[self.HEADER_NAME] = request_id
            log.info(
                "http.access",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                latency_ms=int((time.perf_counter() - start) * 1000),
            )
            return response
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
