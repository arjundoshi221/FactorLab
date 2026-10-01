"""Runtime collection scope; daily membership is always the full published roster."""

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import SettingsConfigDict

from factorlab.core.settings import FactorLabSettings


class CollectionSettings(FactorLabSettings):
    model_config = SettingsConfigDict(env_prefix="US_")
    minute_tier_size: int = Field(default=250, ge=0, le=250)
    live_interval_seconds: int = Field(default=300, ge=1)
    ingest_lock: Path = Path("/app/data/us-ingest.lock")
    schwab_min_request_interval_seconds: float = Field(
        default=1.0,
        gt=0,
        allow_inf_nan=False,
        validation_alias="SCHWAB_MIN_REQUEST_INTERVAL_SECONDS",
    )


class ConstituentCsvIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: Path
    symbol_column: str = Field(default="Symbol", min_length=1)
    # The roster's effective timestamp, never a file's modification timestamp.
    as_of: datetime
    max_age_days: int = Field(default=7, ge=1)
    expected_constituents: int = Field(ge=1)
    source_url: str = Field(min_length=1)


class ConstituentCsvProvider(BaseModel):
    model_config = ConfigDict(extra="forbid")
    indexes: dict[str, ConstituentCsvIndex]
