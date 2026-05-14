"""Authentication of incoming requests.

Supports two modes:

* ``body``  — platform sends the shared secret in the ``authorization``
  field of the JSON body. Verified with constant-time compare.
* ``hmac_header`` — platform signs the raw body with HMAC-SHA256 plus a
  timestamp; we verify the signature and reject stale timestamps.

The mode is chosen via ``BOT_AUTH_MODE``.
"""

import hashlib
import hmac
import time

from fastapi import HTTPException, Request, status

from companion.config import Settings


class AuthService:
    """Verifies that an incoming request is from the platform."""

    def __init__(self, settings: Settings) -> None:
        self._secrets: list[bytes] = [s.encode() for s in settings.shared_secrets]
        self._mode: str = settings.auth_mode
        self._max_skew_seconds: int = settings.hmac_max_skew_seconds

    def verify_body(self, provided: str) -> None:
        """Verify the shared secret sent in the body's ``authorization`` field."""
        if not self._match(provided.encode()):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail={"error": "unauthorized"},
            )

    async def verify_signature(self, request: Request) -> bytes:
        """Verify the HMAC-SHA256 signature header. Returns the raw body bytes."""
        ts = request.headers.get("X-Timestamp", "")
        sig = request.headers.get("X-Signature-Sha256", "")
        if not ts.isdigit() or abs(time.time() - int(ts)) > self._max_skew_seconds:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail={"error": "stale_timestamp"},
            )
        raw = await request.body()
        signed = ts.encode() + b"." + raw
        for secret in self._secrets:
            expected = hmac.new(secret, signed, hashlib.sha256).hexdigest()
            if hmac.compare_digest(expected, sig):
                return raw
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"error": "bad_signature"},
        )

    def _match(self, provided: bytes) -> bool:
        """Constant-time comparison against every accepted secret."""
        return any(hmac.compare_digest(provided, s) for s in self._secrets)
