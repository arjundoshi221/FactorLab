"""Dataset catalogue: one contract per canonical data product (docs/architecture/07 §4).

A dataset fixes its request type, record types, sink protocol and the one
sink method the engine calls. Providers implement datasets; they never define
them. Adding an entry here is the only change that needs DB-service work.
"""

from __future__ import annotations

from dataclasses import dataclass

from factorlab.ingest.datasets.broker import (
    AccountStateRow,
    BrokerSink,
    ExecutionRecord,
    OpenOrderSnapshot,
    PositionSnapshot,
    SnapshotRequest,
)
from factorlab.ingest.datasets.common import (
    RESOLUTIONS,
    Capabilities,
    DatasetSource,
    EntityRef,
    FetchUnit,
    InstrumentRef,
    WriteResult,
)
from factorlab.ingest.datasets.fundamentals import (
    CompanyRequest,
    FundamentalFilingRecord,
    FundamentalsSink,
    LineItemRecord,
)
from factorlab.ingest.datasets.market import (
    BarRecord,
    BarRequest,
    BarSink,
    ContractBarRecord,
    ContractBarSink,
    SeriesWindow,
)
from factorlab.ingest.datasets.political import (
    CommitteeRecord,
    FilingsRequest,
    LegislatorRecord,
    LegislatorSink,
    MembershipRecord,
    PoliticalFilingRecord,
    PoliticalFilingSink,
    PoliticalTradeRecord,
    PoliticalTradeSink,
)
from factorlab.ingest.datasets.reference import (
    ContractRecord,
    ContractSink,
    InstrumentRecord,
    InstrumentSink,
    ReferenceRequest,
)
from factorlab.ingest.datasets.universe import (
    ConstituentRecord,
    UniverseReader,
    UniverseSink,
)


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    id: str
    tables: tuple[str, ...]
    request_type: type
    record_types: tuple[type, ...]
    sink_protocol: type
    sink_method: str
    has_resolution: bool = False
    reference: bool = False  # reference datasets may mint identity (primary bindings only)
    requires_alias: bool = True  # records must carry the provider's own identifier
    # `source` is part of every target table's sort key, so providers' rows coexist.
    # Shadow bindings of datasets without it normalize and count but never write.
    source_keyed: bool = False


DATASETS: dict[str, DatasetSpec] = {
    spec.id: spec
    for spec in (
        DatasetSpec(
            id="ref.listings",
            tables=("ref.entities", "ref.securities", "ref.listings", "ref.identifier_aliases"),
            request_type=ReferenceRequest,
            record_types=(InstrumentRecord,),
            sink_protocol=InstrumentSink,
            sink_method="upsert_instruments",
            reference=True,
        ),
        DatasetSpec(
            id="ref.contracts",
            tables=("ref.contracts", "ref.identifier_aliases"),
            request_type=ReferenceRequest,
            record_types=(ContractRecord,),
            sink_protocol=ContractSink,
            sink_method="upsert_contracts",
            reference=True,
        ),
        DatasetSpec(
            id="ref.universe_membership",
            tables=("ref.universes", "ref.universe_membership"),
            request_type=ReferenceRequest,
            record_types=(ConstituentRecord,),
            sink_protocol=UniverseSink,
            sink_method="write_constituents",
            reference=True,
            requires_alias=False,  # constituent lists are often bare tickers
        ),
        DatasetSpec(
            id="ref.legislators",
            tables=(
                "ref.entities",
                "ref.identifier_aliases",
                "ref.legislator_terms",
                "alt.political_committees",
                "alt.political_committee_memberships",
            ),
            request_type=ReferenceRequest,
            record_types=(LegislatorRecord, CommitteeRecord, MembershipRecord),
            sink_protocol=LegislatorSink,
            sink_method="write_legislators",
            reference=True,
            requires_alias=False,  # keyed by EntityRef (bioguide), not InstrumentRef
        ),
        DatasetSpec(
            id="alt.political_filings",
            tables=("alt.political_filings",),
            request_type=ReferenceRequest,
            record_types=(PoliticalFilingRecord,),
            sink_protocol=PoliticalFilingSink,
            sink_method="write_political_filings",
            requires_alias=False,
        ),
        DatasetSpec(
            id="alt.political_trades",
            tables=("alt.political_trades", "alt.political_filings"),
            request_type=FilingsRequest,
            record_types=(PoliticalTradeRecord,),
            sink_protocol=PoliticalTradeSink,
            sink_method="write_political_trades",
            requires_alias=False,  # tickers from filings are bare, resolved point-in-time
        ),
        DatasetSpec(
            id="fundamentals.filings",
            tables=("fundamentals.filings", "fundamentals.line_items"),
            request_type=CompanyRequest,
            record_types=(FundamentalFilingRecord, LineItemRecord),
            sink_protocol=FundamentalsSink,
            sink_method="write_fundamentals",
            requires_alias=False,  # issuers are EntityRefs (CIK), resolved by the DB service
        ),
        DatasetSpec(
            id="broker.snapshot",
            tables=(
                "broker.positions_snapshot",
                "broker.account_state_snapshot",
                "broker.executions",
                "broker.open_orders_snapshot",
            ),
            request_type=SnapshotRequest,
            record_types=(PositionSnapshot, AccountStateRow, ExecutionRecord, OpenOrderSnapshot),
            sink_protocol=BrokerSink,
            sink_method="write_snapshot",
            requires_alias=False,  # rows carry broker_code + vendor_id, resolved in the DB service
        ),
        DatasetSpec(
            id="market.bars",
            tables=("market.bars",),
            request_type=BarRequest,
            record_types=(BarRecord,),
            sink_protocol=BarSink,
            sink_method="write_bars",
            has_resolution=True,
            source_keyed=True,
        ),
        DatasetSpec(
            id="market.futures_contract_bars",
            tables=("market.futures_contract_bars",),
            request_type=BarRequest,
            record_types=(ContractBarRecord,),
            sink_protocol=ContractBarSink,
            sink_method="write_contract_bars",
            has_resolution=True,
            source_keyed=True,
        ),
    )
}


def dataset(dataset_id: str) -> DatasetSpec:
    try:
        return DATASETS[dataset_id]
    except KeyError:
        raise KeyError(f"unknown dataset {dataset_id!r}; known: {sorted(DATASETS)}") from None


__all__ = [
    "DATASETS",
    "RESOLUTIONS",
    "AccountStateRow",
    "BarRecord",
    "BarRequest",
    "BarSink",
    "BrokerSink",
    "Capabilities",
    "ConstituentRecord",
    "ContractBarRecord",
    "ContractBarSink",
    "ContractRecord",
    "ContractSink",
    "DatasetSource",
    "DatasetSpec",
    "EntityRef",
    "ExecutionRecord",
    "FetchUnit",
    "InstrumentRecord",
    "InstrumentRef",
    "InstrumentSink",
    "OpenOrderSnapshot",
    "PositionSnapshot",
    "ReferenceRequest",
    "SeriesWindow",
    "SnapshotRequest",
    "UniverseReader",
    "UniverseSink",
    "WriteResult",
    "dataset",
]
