"""Tests for Congress.gov ingest — committee_id normalization, deterministic
action_id, FK filter behavior, DB-state invariants.
"""

from __future__ import annotations

import os
import uuid

import pytest

from factorlab.countries.us.political.congress_gov.ingest import (
    NAMESPACE_BILL_ACTIONS,
    _action_row,
    _normalize_committee_id,
    _to_hearing_row,
)


# ===========================================================================
#  Pure unit
# ===========================================================================

class TestNormalizeCommitteeId:
    def test_strips_trailing_00_for_full_committee(self):
        # Congress.gov: 'hswm00' (House Ways & Means full committee)
        # → matches our 4-char Thomas IDs ('HSWM').
        assert _normalize_committee_id("hswm00") == "HSWM"
        assert _normalize_committee_id("HSWM00") == "HSWM"

    def test_keeps_subcommittee_suffix(self):
        assert _normalize_committee_id("hswm05") == "HWSM05".replace("HWSM", "HSWM")  # → HSWM05
        assert _normalize_committee_id("hssy11") == "HSSY11"

    def test_uppercase_no_suffix(self):
        # 4-char codes with no '00' (rare in Congress.gov but handle it)
        assert _normalize_committee_id("hssy") == "HSSY"

    def test_none_and_empty(self):
        assert _normalize_committee_id(None) is None
        assert _normalize_committee_id("") is None
        assert _normalize_committee_id("   ") is None


class TestActionRowDeterministic:
    """A1 fix verification — same input MUST produce same UUID across calls."""

    def _action(self, **overrides):
        base = {
            "actionDate": "2025-05-20",
            "text": "Referred to the House Committee on Energy.",
            "type": "IntroReferral",
            "sourceSystem": {"name": "House", "code": 9},
        }
        base.update(overrides)
        return base

    def test_same_input_yields_same_action_id(self):
        a = self._action()
        row1 = _action_row("US-119-HR-100", a)
        row2 = _action_row("US-119-HR-100", a)
        assert row1["action_id"] == row2["action_id"]
        # Verify it's actually a UUIDv5 (deterministic), not a v4 (random)
        # UUID version is the upper 4 bits of the time_hi_and_version field
        assert row1["action_id"].version == 5

    def test_different_text_yields_different_action_id(self):
        a1 = self._action(text="Action one.")
        a2 = self._action(text="Action two.")
        row1 = _action_row("US-119-HR-100", a1)
        row2 = _action_row("US-119-HR-100", a2)
        assert row1["action_id"] != row2["action_id"]

    def test_different_bill_uid_yields_different_action_id(self):
        a = self._action()
        row1 = _action_row("US-119-HR-100", a)
        row2 = _action_row("US-119-HR-101", a)
        assert row1["action_id"] != row2["action_id"]

    def test_action_id_in_namespace(self):
        """Sanity: deriving action_id manually using NAMESPACE_BILL_ACTIONS
        produces the same UUID as the helper."""
        a = self._action()
        row = _action_row("US-119-HR-100", a)
        expected = uuid.uuid5(
            NAMESPACE_BILL_ACTIONS,
            f"US-119-HR-100|{a['actionDate']}|{a['text'][:500]}",
        )
        assert row["action_id"] == expected

    def test_returns_none_on_missing_required_fields(self):
        assert _action_row("US-119-HR-100", {"actionDate": "2025-01-01", "text": ""}) is None
        assert _action_row("US-119-HR-100", {"text": "x"}) is None  # no date


class TestToHearingRow:
    def _hearing(self, **overrides):
        base = {
            "jacketNumber": 58978,
            "congress": 119,
            "chamber": "House",
            "title": "Test hearing",
            "dates": [{"date": "2025-03-05"}],
            "committees": [{"name": "Sci", "systemCode": "hssy00"}],
            "formats": [
                {"type": "Formatted Text", "url": "https://x.htm"},
                {"type": "PDF", "url": "https://x.pdf"},
            ],
            "citation": "H.Hrg.119",
            "updateDate": "2026-05-01T01:21:26Z",
        }
        base.update(overrides)
        return base

    def test_committee_id_normalized(self):
        row = _to_hearing_row(self._hearing())
        assert row["committee_id"] == "HSSY"

    def test_prefers_pdf_url(self):
        row = _to_hearing_row(self._hearing())
        assert row["url"] == "https://x.pdf"

    def test_returns_none_on_missing_required(self):
        assert _to_hearing_row({"jacketNumber": None}) is None
        assert _to_hearing_row({"jacketNumber": 1}) is None        # no chamber/congress


# ===========================================================================
#  Integration — DB invariants
# ===========================================================================

pytestmark_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set — skipping DB-backed Congress.gov tests",
)


@pytest.fixture(scope="module")
def engine():
    from factorlab.storage.db import get_engine
    return get_engine()


@pytestmark_db
class TestCongressDb:
    def test_no_bill_actions_duplicates(self, engine):
        """A1 invariant: bill_actions natural key (bill_uid, action_date,
        action_text) must be unique post-fix."""
        from sqlalchemy import text
        with engine.connect() as c:
            dupes = c.execute(text("""
                SELECT COUNT(*) FROM (
                    SELECT bill_uid, action_date, left(action_text, 500), count(*)
                      FROM alt_political_us.bill_actions
                     GROUP BY 1, 2, 3 HAVING count(*) > 1
                ) t
            """)).scalar()
        assert dupes == 0, f"Found {dupes} duplicate (bill_uid, date, text) groups"

    def test_no_bill_committees_fk_orphans(self, engine):
        """Pass C FK filter must keep bill_committees clean."""
        from sqlalchemy import text
        with engine.connect() as c:
            orphans = c.execute(text("""
                SELECT count(*)
                  FROM alt_political_us.bill_committees bc
                  LEFT JOIN alt_political_us.committees c
                    ON bc.country_code = c.country_code
                   AND bc.committee_id = c.committee_id
                 WHERE c.committee_id IS NULL
            """)).scalar()
        assert orphans == 0

    def test_no_bill_sponsors_fk_orphans(self, engine):
        """Every bill_sponsors row must reference a real bill + legislator."""
        from sqlalchemy import text
        with engine.connect() as c:
            orphans = c.execute(text("""
                SELECT count(*) FROM alt_political_us.bill_sponsors bs
                 WHERE NOT EXISTS (SELECT 1 FROM alt_political_us.bills b
                                     WHERE b.bill_uid = bs.bill_uid)
                    OR NOT EXISTS (SELECT 1 FROM alt_political_us.legislators l
                                     WHERE l.bioguide_id = bs.bioguide_id)
            """)).scalar()
        assert orphans == 0

    def test_active_congress_skeleton_loaded(self, engine):
        """Pass A must have populated the active congress (119 for 2025-26)."""
        from sqlalchemy import text
        with engine.connect() as c:
            n = c.execute(text(
                "SELECT count(*) FROM alt_political_us.bills WHERE congress=119"
            )).scalar()
        assert n > 10000, f"Expected >10K bills for 119th congress; got {n}"
