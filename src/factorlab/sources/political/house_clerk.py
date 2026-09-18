"""House Clerk filing-index and PTR parsing."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from datetime import date, datetime
from typing import Any
from xml.etree import ElementTree

import pdfplumber
import requests

from factorlab.storage.clickhouse import ClickHouseStorage

_INDEX_URL = (
    "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.ZIP"
)
_PTR_URL = (
    "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{filing_id}.pdf"
)
PARSER_VERSION = "house_ptr_v1"

_TRANSACTION_CORE_PATTERN = re.compile(
    r"\b(?P<tx_type>P|S|E)"
    r"(?:\s+\((?P<sale_scope>partial|full)\))?\s+"
    r"(?P<transaction_date>\d{1,2}/\d{1,2}/\d{4})"
    r"(?:\s+(?P<notification_date>\d{1,2}/\d{1,2}/\d{4}))?\s+"
    r"(?P<amount>(?:\$[\d,]+\s*-\s*\$[\d,]+)|(?:Over\s+\$[\d,]+))",
    re.IGNORECASE,
)
_ASSET_TYPE_PATTERN = re.compile(r"\[(?P<asset_type>[A-Z]{2})\]")
_PAREN_TICKER_PATTERN = re.compile(
    r"\((?P<ticker>[A-Z0-9.\-/]{1,15})\)\s*\[[A-Z]{2}\]",
    re.IGNORECASE,
)
_BARE_TICKER_PATTERN = re.compile(
    r"(?:NYSEARCA:\s*)?(?P<ticker>[A-Z][A-Z0-9.\-/]{0,14})\s*\[[A-Z]{2}\]",
    re.IGNORECASE,
)

_OWNER_TYPES = {
    "": "self",
    "SP": "spouse",
    "JT": "joint",
    "DC": "dependent_child",
    "JR": "junior",
}


def fetch_house_filing_index(
    year: int,
    storage: ClickHouseStorage,
    *,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], Any]:
    """Fetch, archive, and parse the annual House financial-disclosure index."""
    http = session or requests.Session()
    url = _INDEX_URL.format(year=year)
    response = http.get(url, timeout=60)
    raw_id = storage.archive_http_response(
        source="house_clerk_filing_index",
        source_url=url,
        response_body=response.content,
        status_code=response.status_code,
        response_headers=dict(response.headers),
        fetch_key=str(year),
        content_type=response.headers.get("Content-Type", "application/zip"),
        metadata={"filing_year": year},
    )
    response.raise_for_status()
    return parse_house_filing_index(response.content, year), raw_id


def parse_house_filing_index(payload: bytes, year: int) -> list[dict[str, Any]]:
    """Parse PTR filing rows from a House annual ZIP/XML payload."""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        xml_name = next(
            name for name in archive.namelist() if name.lower().endswith(".xml")
        )
        root = ElementTree.fromstring(archive.read(xml_name))

    filings = []
    for member in root.findall(".//Member"):
        values = {
            child.tag: (child.text or "").strip()
            for child in member
        }
        if values.get("FilingType") != "P":
            continue
        filing_year = int(values.get("Year") or year)
        filing_id = values["DocID"]
        filings.append(
            {
                "filing_id": filing_id,
                "filing_year": filing_year,
                "filing_type": "P",
                "filing_date": _parse_date(values["FilingDate"]),
                "filer_prefix": values.get("Prefix", ""),
                "filer_first_name": values.get("First", ""),
                "filer_last_name": values.get("Last", ""),
                "filer_suffix": values.get("Suffix", ""),
                "filer_name_raw": " ".join(
                    value
                    for value in (
                        values.get("Prefix", ""),
                        values.get("First", ""),
                        values.get("Last", ""),
                        values.get("Suffix", ""),
                    )
                    if value
                ),
                "state_district_raw": values.get("StateDst", ""),
                "filing_url": _PTR_URL.format(
                    year=filing_year,
                    filing_id=filing_id,
                ),
            }
        )
    return filings


def fetch_and_parse_ptr(
    filing: dict[str, Any],
    storage: ClickHouseStorage,
    *,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], Any]:
    """Fetch one PTR PDF, archive it, extract text, and parse transactions."""
    http = session or requests.Session()
    url = filing["filing_url"]
    response = http.get(url, timeout=60)
    raw_id = storage.archive_http_response(
        source="house_clerk_ptr_pdf",
        source_url=url,
        response_body=response.content,
        status_code=response.status_code,
        response_headers=dict(response.headers),
        fetch_key=filing["filing_id"],
        content_type=response.headers.get("Content-Type", "application/pdf"),
        metadata={
            "filing_id": filing["filing_id"],
            "filing_year": filing["filing_year"],
        },
    )
    response.raise_for_status()
    with pdfplumber.open(io.BytesIO(response.content)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return parse_house_ptr_text(text, filing), raw_id


def parse_house_ptr_text(
    text: str,
    filing: dict[str, Any],
) -> list[dict[str, Any]]:
    """Parse transaction rows anchored on type, dates, and amount."""
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in text.splitlines()
        if line.strip()
    ]
    trades = []
    seen_keys = set()
    for index, line in enumerate(lines):
        match = _TRANSACTION_CORE_PATTERN.search(line)
        if not match:
            continue

        asset_parts = [line[:match.start()].strip()]
        for continuation in lines[index + 1:index + 4]:
            if "\x00" in continuation:
                break
            if _TRANSACTION_CORE_PATTERN.search(continuation):
                break
            asset_parts.append(continuation)
            if _ASSET_TYPE_PATTERN.search(continuation):
                break

        asset_block = " ".join(part for part in asset_parts if part)
        asset_type_match = _ASSET_TYPE_PATTERN.search(asset_block)
        if not asset_type_match:
            continue
        ticker_match = (
            _PAREN_TICKER_PATTERN.search(asset_block)
            or _BARE_TICKER_PATTERN.search(asset_block)
        )
        ticker = ticker_match.group("ticker").upper() if ticker_match else None
        asset_name = _clean_asset_name(asset_block)

        owner_match = re.match(r"^(SP|JT|DC|JR)\s+", asset_name)
        owner_code = owner_match.group(1) if owner_match else ""
        if owner_match:
            asset_name = asset_name[owner_match.end():].strip()

        groups = match.groupdict()
        amount_min, amount_max = _parse_amount(groups["amount"])
        tx_type = _transaction_type(
            groups["tx_type"],
            groups.get("sale_scope"),
        )
        trade_key = hashlib.sha256(
            "|".join(
                (
                    filing["filing_id"],
                    groups["transaction_date"],
                    asset_name,
                    tx_type,
                    groups["amount"],
                )
            ).encode("utf-8")
        ).hexdigest()
        if trade_key in seen_keys:
            continue
        seen_keys.add(trade_key)
        trades.append(
            {
                "trade_key": trade_key,
                "owner_code": owner_code,
                "filer_type": _OWNER_TYPES.get(owner_code, "self"),
                "asset_name_raw": asset_name,
                "ticker": ticker,
                "asset_type_code": asset_type_match.group("asset_type").upper(),
                "transaction_type": tx_type,
                "transaction_date": _parse_date(groups["transaction_date"]),
                "notification_date": (
                    _parse_date(groups["notification_date"])
                    if groups.get("notification_date")
                    else None
                ),
                "amount_str": groups["amount"],
                "amount_min": amount_min,
                "amount_max": amount_max,
            }
        )
    return trades


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%m/%d/%Y").date()


def _parse_amount(value: str) -> tuple[int | None, int | None]:
    numbers = [
        int(number.replace(",", ""))
        for number in re.findall(r"\$([\d,]+)", value)
    ]
    if not numbers:
        return None, None
    if value.lower().startswith("over"):
        return numbers[0] + 1, None
    return numbers[0], numbers[1] if len(numbers) > 1 else None


def _transaction_type(code: str, sale_scope: str | None) -> str:
    if code.upper() == "P":
        return "purchase"
    if code.upper() == "E":
        return "exchange"
    return "sale_partial" if (sale_scope or "").lower() == "partial" else "sale_full"


def _clean_asset_name(asset_block: str) -> str:
    value = _PAREN_TICKER_PATTERN.sub("", asset_block)
    value = _BARE_TICKER_PATTERN.sub("", value)
    value = _ASSET_TYPE_PATTERN.sub("", value)
    return re.sub(r"\s+", " ", value).strip(" -")
