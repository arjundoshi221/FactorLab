"""Stable ClickHouse v2 identities shared by migration and live ingestion."""

from __future__ import annotations

import uuid
from typing import Any, Mapping

ENTITY_NAMESPACE = uuid.UUID("7cc91b95-bef0-4d70-81a4-23757e6218cd")
SECURITY_NAMESPACE = uuid.UUID("91529032-aef5-44f7-b33e-25f05f620d22")
LISTING_NAMESPACE = uuid.UUID("74360588-b425-4daf-bbff-9a601f8e97e4")
CONTRACT_NAMESPACE = uuid.UUID("f889fd4f-bc13-4450-ac2e-a477f0f07189")
LEGISLATOR_NAMESPACE = uuid.UUID("be05fde7-382d-4a7e-b84d-d7871c082f06")
COMMITTEE_NAMESPACE = uuid.UUID("dd083df5-fbbc-4b07-a16e-06e4b1576533")
POLITICAL_TRADE_NAMESPACE = uuid.UUID("52b5ecf1-d93b-425f-a84c-066a839de77c")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8").rstrip("\x00")
    return str(value)


def canonical_ids(instrument: Mapping[str, Any]) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Return issuer, security and listing IDs for a legacy-shaped instrument."""
    isin = _text(instrument.get("isin")).strip().upper()
    instrument_key = _text(instrument["instrument_key"])
    security_key = f"isin:{isin}" if isin else f"legacy:factorlab:ref_instruments:{instrument_key}"
    return (
        uuid.uuid5(ENTITY_NAMESPACE, security_key),
        uuid.uuid5(SECURITY_NAMESPACE, security_key),
        uuid.uuid5(LISTING_NAMESPACE, f"factorlab:ref_instruments:{instrument_key}"),
    )


def contract_id(contract_key: str) -> uuid.UUID:
    return uuid.uuid5(CONTRACT_NAMESPACE, f"factorlab:ref_contracts:{contract_key}")


def legislator_id(bioguide_id: str) -> uuid.UUID:
    return uuid.uuid5(LEGISLATOR_NAMESPACE, f"bioguide:{bioguide_id.upper()}")


def committee_id(legacy_committee_id: str) -> uuid.UUID:
    return uuid.uuid5(
        COMMITTEE_NAMESPACE, f"factorlab:alt_political_committees:{legacy_committee_id}"
    )


def political_trade_id(trade_key: str) -> uuid.UUID:
    return uuid.uuid5(
        POLITICAL_TRADE_NAMESPACE, f"factorlab:alt_political_trades:{trade_key}"
    )
