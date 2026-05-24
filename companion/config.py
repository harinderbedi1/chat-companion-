"""Runtime configuration loaded from environment variables.

Every other module imports ``Settings`` (or a single instance of it) from
here. Configuration is validated at boot — if a required env var is
missing or malformed, the service refuses to start.
"""

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Loaded once at process start."""

    model_config = SettingsConfigDict(
        env_prefix="BOT_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Identity ─────────────────────────────────────────────────────────
    platform_name: str = "Platform"
    # v4 = generic guidance (no inline examples), no-advice, generous recall.
    prompt_version: str = "v4"

    # ── Auth ─────────────────────────────────────────────────────────────
    shared_secret: str = Field(
        ...,
        min_length=8,
        description="Comma-separated values are allowed for zero-downtime rotation.",
    )
    auth_mode: str = Field("body", pattern="^(body|hmac_header)$")
    hmac_max_skew_seconds: int = 300

    # ── Storage (SQLite) ─────────────────────────────────────────────────
    # Path to the SQLite database file. ":memory:" works for tests.
    # For Docker, mount a volume at /data and set BOT_DB_PATH=/data/companion.db.
    db_path: str = "./companion.db"

    # ── Conversation history ─────────────────────────────────────────────
    history_max_messages: int = 20
    history_ttl_seconds: int = 30 * 24 * 3600

    # ── Idempotency cache ────────────────────────────────────────────────
    idempotency_ttl_seconds: int = 86400

    # ── History summarization ────────────────────────────────────────────
    # When stored history reaches this many messages, the SummaryService
    # compacts the older ones into a single summary message and keeps the
    # most recent N raw.
    history_summarize_threshold: int = 10
    history_keep_recent: int = 2

    # ── LLM provider routing (LangChain init_chat_model spec strings) ────
    llm_primary: str = "anthropic:claude-sonnet-4-6"
    llm_fallback: str = "openai:gpt-4o-mini"
    llm_primary_base_url: Optional[str] = None
    llm_fallback_base_url: Optional[str] = None
    llm_timeout_seconds: int = 30
    max_output_tokens: int = 800
    temperature: float = 0.5

    # ── Reply bounds (safety) ────────────────────────────────────────────
    min_reply_chars: int = 5
    max_reply_chars: int = 2000

    # ── Moderation ───────────────────────────────────────────────────────
    moderation_provider: str = Field("openai", pattern="^(openai|llama_guard|none)$")

    # ── Admin dashboard ──────────────────────────────────────────────────
    # Separate from shared_secret — only used by the admin UI.
    admin_token: Optional[str] = None
    # How long to keep daily counters before they expire from Redis.
    stats_daily_ttl_seconds: int = 90 * 24 * 3600

    # ── Observability ────────────────────────────────────────────────────
    log_level: str = "INFO"
    langfuse_host: Optional[str] = None
    langfuse_public_key: Optional[str] = None
    langfuse_secret_key: Optional[str] = None

    # ── Derived helpers ──────────────────────────────────────────────────
    @property
    def shared_secrets(self) -> list[str]:
        """Return the list of accepted shared secrets (for rotation)."""
        return [s.strip() for s in self.shared_secret.split(",") if s.strip()]

    def model_spec(self, role: str) -> str:
        """Return the LangChain provider:model spec for the given role."""
        if role == "primary":
            return self.llm_primary
        if role == "fallback":
            return self.llm_fallback
        raise ValueError(f"Unknown LLM role: {role!r}")

    def base_url(self, role: str) -> Optional[str]:
        """Return the override base URL for the given role, if any."""
        if role == "primary":
            return self.llm_primary_base_url
        if role == "fallback":
            return self.llm_fallback_base_url
        raise ValueError(f"Unknown LLM role: {role!r}")
