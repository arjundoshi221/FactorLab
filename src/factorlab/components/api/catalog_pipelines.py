"""Plain-language registry of the collection pipelines that write the v2 tables."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Market = Literal["India", "US", "Political"]
SessionCalendar = Literal["XBOM", "XNYS"]

COMMON_TABLES = ("raw.archive", "meta.ingestion_runs")


@dataclass(frozen=True)
class PipelineInfo:
    id: str
    label: str
    market: Market
    source: str
    description: str
    schedule: str
    # Longest normal gap between runs; None for on-demand pipelines with no cadence to miss.
    expected_interval_seconds: int | None
    # Runs are expected only while this exchange is open; None means around the clock.
    session_calendar: SessionCalendar | None
    tables_written: tuple[str, ...]
    scheduled: bool = True
    # One run per symbol: summarize, never list every run.
    per_symbol_runs: bool = False


PIPELINES: tuple[PipelineInfo, ...] = (
    PipelineInfo(
        id="india_intraday_1min",
        label="India intraday bars",
        market="India",
        source="Upstox",
        description="Collects 1-minute bars for NSE cash equities and the nearest single-stock futures during the trading day.",
        schedule="Every 5 minutes on NSE trading days, 09:15–15:32 IST (futures every 10 minutes).",
        expected_interval_seconds=900,
        session_calendar="XBOM",
        tables_written=("market.bars", "market.futures_contract_bars", "meta.expected_series",
                        "meta.unresolved_entities", *COMMON_TABLES),
    ),
    PipelineInfo(
        id="india_historical_1min_recovery",
        label="India history recovery",
        market="India",
        source="Upstox",
        description="Back-fills missing 1-minute India bars from Upstox history when a session has gaps.",
        schedule="On demand after the collector detects missing minutes.",
        expected_interval_seconds=None,
        session_calendar=None,
        tables_written=("market.bars", "market.futures_contract_bars", *COMMON_TABLES),
    ),
    PipelineInfo(
        id="india_reference_premarket",
        label="India reference refresh",
        market="India",
        source="Upstox",
        description="Refreshes India listings, contracts, and identifier aliases from the Upstox instrument master.",
        schedule="Manual; not scheduled in production.",
        expected_interval_seconds=None,
        session_calendar=None,
        tables_written=("ref.entities", "ref.securities", "ref.listings", "ref.contracts",
                        "ref.identifier_aliases", *COMMON_TABLES),
        scheduled=False,
    ),
    PipelineInfo(
        id="us_live",
        label="US live bars",
        market="US",
        source="Schwab",
        description="Collects the current session's 1-minute bars for every configured US equity.",
        schedule="A sweep every 5 minutes during the US regular session (09:30–16:00 ET).",
        expected_interval_seconds=900,
        session_calendar="XNYS",
        tables_written=("market.bars", "meta.session_coverage", "meta.recovery_state", "meta.source_status",
                        *COMMON_TABLES),
        per_symbol_runs=True,
    ),
    PipelineInfo(
        id="us_recovery_daily",
        label="US daily history",
        market="US",
        source="Schwab",
        description="Loads and repairs full daily price history for each US equity.",
        schedule="Continuously until every symbol's daily history is complete; retries after 15 minutes.",
        expected_interval_seconds=None,
        session_calendar=None,
        tables_written=("market.bars", "meta.session_coverage", "meta.recovery_state", *COMMON_TABLES),
        per_symbol_runs=True,
    ),
    PipelineInfo(
        id="us_recovery_1min",
        label="US minute history",
        market="US",
        source="Schwab",
        description="Back-fills up to 60 days of 1-minute US bars and closes coverage gaps.",
        schedule="Continuously until minute history is complete; retries after 15 minutes.",
        expected_interval_seconds=None,
        session_calendar=None,
        tables_written=("market.bars", "meta.session_coverage", "meta.recovery_state", *COMMON_TABLES),
        per_symbol_runs=True,
    ),
    PipelineInfo(
        id="us_universe_sync",
        label="US universe",
        market="US",
        source="GitHub CSV + Schwab",
        description="Resolves which US equities to collect and validates each one with Schwab.",
        schedule="Once a day; retries after 15 minutes on failure.",
        expected_interval_seconds=2 * 86_400,
        session_calendar=None,
        tables_written=("ref.entities", "ref.securities", "ref.listings", "ref.identifier_aliases",
                        "ref.exchanges", "meta.expected_series", "meta.source_status", *COMMON_TABLES),
    ),
    PipelineInfo(
        id="political_bootstrap",
        label="US political disclosures",
        market="Political",
        source="House Clerk disclosures + congress-legislators",
        description="Collects members of Congress, committees, House periodic transaction reports, and the trades they disclose.",
        schedule="Daily at 02:15 UTC (07:45 IST).",
        expected_interval_seconds=2 * 86_400,
        session_calendar=None,
        tables_written=("alt.political_trades", "alt.political_filings", "alt.political_committees",
                        "alt.political_committee_memberships", "ref.entities", "ref.identifier_aliases",
                        "ref.legislator_terms", *COMMON_TABLES),
    ),
)

PIPELINES_BY_ID = {pipeline.id: pipeline for pipeline in PIPELINES}


def writers_of(table: str) -> list[PipelineInfo]:
    return [pipeline for pipeline in PIPELINES if table in pipeline.tables_written]
