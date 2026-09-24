from scripts.resolve_clickhouse_v2_candidates import (
    candidate_fingerprint,
    build_plan,
    canonical_ids,
    contract_id,
    committee_id,
    legislator_id,
    political_trade_id,
)


def test_candidate_ids_are_deterministic_and_do_not_reuse_legacy_ids():
    instrument = {
        "instrument_id": "65b39d93-0b3f-5a14-a4be-e12bf09acc01",
        "instrument_key": "NSE_EQ|INE001B01026",
        "isin": "INE001B01026",
    }

    first = canonical_ids(instrument)
    second = canonical_ids(instrument)

    assert first == second
    assert len(set(first)) == 3
    assert all(str(value) != instrument["instrument_id"] for value in first)


def test_distinct_listing_keys_share_security_only_when_isin_matches():
    first = canonical_ids({"instrument_key": "A", "isin": "US0000000001"})
    second = canonical_ids({"instrument_key": "B", "isin": "US0000000001"})

    assert first[:2] == second[:2]
    assert first[2] != second[2]


def test_contract_and_legislator_ids_are_stable():
    assert contract_id("NSE_FO|48706") == contract_id("NSE_FO|48706")
    assert legislator_id("A000055") == legislator_id("A000055")
    assert committee_id("HSAP") == committee_id("HSAP")
    assert political_trade_id("f" * 64) == political_trade_id("f" * 64)
    assert committee_id("HSAP") != political_trade_id("f" * 64)


def test_plan_marks_every_mapping_as_unapproved():
    source = {
        "countries": [{}, {}],
        "exchanges": [{"currency_code": b"INR"}, {"currency_code": b"USD"}, {"currency_code": b"USD"}],
        "instruments": [{}, {}, {}],
        "contracts": [{}, {}],
        "legislators": [{}],
        "committees": [{}],
        "political_trades": [{}, {}],
    }

    plan = build_plan(source)

    assert plan["currencies"] == 2
    assert plan["unapproved_crosswalks"] == 9
    assert plan["unapproved_reference_enrichments"] == 5


class FingerprintClient:
    class Result:
        def __init__(self, rows):
            self.result_rows = rows

    def __init__(self, reverse=False):
        self.reverse = reverse

    def query(self, query):
        if "migration_id_crosswalk" in query:
            rows = [
                ("factorlab", "ref_instruments", "key-a", "a" * 64, "listing", "00000000-0000-0000-0000-000000000001"),
                ("factorlab", "ref_contracts", "key-b", "b" * 64, "contract", "00000000-0000-0000-0000-000000000002"),
            ]
            return self.Result(list(reversed(rows)) if self.reverse else rows)
        return self.Result(
            [("factorlab", "ref_exchanges", "NSE", "c" * 64, "NSE", "XNSE", "Asia/Kolkata", "09:15", "15:30", "INR")]
        )


def test_candidate_fingerprint_changes_with_order_from_database_contract():
    first = candidate_fingerprint(FingerprintClient())
    second = candidate_fingerprint(FingerprintClient())

    assert len(first) == 64
    assert first == second
