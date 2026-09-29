"""Stable ClickHouse v2 identities shared by migration and live ingestion."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

ENTITY_NAMESPACE = uuid.UUID("7cc91b95-bef0-4d70-81a4-23757e6218cd")
SECURITY_NAMESPACE = uuid.UUID("91529032-aef5-44f7-b33e-25f05f620d22")
LISTING_NAMESPACE = uuid.UUID("74360588-b425-4daf-bbff-9a601f8e97e4")
CONTRACT_NAMESPACE = uuid.UUID("f889fd4f-bc13-4450-ac2e-a477f0f07189")
LEGISLATOR_NAMESPACE = uuid.UUID("be05fde7-382d-4a7e-b84d-d7871c082f06")
COMMITTEE_NAMESPACE = uuid.UUID("dd083df5-fbbc-4b07-a16e-06e4b1576533")
POLITICAL_TRADE_NAMESPACE = uuid.UUID("52b5ecf1-d93b-425f-a84c-066a839de77c")
FILING_NAMESPACE = uuid.UUID("3f1c9a7e-5b2d-4c8e-9a61-7d0f4e2b8c35")


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


def natural_listing_ids(
    *, exchange_code: str, trading_symbol: str, isin: str | None
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Provider-neutral identity for a listing seen for the first time (07 §8.1 step 3).

    Issuer and security keep the ISIN derivation used by :func:`canonical_ids`,
    so a new listing of a known ISIN joins the existing security. The listing
    key is the exchange plus ISIN (or symbol when there is no ISIN) and never a
    vendor identifier. Existing listing ids are never re-derived (07 §8.2).
    """
    isin_text = _text(isin).strip().upper()
    exchange = _text(exchange_code).strip().upper()
    symbol = _text(trading_symbol).strip()
    security_key = f"isin:{isin_text}" if isin_text else f"listing:{exchange}:sym:{symbol}"
    listing_key = f"isin:{isin_text}" if isin_text else f"sym:{symbol}"
    return (
        uuid.uuid5(ENTITY_NAMESPACE, security_key),
        uuid.uuid5(SECURITY_NAMESPACE, security_key),
        uuid.uuid5(LISTING_NAMESPACE, f"factorlab:listing:{exchange}:{listing_key}"),
    )


def natural_contract_id(
    *,
    underlying_listing_id: uuid.UUID,
    contract_type: str,
    expiry: Any,
    right: str | None,
    strike: Any,
) -> uuid.UUID:
    """Provider-neutral contract identity from the ``ref.contracts`` natural key."""
    return uuid.uuid5(
        CONTRACT_NAMESPACE,
        f"factorlab:contract:{underlying_listing_id}:{contract_type}:{expiry}:"
        f"{right or ''}:{'' if strike is None else strike}",
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
    return uuid.uuid5(POLITICAL_TRADE_NAMESPACE, f"factorlab:alt_political_trades:{trade_key}")


def filing_id(regulator: str, accession_number: str) -> uuid.UUID:
    """Canonical regulatory filing id, e.g. ``filing_id("sec", "0000320193-26-000071")``."""
    return uuid.uuid5(FILING_NAMESPACE, f"factorlab:filing:{regulator}:{accession_number}")
