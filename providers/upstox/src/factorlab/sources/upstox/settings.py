"""Typed view of ``configs/sources/upstox.yaml`` (docs/architecture/07 §9.3)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from factorlab.ingest.bindings import ProviderSettings

ALIAS_KIND = "upstox_instrument_key"


class UpstoxApi(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    base_url: str = "https://api.upstox.com"
    token_env: str = "UPSTOX_ACCESS_TOKEN"  # a secret NAME, resolved through get_secret()
    timeout: float = 20.0


class UpstoxRateLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    per_second: int = 50
    per_minute: int = 500
    per_30_minutes: int = 2000

    def windows(self) -> tuple[tuple[float, int], ...]:
        return ((1.0, self.per_second), (60.0, self.per_minute), (1800.0, self.per_30_minutes))


class UpstoxSettings(ProviderSettings):
    api: UpstoxApi = Field(default_factory=UpstoxApi)
    rate_limits: UpstoxRateLimits = Field(default_factory=UpstoxRateLimits)
    instruments_url: str = (
        "https://assets.upstox.com/market-quote/instruments/exchange/{exchange}.json.gz"
    )
    exchanges: tuple[str, ...] = ("NSE",)
    # Matches today's V2IndiaStorage.sync_instruments / sync_contracts filters.
    listing_instrument_types: tuple[str, ...] = ("EQ",)
    contract_underlying_types: tuple[str, ...] = ("EQUITY",)
    history_available_from: date = date(2022, 1, 1)
    historical_chunk_days: int = Field(default=30, ge=1, le=31)
    quote_batch_size: int = Field(default=100, ge=1, le=100)
    retries: int = Field(default=2, ge=0)
