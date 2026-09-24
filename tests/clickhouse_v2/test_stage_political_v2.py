import hashlib
from datetime import UTC, date, datetime
from gzip import compress
from types import SimpleNamespace
from uuid import UUID

import pytest
from scripts.stage_clickhouse_v2_political import (
    AMOUNT_BUCKET_IDS,
    REVIEWED_PDF_MISMATCHES,
    _archive_rows,
    build_candidates,
    exact_pdf_match,
    pdf_match_diagnostics,
    resolve_legislator,
    reviewed_pdf_match,
    supported_pdf_match,
)


def _legacy_trade():
    return {
        "raw_id": UUID("00000000-0000-0000-0000-000000000001"),
        "trade_key": b"a" * 64,
        "source_hash": "b" * 64,
        "transaction_date": date(2026, 8, 11),
        "transaction_type": "sale_full",
        "ticker": "ACN",
        "asset_name_raw": "Accenture plc Class A Ordinary Shares",
        "amount_str": "$1,001 - $15,000",
        "legislator_name": "Example Legislator",
        "bioguide_id": "E000001",
    }


def _parsed_trade():
    return {
        "tx_date": "08/11/2026",
        "tx_type": "S",
        "ticker": "ACN",
        "asset_name_raw": "Accenture plc Class A Ordinary Shares",
        "amount_str": "$1,001 - $15,000",
        "amount_min": 1001,
        "amount_max": 15000,
        "asset_type_code": "ST",
    }


def test_exact_pdf_match_requires_unique_full_transaction():
    source = _legacy_trade()
    parsed = _parsed_trade()

    assert exact_pdf_match(source, [parsed]) == parsed
    assert exact_pdf_match(source, [parsed, parsed]) is None
    assert exact_pdf_match(source, [{**parsed, "amount_str": "$15,001 - $50,000"}]) is None
    assert exact_pdf_match(source, [{**parsed, "asset_name_raw": "Other issuer"}]) is None
    assert pdf_match_diagnostics(source, [parsed, parsed])[1] == "duplicate_exact_pdf_rows"
    assert pdf_match_diagnostics(source, [{**parsed, "ticker": "OTHER"}])[1] == "ticker_mismatch"
    assert pdf_match_diagnostics(source, [{**parsed, "asset_name_raw": "Other issuer"}])[1] == "asset_name_mismatch"
    assert exact_pdf_match({**source, "transaction_type": "sale_partial"}, [parsed]) == parsed
    assert supported_pdf_match(source, [parsed, parsed]) == (parsed, 2)
    assert supported_pdf_match(
        source, [parsed, {**parsed, "asset_type_code": "OP"}]
    ) is None
    suffix_source = {**source, "asset_name_raw": "Agilent Common Stock", "ticker": "A"}
    suffix_pdf = {**parsed, "asset_name_raw": "Agilent Common Stock (A)", "ticker": None}
    assert supported_pdf_match(suffix_source, [suffix_pdf]) == (suffix_pdf, 1)
    assert supported_pdf_match({**suffix_source, "asset_name_raw": ""}, [suffix_pdf]) is None


