"""Typed view of ``configs/sources/schwab.yaml`` (docs/architecture/07 §9.3)."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from factorlab.ingest.bindings import ProviderSettings

ALIAS_KIND = "schwab_symbol"
# Shared with the auth Worker and the secrets agent; ids become secret and file names.
ACCOUNT_ID = re.compile(r"[a-z0-9_]{1,32}")
# Pseudo MIC of US index values; seeded by V2USStorage.seed_us_reference_data.
INDEX_VENUE = "INDX"
# Futures venues by MIC (F-043); seeded with the same codes by V2USStorage.
FUTURES_VENUES = frozenset({"XCME", "XCBT", "XNYM", "XCEC", "XCBF", "IFUS"})

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
    "Z": "BATS",
    "CBOE": "BATS",
    "CBOE BZX": "BATS",
    # Index values ($SPX, $COMPX, ...) are published, not traded; one pseudo venue.
    "INDEX": INDEX_VENUE,
    "IND": INDEX_VENUE,
}


PRIMARY_ACCOUNT = "primary"


class SchwabApi(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    base_url: str = "https://api.schwabapi.com"
    access_token_env: str = "SCHWAB_ACCESS_TOKEN"  # a secret NAME; expiry at <NAME>.expires_at
    timeout: float = 30.0
    # Schwab developer apps pooled by the transport; each has its own token and rate budget.
    accounts: tuple[str, ...] = Field(default=(PRIMARY_ACCOUNT,), min_length=1)

    @field_validator("accounts")
    @classmethod
    def _valid_accounts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for account in value:
            if not ACCOUNT_ID.fullmatch(account):
                raise ValueError(f"Schwab account id {account!r} must match {ACCOUNT_ID.pattern}")
        if len(set(value)) != len(value):
            raise ValueError("Schwab account ids must be unique")
        return value

    def token_secret(self, account: str) -> str:
        """Secret name of an account's access token: ``primary`` keeps the legacy name."""
        if account == PRIMARY_ACCOUNT:
            return self.access_token_env
        return f"{self.access_token_env}_{account.upper()}"


class SchwabRateLimits(BaseModel):
    """Budget of one account (one developer app); the transport keeps one per account."""

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
    quote_batch_size: int = Field(default=50, ge=1, le=50)
