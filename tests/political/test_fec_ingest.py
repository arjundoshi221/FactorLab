"""Tests for FEC ingest — resolver, row mapping, FK filter.

Mix of unit (resolver, _to_donation_row, normalizer) + integration (DB FK
behavior). Integration tests are skipped if DATABASE_URL is unset.
"""

from __future__ import annotations

import os

import pytest

from factorlab.countries.us.political._resolver import Resolver, normalize_pac_name
from factorlab.countries.us.political.fec.ingest import _to_donation_row


# ===========================================================================
#  Pure unit — normalize_pac_name + resolver cascade (no DB)
# ===========================================================================

class TestNormalizePacName:
    def test_strips_pac_vocabulary(self):
        assert normalize_pac_name(
            "MICROSOFT CORPORATION STAKEHOLDERS VOLUNTARY PAC - MSVPAC"
        ) == "microsoft"

    def test_strips_parenthetical_acronym(self):
        assert normalize_pac_name(
            "LOCKHEED MARTIN CORPORATION EMPLOYEES POLITICAL ACTION COMMITTEE (LMPAC)"
        ) == "lockheed martin"

    def test_preserves_pacific_in_company_name(self):
        # Regression: \w*pac\w*\b used to match 'pacific' too
        norm = normalize_pac_name("UNION PACIFIC CORP. FUND FOR EFFECTIVE GOVERNMENT")
        assert "pacific" in norm
        assert "union" in norm

    def test_preserves_impact(self):
        assert "impact" in normalize_pac_name("IMPACT BANCORP PAC")

    def test_strips_word_ending_in_pac(self):
        # FEDPAC, MSVPAC, AAPAC all should drop
        assert normalize_pac_name("X FEDPAC INC PAC") in ("x", "")

    def test_empty_input(self):
        assert normalize_pac_name("") == ""
        assert normalize_pac_name(None) == ""    # type: ignore[arg-type]


@pytest.fixture(scope="module")
def resolver():
    return Resolver()


class TestResolverPacNameMatches:
    """High-priority public-co PACs MUST resolve. Wrong > miss → also assert
    the genuinely-absent companies don't resolve."""

    @pytest.mark.parametrize("pac_name,expected_ticker", [
        ("MICROSOFT CORPORATION STAKEHOLDERS VOLUNTARY PAC - MSVPAC", "MSFT"),
        ("APPLE INC POLITICAL ACTION COMMITTEE", "AAPL"),
        ("BROADCOM CORPORATION PAC", "AVGO"),
        ("NVIDIA CORPORATION POLITICAL ACTION COMMITTEE", "NVDA"),
        ("PFIZER INC. PAC", "PFE"),
        ("BOEING COMPANY POLITICAL ACTION COMMITTEE", "BA"),
        ("LOCKHEED MARTIN CORPORATION EMPLOYEES POLITICAL ACTION COMMITTEE (LMPAC)", "LMT"),
        ("CHEVRON EMPLOYEES POLITICAL ACTION COMMITTEE - CHEVRON CORPORATION", "CVX"),
        ("CATERPILLAR INC. POLITICAL ACTION COMMITTEE", "CAT"),
        ("BLACKROCK FUNDS SERVICES GROUP LLC POLITICAL ACTION COMMITTEE", "BLK"),
        ("METLIFE INC. EMPLOYEES POLITICAL PARTICIPATION FUND A", "MET"),
        ("DEERE & COMPANY POLITICAL ACTION COMMITTEE - ILLINOIS", "DE"),
        ("UNION PACIFIC CORP. FUND FOR EFFECTIVE GOVERNMENT", "UNP"),
        ("SALESFORCE, INC VOLUNTARY POLITICAL PARTICIPATION NETWORK", "CRM"),
    ])
    def test_high_priority_matches(self, resolver, pac_name, expected_ticker):
        ticker, kind, conf = resolver.resolve_pac_name(pac_name)
        assert ticker == expected_ticker, (
            f"{pac_name!r}: expected {expected_ticker}, got {ticker!r} (kind={kind}, conf={conf})"
        )
        assert conf >= 0.85

    @pytest.mark.parametrize("pac_name", [
        "FIRST INTERSTATE TEXAS LEADERSHIP FUNDS/FEDERAL",      # was FIBK false positive
        "U.S. VENTURE INC. PAC",                                # was USAU FP
        "UNITED STATES SURGICAL CORPORATION HEALTH PAC",        # was UGA FP
        "AMERICAN NATIONAL INSURANCE COMPANY EMPLOYEE POLITICAL",  # was ANG-PD FP
        "NORTH AMERICAN VAN LINES INC PAC (NAPAC)/NORFOLK SOUTHERN",
        "ROCKET CITY PAC",
        "NEW JERSEY RIGHT TO LIFE COMMITTEE FEDERAL PAC",
        "LONG ISLAND PROGRESSIVE COALITION POLITICAL ACTION COMMITTEE",  # not PGR
    ])
    def test_false_positives_rejected(self, resolver, pac_name):
        """Conservative cascade returns None instead of guessing."""
        ticker, kind, conf = resolver.resolve_pac_name(pac_name)
        assert ticker is None, (
            f"{pac_name!r}: should not match, got {ticker!r} (kind={kind}, conf={conf})"
        )


