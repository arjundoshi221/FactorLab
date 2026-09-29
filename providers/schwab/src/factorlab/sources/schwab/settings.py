"""Typed view of ``configs/sources/schwab.yaml`` (docs/architecture/07 §9.3)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from factorlab.ingest.bindings import ProviderSettings

ALIAS_KIND = "schwab_symbol"

# Schwab exchange names/codes -> ref.exchanges code; matches universe/schwab.SUPPORTED_EXCHANGES.
DEFAULT_EXCHANGES = {
    "A": "XASE",
    "AMEX": "XASE",
    "NYSE AMERICAN": "XASE",
    "N": "XNYS",
    "NYSE": "XNYS",
    "P": "ARCX",
    "NYSE ARCA": "ARCX",
    "Q": "XNAS",
    "NASDAQ": "XNAS",
    "NASDAQ GLOBAL MARKET": "XNAS",
    "NASDAQ GLOBAL SELECT": "XNAS",
    "NASDAQ CAPITAL MARKET": "XNAS",
}


class SchwabApi(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    base_url: str = "https://api.schwabapi.com"
    access_token_env: str = "SCHWAB_ACCESS_TOKEN"  # a secret NAME; expiry at <NAME>.expires_at
    timeout: float = 30.0


class SchwabRateLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    requests_per_second: float = 1.0
    requests_per_minute: int = 120

    def windows(self) -> tuple[tuple[float, int], ...]:
        return ((1.0, max(int(self.requests_per_second), 1)), (60.0, self.requests_per_minute))


class SchwabSettings(ProviderSettings):
    api: SchwabApi = Field(default_factory=SchwabApi)
    rate_limits: SchwabRateLimits = Field(default_factory=SchwabRateLimits)
    exchanges: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_EXCHANGES))
    minute_max_lookback_days: int = Field(default=48, ge=1)
    minute_chunk_days: int = Field(default=10, ge=1, le=10)
