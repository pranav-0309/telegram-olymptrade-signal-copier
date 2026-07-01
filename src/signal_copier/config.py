from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Telegram (not used by M2, declared for schema completeness) ------
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    telegram_phone: str = ""
    telegram_session_string: str = ""
    # Channel title pattern (case-insensitive substring, whitespace-normalized).
    # The bot scans the user's dialog list at startup and refuses to start
    # unless exactly one channel title contains this pattern.
    # Example: "Magic Trader Signals" (matches "📈 Magic Trader Signals 🚀").
    telegram_target_chat: str = "Magic Trader Signals"
    telegram_self_dm_notifications: bool = True

    # --- MT5 broker (M13 — replaces OLYMP_* block; docs/refactor.md §4.6) ----
    mt5_login: int = 0
    mt5_password: str = ""
    mt5_server: str = ""
    mt5_terminal_path: str | None = None

    # --- Database (not used by M2, declared for schema completeness) ------
    database_url: str = "postgresql://user:pass@localhost:5432/copier"

    # --- Trading (used by M2) ---------------------------------------------
    dry_run: bool = True
    require_confirm: bool = False
    amount_initial: Decimal = Field(default=Decimal("2.00"), gt=0)
    amount_gale1: Decimal = Field(default=Decimal("4.00"), gt=0)
    amount_gale2: Decimal = Field(default=Decimal("8.00"), gt=0)
    expiration_seconds: int = Field(default=300, gt=0)
    trigger_skew_tolerance_seconds: float = Field(default=2.0, ge=0)

    # --- Optional safety limits (FR-6.1/6.2/6.3, deferred to M6) ----------
    daily_loss_limit: Decimal = Field(default=Decimal("0.00"), ge=0)
    daily_trade_limit: int = Field(default=0, ge=0)
    daily_drawdown_pct: int = Field(default=0, ge=0, le=100)

    # --- Schedule / Timezone (used by M2) ---------------------------------
    timezone: str = "America/Sao_Paulo"
    log_path: Path = Path("./logs/signal_copier.log")

    # --- Validators --------------------------------------------------------

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {v!r}") from exc
        return v

    @field_validator("mt5_server")
    @classmethod
    def _validate_demo_server(cls, v: str) -> str:
        """FR-6.6 equivalent for MT5: refuse non-demo server.

        Empty string is allowed at config-load time (the runtime guard at
        __main__.py:49-56 catches missing MT5_* so existing tests/.env files
        stay green through M13.1). Non-empty values must contain 'demo'
        (case-insensitive substring) so a real-account login plus real
        server cannot start the bot.
        """
        if v == "":
            return v
        if "demo" not in v.lower():
            raise ValueError(
                f"mt5_server must contain 'demo' (case-insensitive); got {v!r}. "
                "Real-money trading is a v2 feature gated behind a clean demo soak test."
            )
        return v

    def tz(self) -> ZoneInfo:
        """Convenience accessor for the configured timezone."""
        return ZoneInfo(self.timezone)