# ===========================================================================
#  _to_donation_row — FK soft-NULL on candidate_id
# ===========================================================================

class TestToDonationRow:
    def _row_with_candidate_id(self, cand_id: str | None) -> dict:
        return {
            "sub_id": "1234567890",
            "two_year_transaction_period": 2024,
            "contributor_name": "TEST DONOR",
            "contribution_receipt_amount": 1000,
            "contribution_receipt_date": "2024-06-15",
            "committee_id": "C00100000",
            "committee": {"name": "RECIPIENT COMMITTEE"},
            "candidate_id": cand_id,
            "candidate_name": "TEST CANDIDATE",
        }

    def test_soft_null_unknown_candidate_id(self):
        """FEC's Schedule A occasionally returns committee-shaped IDs in
        candidate_id. The filter must NULL them out to avoid FK violations."""
        valid = {"H8CA05035", "S2WA00229"}
        row = _to_donation_row(self._row_with_candidate_id("C00484535"), valid)
        assert row["candidate_id"] is None
        assert row["candidate_name"] is None    # both nulled together

    def test_passthrough_known_candidate_id(self):
        valid = {"H8CA05035", "S2WA00229"}
        row = _to_donation_row(self._row_with_candidate_id("H8CA05035"), valid)
        assert row["candidate_id"] == "H8CA05035"
        assert row["candidate_name"] == "TEST CANDIDATE"

    def test_no_filter_when_set_omitted(self):
        """Backwards compat: passing None preserves prior behavior (no filter)."""
        row = _to_donation_row(self._row_with_candidate_id("C00484535"), None)
        assert row["candidate_id"] == "C00484535"   # passed through (will fail FK at DB)

    def test_skip_row_with_no_sub_id(self):
        raw = self._row_with_candidate_id(None)
        raw["sub_id"] = ""
        assert _to_donation_row(raw, set()) is None


# ===========================================================================
#  Integration — DB FK + state of cataloged committees
# ===========================================================================

pytestmark_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set — skipping DB-backed FEC tests",
)


@pytest.fixture(scope="module")
def engine():
    from factorlab.storage.db import get_engine
    return get_engine()


@pytest.fixture(scope="module")
def valid_candidate_ids(engine) -> set[str]:
    from sqlalchemy import text
    with engine.connect() as c:
        return {r[0] for r in c.execute(text(
            "SELECT fec_candidate_id FROM alt_political_us.legislator_fec_ids"
        ))}


@pytestmark_db
class TestFecDb:
    def test_legislator_fec_ids_populated(self, valid_candidate_ids):
        """Stage-1 ingest must have populated the candidate-id catalog."""
        assert len(valid_candidate_ids) > 1500, (
            f"Expected ≥1500 fec candidate ids; got {len(valid_candidate_ids)}"
        )

    def test_no_donations_with_orphan_candidate_id(self, engine, valid_candidate_ids):
        """FK-filter invariant: every non-NULL campaign_donations.candidate_id
        must exist in legislator_fec_ids."""
        from sqlalchemy import text
        with engine.connect() as c:
            orphans = c.execute(text("""
                SELECT count(*) FROM alt_political_us.campaign_donations cd
                 WHERE cd.candidate_id IS NOT NULL
                   AND NOT EXISTS (
                       SELECT 1 FROM alt_political_us.legislator_fec_ids l
                        WHERE l.fec_candidate_id = cd.candidate_id
                   )
            """)).scalar()
        assert orphans == 0, f"Found {orphans} donation rows with orphan candidate_id"

    def test_no_donations_with_orphan_donor_committee(self, engine):
        """donor_committee_id FK invariant — set by stub-or-rich path."""
        from sqlalchemy import text
        with engine.connect() as c:
            orphans = c.execute(text("""
                SELECT count(*) FROM alt_political_us.campaign_donations cd
                 WHERE cd.donor_committee_id IS NOT NULL
                   AND NOT EXISTS (
                       SELECT 1 FROM alt_political_us.fec_committees fc
                        WHERE fc.country_code = cd.donor_committee_country
                          AND fc.committee_id = cd.donor_committee_id
                   )
            """)).scalar()
        assert orphans == 0, f"Found {orphans} donations with orphan donor_committee_id"

    def test_corp_pacs_have_resolved_subset(self, engine):
        """Resolver coverage: at least 500 of the corp-PAC universe should
        resolve to a ticker (post-tuning baseline ~590)."""
        from sqlalchemy import text
        with engine.connect() as c:
            resolved = c.execute(text("""
                SELECT count(*) FROM alt_political_us.fec_committees
                 WHERE country_code='US' AND organization_type='C'
                   AND sponsor_company_ticker IS NOT NULL
            """)).scalar()
        assert resolved >= 500, (
            f"Resolved corp PACs dropped below baseline: {resolved}"
        )