def test_candidates_are_unapproved_and_skip_ambiguous_trades(monkeypatch):
    source = _legacy_trade()
    parsed = _parsed_trade()
    monkeypatch.setattr(
        "scripts.stage_clickhouse_v2_political.parse_archive", lambda _: [parsed]
    )
    raw_id = str(source["raw_id"])
    entity = UUID("00000000-0000-0000-0000-000000000002")
    now = datetime(2026, 9, 23, tzinfo=UTC)
    filings = [{
        "filing_id": "filing-1", "raw_id": source["raw_id"],
        "source_hash": "c" * 64, "filer_name_raw": "Example Legislator",
        "bioguide_id": "E000001",
    }]

    rows, counts = build_candidates(
        filings, [source], {raw_id: (b"%PDF", "d" * 64)},
        {"E000001": entity}, "factorlab", now, 123,
    )

    assert counts["filing_candidates"] == 1
    assert counts["trade_candidates"] == 1
    assert all(row[18] == "" and row[19] is None for row in rows)
    trade = rows[1]
    assert trade[4] == AMOUNT_BUCKET_IDS[parsed["amount_str"]]
    assert trade[5:7] == [1001, 15000]
    assert trade[11] == entity
    assert trade[12:16] == [None, None, None, None]
    assert trade[17] == "unresolved"

    monkeypatch.setattr(
        "scripts.stage_clickhouse_v2_political.parse_archive", lambda _: [parsed, parsed]
    )
    _, counts = build_candidates(
        [], [source], {raw_id: (b"%PDF", "d" * 64)},
        {}, "factorlab", now, 123,
    )
    assert counts["trade_candidates"] == 1
    assert counts["trade_candidates_with_identical_pdf_duplicates"] == 1


def test_archived_pdfs_require_matching_hash_and_unique_raw_id():
    body = b"%PDF-1.4 example"
    raw_id = UUID("00000000-0000-0000-0000-000000000001")

    class Client:
        def __init__(self, rows):
            self.rows = rows

        def query(self, _):
            return SimpleNamespace(result_rows=self.rows)

    valid = (raw_id, compress(body).hex(), hashlib.sha256(body).hexdigest())
    assert _archive_rows(Client([valid]), "factorlab")[str(raw_id)][1] == valid[2]
    with pytest.raises(ValueError, match="hash mismatch"):
        _archive_rows(Client([(raw_id, compress(body).hex(), "0" * 64)]), "factorlab")
    with pytest.raises(ValueError, match="duplicate"):
        _archive_rows(Client([valid, valid]), "factorlab")


def test_official_house_aliases_do_not_fuzzy_match():
    entity = UUID("00000000-0000-0000-0000-000000000003")
    row = {
        "bioguide_id": None,
        "legislator_name": "Hon. Richard Dean Dr McCormick",
        "state": b"GA",
    }
    resolved = resolve_legislator(row, "legislator_name", {"M001218": entity})
    assert resolved[:4] == ("M001218", entity, "high", "Richard McCormick")
    assert "historical_district_source" in resolved[4]
    assert resolve_legislator(
        {**row, "state": b"PA"}, "legislator_name", {"M001218": entity}
    )[0] is None


@pytest.mark.parametrize("trade_key", REVIEWED_PDF_MISMATCHES)
def test_reviewed_parser_mismatch_requires_exact_legacy_and_unique_pdf(trade_key):
    expected = REVIEWED_PDF_MISMATCHES[trade_key]
    month, day, year = map(int, expected["date"].split("/"))
    legacy = {
        "trade_key": trade_key,
        "raw_id": expected["raw_id"],
        "ticker": expected["legacy_ticker"],
        "asset_name_raw": expected["legacy_asset"],
        "amount_str": expected["amount"],
        "transaction_date": date(year, month, day),
        "transaction_type": next(iter({"P": {"purchase"}, "S": {"sale_full"}}[expected["type"]])),
    }
    parsed = {
        "tx_date": expected["date"], "tx_type": expected["type"],
        "amount_str": expected["amount"], "ticker": expected["pdf_ticker"],
        "asset_name_raw": expected["pdf_asset"],
    }
    sha = expected.get("pdf_sha256")
    assert reviewed_pdf_match(legacy, [parsed], archive_sha256=sha) == parsed
    assert reviewed_pdf_match(legacy, [parsed, parsed], archive_sha256=sha) is None
    assert reviewed_pdf_match({**legacy, "amount_str": "changed"}, [parsed], archive_sha256=sha) is None
    assert reviewed_pdf_match(legacy, [{**parsed, "asset_name_raw": "changed"}], archive_sha256=sha) is None
    if sha:
        assert reviewed_pdf_match(legacy, [parsed], archive_sha256="wrong") is None
