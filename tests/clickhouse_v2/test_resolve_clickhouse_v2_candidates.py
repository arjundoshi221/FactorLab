from scripts.resolve_clickhouse_v2_candidates import (
    build_plan,
    canonical_ids,
    contract_id,
    legislator_id,
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


def test_plan_marks_every_mapping_as_unapproved():
    source = {
        "countries": [{}, {}],
        "exchanges": [{"currency_code": b"INR"}, {"currency_code": b"USD"}, {"currency_code": b"USD"}],
        "instruments": [{}, {}, {}],
        "contracts": [{}, {}],
        "legislators": [{}],
    }

    plan = build_plan(source)

    assert plan["currencies"] == 2
    assert plan["unapproved_crosswalks"] == 6
    assert plan["unapproved_reference_enrichments"] == 5
