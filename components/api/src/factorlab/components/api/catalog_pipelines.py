"""Plain-language registry of the collection pipelines that write the v2 tables."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Market = Literal["India", "US", "Political", "Broker"]
# GLOBEX is the CME futures week (factorlab.calendars.globex), not an exchange_calendars code.
SessionCalendar = Literal["XBOM", "XNYS", "GLOBEX"]

COMMON_TABLES = ("raw.archive", "meta.ingestion_runs")
# The index engine records derived.computations rows instead of ingestion runs.
INDEX_PIPELINE = "us_index_compute"


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
    # Compose service whose runs these are (monitoring and the collection plan).
    service: str
    # Declared universe id the runs record in meta.ingestion_runs.universe_id; the
    # collection plan flags runs reporting another. Empty when runs carry none.
    universe: str = ""
    # Bar resolution collected; None for reference, snapshot and derived pipelines.
    resolution: str | None = None
    scheduled: bool = True
    # One run per symbol: summarize, never list every run.
    per_symbol_runs: bool = False
    # Runs at the IBKR_SNAPSHOT_TIMES New York slots on XNYS sessions (source_health).
    slot_schedule: bool = False


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
        tables_written=(
            "market.bars",
            "market.futures_contract_bars",
            "meta.expected_series",
            "meta.unresolved_entities",
            *COMMON_TABLES,
        ),
        service="ingest-india",
        universe="full_nse_eq",
        resolution="1min",
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
        service="ingest-india",
        universe="full_nse_eq",
        resolution="1min",
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
        tables_written=(
            "ref.entities",
            "ref.securities",
            "ref.listings",
            "ref.contracts",
            "ref.identifier_aliases",
            *COMMON_TABLES,
        ),
        service="ingest-india",
        universe="full_nse_eq",
        scheduled=False,
    ),
    PipelineInfo(
        id="us_live",
        label="US live bars",
        market="US",
        source="Schwab",
        description="Collects the current session's 1-minute bars for the liquid minute tier (up to 250 of the configured US equities, ETFs, and index values such as $SPX).",
        schedule="A sweep every 5 minutes during the US regular session (09:30–16:00 ET).",
        expected_interval_seconds=900,
        session_calendar="XNYS",
        tables_written=(
            "market.bars",
            "meta.session_coverage",
            "meta.recovery_state",
            "meta.source_status",
            *COMMON_TABLES,
        ),
        service="ingest-us",
        universe="us_liquid_250",
        resolution="1min",
        per_symbol_runs=True,
    ),
    PipelineInfo(
        id="us_recovery_daily",
        label="US daily history",
        market="US",
        source="Schwab",
        description="Loads and repairs full daily price history for each US equity, ETF, and index value.",
        schedule="Continuously until every symbol's daily history is complete; retries after 15 minutes.",
        expected_interval_seconds=None,
        session_calendar=None,
        tables_written=(
            "market.bars",
            "meta.session_coverage",
            "meta.recovery_state",
            *COMMON_TABLES,
        ),
        service="ingest-us",
        # Daily runs record the published membership's name (universe-us config ``name``).
        universe="us_configured",
        resolution="daily",
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
        tables_written=(
            "market.bars",
            "meta.session_coverage",
            "meta.recovery_state",
            *COMMON_TABLES,
        ),
        service="ingest-us",
        universe="us_liquid_250",
        resolution="1min",
        per_symbol_runs=True,
    ),
    PipelineInfo(
        id="us_universe_sync",
        label="US universe",
        market="US",
        source="iShares IWV holdings + Schwab",
        description="Resolves which US equities, ETFs, and index values to collect from the Russell 3000 proxy, validates each one with Schwab, and records membership and GICS sectors.",
        schedule="Once a day; retries after 15 minutes on failure.",
        expected_interval_seconds=2 * 86_400,
        session_calendar=None,
        tables_written=(
            "ref.entities",
            "ref.securities",
            "ref.listings",
            "ref.identifier_aliases",
            "ref.exchanges",
            "ref.universes",
            "ref.universe_membership",
            "ref.sectors",
            "ref.security_sector_history",
            "meta.expected_series",
            "meta.source_status",
            "meta.unresolved_entities",
            *COMMON_TABLES,
        ),
        service="universe-us",
        universe="us_configured",
    ),
    PipelineInfo(
        id="us_shares_outstanding",
        label="US shares outstanding",
        market="US",
        source="Schwab",
        description="Snapshots each US equity's shares outstanding, which weights FactorLab's cap-weighted indexes.",
        schedule="After each successful US universe refresh, once a day.",
        expected_interval_seconds=2 * 86_400,
        session_calendar=None,
        tables_written=(
            "fundamentals.shares_outstanding",
            "meta.unresolved_entities",
            *COMMON_TABLES,
        ),
        service="universe-us",
    ),
    PipelineInfo(
        id=INDEX_PIPELINE,
        label="US sector indexes",
        market="US",
        source="FactorLab",
        description="Computes FactorLab's equal- and cap-weighted GICS sector indexes and whole-universe composites from daily closes, sectors, and shares outstanding.",
        schedule="Weekdays at 23:30 UTC, after the daily bar pass; rewrites the last 14 days.",
        # Friday's run to Monday's, plus an hour of grace.
        expected_interval_seconds=3 * 86_400 + 3_600,
        session_calendar=None,
        # Runs are derived.computations rows, read through the catalog's RUNS_SOURCE.
        tables_written=("derived.index_levels", "derived.index_weights", "derived.computations"),
        service="indexes-us",
        resolution="daily",
    ),
    PipelineInfo(
        id="us_futures_roster",
        label="US futures roster",
        market="US",
        source="Schwab",
        description="Resolves the dated contracts to record for each US futures root (equity index, energy, metals, rates, FX).",
        schedule="Once per Globex session, at its open.",
        expected_interval_seconds=25 * 3_600,
        session_calendar="GLOBEX",
        tables_written=(
            "ref.entities",
            "ref.securities",
            "ref.listings",
            "ref.contracts",
            "ref.identifier_aliases",
            "meta.unresolved_entities",
            *COMMON_TABLES,
        ),
        service="futures-us",
        universe="us_futures",
    ),
    PipelineInfo(
        id="us_futures_quotes",
        label="US futures quotes",
        market="US",
        source="Schwab",
        description="Snapshots every roster contract's last trade, bid and ask, day range, volume, open interest, and settlement.",
        schedule="Every minute while Globex is open (Sunday 18:00 to Friday 17:00 ET, with a daily 17:00–18:00 break).",
        expected_interval_seconds=600,
        session_calendar="GLOBEX",
        tables_written=("market.futures_quotes", *COMMON_TABLES),
        service="futures-us",
        universe="us_futures",
    ),
    PipelineInfo(
        id="us_futures_daily",
        label="US futures daily bars",
        market="US",
        source="Schwab",
        description="Builds one daily bar per futures contract from the session's quotes, then sets the close to the exchange settlement.",
        schedule="A provisional bar at each 17:00 ET close; the settled bar an hour into the next session.",
        # Friday's provisional bar to Sunday evening's settled one.
        expected_interval_seconds=3 * 86_400,
        session_calendar=None,
        tables_written=("market.futures_contract_bars", *COMMON_TABLES),
        service="futures-us",
        universe="us_futures",
        resolution="daily",
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
        tables_written=(
            "alt.political_trades",
            "alt.political_filings",
            "alt.political_committees",
            "alt.political_committee_memberships",
            "ref.entities",
            "ref.identifier_aliases",
            "ref.legislator_terms",
            *COMMON_TABLES,
        ),
        service="ingest-political",
        universe="house",
    ),
    PipelineInfo(
        id="ibkr_broker_snapshot",
        label="IBKR broker snapshot",
        market="Broker",
        source="IBKR Gateway",
        description="Snapshots positions, balances, open orders and recent executions from the paper and live IBKR Gateways.",
        schedule="At the IBKR_SNAPSHOT_TIMES New York slots on NYSE trading days (default 06:00 and 16:30).",
        expected_interval_seconds=None,
        session_calendar="XNYS",
        tables_written=(
            "broker.positions_snapshot",
            "broker.account_state_snapshot",
            "broker.open_orders_snapshot",
            "broker.executions",
            "meta.source_status",
            *COMMON_TABLES,
        ),
        service="ibkr-snapshot",
        universe="ibkr_accounts",
        slot_schedule=True,
    ),
    PipelineInfo(
        id="ibkr_broker_simulation",
        label="IBKR simulated accounts",
        market="Broker",
        source="Synthetic IBKR responses",
        description="Ingests synthetic broker responses for the shared test profiles' simulated accounts.",
        schedule="Manual; run by an operator for test profiles.",
        expected_interval_seconds=None,
        session_calendar=None,
        tables_written=(
            "broker.positions_snapshot",
            "broker.account_state_snapshot",
            "broker.open_orders_snapshot",
            "broker.executions",
            *COMMON_TABLES,
        ),
        service="ibkr-snapshot",
        scheduled=False,
    ),
    PipelineInfo(
        id="broker.flex_activity:ibkr",
        label="IBKR Flex statements",
        market="Broker",
        source="IBKR Flex Web Service",
        description="Imports an IBKR Activity Flex statement: fills, executed orders, cash movements and end-of-day positions, with every statement row kept whole.",
        schedule="Manual; run by an operator on the VPS (the Flex token is IP-restricted).",
        expected_interval_seconds=None,
        session_calendar=None,
        tables_written=(
            "broker.executions",
            "broker.order_events",
            "broker.cash_flows",
            "broker.positions_snapshot",
            "broker.statement_rows",
            *COMMON_TABLES,
        ),
        # Run with `engine run` inside the ibkr-snapshot container.
        service="ibkr-snapshot",
        scheduled=False,
    ),
)

PIPELINES_BY_ID = {pipeline.id: pipeline for pipeline in PIPELINES}


def writers_of(table: str) -> list[PipelineInfo]:
    return [pipeline for pipeline in PIPELINES if table in pipeline.tables_written]
