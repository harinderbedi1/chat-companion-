"""Pydantic request and response models — the wire contract.

Bad inputs are caught here and return 422 with field-level detail before
any downstream code runs.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _coerce_to_str(value):
    """Accept int/float JSON values where the contract is a string.

    Some platforms send user_id, category.id, or authorization_key as a
    JSON number instead of a string. We accept either and store as str.
    """
    if isinstance(value, (int, float)):
        return str(value)
    return value


class Category(BaseModel):
    """Topic context attached to every user message.

    ``title`` goes into the prompt so the AI knows what topic the user
    belongs to. ``id`` is the platform's stable identifier for that
    category — we log it and bump a per-category counter so the admin
    dashboard can break down activity by category.
    """

    # Accept unknown category fields so the platform can evolve freely.
    model_config = ConfigDict(extra="allow")

    id: Optional[str] = Field(None, max_length=128)
    title: str = Field(..., min_length=1, max_length=128)

    @field_validator("id", mode="before")
    @classmethod
    def _id_to_str(cls, value):
        return _coerce_to_str(value)


class ReplyRequest(BaseModel):
    """Incoming chat request from the platform."""

    # Accept either a string (e.g. "u_alice_42") or a number (e.g. 12345).
    # We normalize to string internally via the validator below.
    user_id: str = Field(..., min_length=1, max_length=64)
    text: str = Field(..., min_length=1, max_length=8000)
    category: Category
    # Same flexibility for the shared secret — string or number, your choice.
    authorization_key: str = Field(...)
    # Optional epoch-seconds timestamp of when the user sent the message.
    # If present, we prefix it onto the message so the bot can reason about
    # time gaps in the conversation history.
    timestamp: Optional[int] = Field(None, ge=0)

    @field_validator("user_id", "authorization_key", mode="before")
    @classmethod
    def _normalize_to_str(cls, value):
        return _coerce_to_str(value)


class ReplyResponse(BaseModel):
    """Outgoing chat reply to the platform."""

    # model_used clashes with Pydantic's protected `model_` namespace.
    model_config = ConfigDict(protected_namespaces=())

    user_id: str
    reply: str
    model_used: str
    prompt_version: str
    request_id: str


class HealthResponse(BaseModel):
    status: str


class DeleteHistoryResponse(BaseModel):
    user_id: str
    deleted_keys: int
