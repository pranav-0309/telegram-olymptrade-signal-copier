from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
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
    telegram_target_chat: str = "@analyst_channel"
    telegram_self_dm_notifications: bool = True

    # --- OlympTrade (not used by M2, declared for schema completeness) ----
    olymp_access_token: str = ""
    olymp_account_group: str = "demo"  # FR-6.6: must be "demo" for v1
    olymp_account_id: str = ""

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

    @field_validator("olymp_account_group")
    @classmethod
    def _validate_account_group(cls, v: str) -> str:
        if v not in {"demo", "real"}:
            raise ValueError(f"olymp_account_group must be 'demo' or 'real', got {v!r}")
        return v

    @model_validator(mode="after")
    def _demo_only_guardrail(self) -> Config:
        # FR-6.6: refuse to start with real account + dry_run off.
        if self.olymp_account_group == "real" and not self.dry_run:
            raise ValueError(
                "Refusing to start: OLYMP_ACCOUNT_GROUP=real requires DRY_RUN=true. "
                "Real-money trading is a v2 feature, gated behind a 7-day clean demo soak test."
            )
        return self

    def tz(self) -> ZoneInfo:
        """Convenience accessor for the configured timezone."""
        return ZoneInfo(self.timezone)
