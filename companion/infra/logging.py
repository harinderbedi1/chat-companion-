"""Structured logging configuration.

Every module gets a logger via ``structlog.get_logger()``. Output is JSON
on stdout — easy for any log aggregator to pick up. Sensitive field names
(``authorization``, ``api_key``, etc.) are auto-redacted before write.
"""

import logging
from typing import Any

import structlog


_SECRET_FIELD_NAMES = frozenset({
    "authorization",
    "api_key",
    "secret",
    "shared_secret",
    "token",
    "password",
})


def _redact_secrets(_: Any, __: str, event_dict: dict) -> dict:
    """structlog processor: replace known-secret fields with [REDACTED]."""
    for key in list(event_dict.keys()):
        if key.lower() in _SECRET_FIELD_NAMES:
            event_dict[key] = "[REDACTED]"
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog for the whole process. Idempotent."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=numeric_level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_secrets,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
