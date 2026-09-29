"""Typed view of ``configs/sources/eodhd.yaml`` (docs/architecture/07 §9.3)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from factorlab.ingest.bindings import ProviderSettings

ALIAS_KIND = "eodhd_symbol"

# EODHD "Exchange" field -> ref.exchanges code; matches eodhd/us_universe.VENUE_MAP.
DEFAULT_VENUES = {
    "NASDAQ": "XNAS",
    "NYSE": "XNYS",
    "NYSE MKT": "XASE",
    "AMEX": "XASE",
    "NYSE AMERICAN": "XASE",
    "NYSE ARCA": "ARCX",
}


class EodhdApi(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    base_url: str = "https://eodhd.com/api"
    key_env: str = "EODHD_API_KEY"  # a secret NAME, resolved through get_secret()
    key_param: str = "api_token"
    timeout: float = 60.0


class EodhdRateLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    requests_per_second: float = 16.0
    requests_per_minute: int = 1000

    def windows(self) -> tuple[tuple[float, int], ...]:
        return ((1.0, max(int(self.requests_per_second), 1)), (60.0, self.requests_per_minute))


class EodhdIndex(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # e.g. GSPC.INDX
    minimum_constituents: int = Field(ge=1)
    name: str = ""


class EodhdSettings(ProviderSettings):
    api: EodhdApi = Field(default_factory=EodhdApi)
    rate_limits: EodhdRateLimits = Field(default_factory=EodhdRateLimits)
    default_exchange: str = "US"
    venues: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_VENUES))
    # EODHD "Type" values kept for ref.listings -> canonical product_type.
    listing_types: dict[str, str] = Field(default_factory=lambda: {"common stock": "common"})
    bulk_max_days: int = Field(default=5, ge=1, le=31)
    universes: dict[str, EodhdIndex] = Field(default_factory=dict)
