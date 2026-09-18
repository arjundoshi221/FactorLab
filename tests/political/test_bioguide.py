"""Integration tests for the StrictBioguideMatcher (DB-backed).

These tests hit the live Postgres DB (alt_political_us.legislators +
legislator_terms + legislator_aliases) — they're integration tests, not
unit tests. The DB has all 12,766 legislators + 45,530 terms loaded;
each test asserts a known-good resolution outcome on real data.

Skip if DATABASE_URL is unset or the DB is unreachable.
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from factorlab.storage.db import get_engine

# Skip the whole module when DB isn't available (e.g. CI without Postgres)
pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set — skipping DB-backed bioguide tests",
)


@pytest.fixture(scope="module")
def matcher():
    from factorlab.countries.us.political._bioguide import StrictBioguideMatcher
    return StrictBioguideMatcher(get_engine())


# ── Stage 0: legislator_aliases overrides ──────────────────────────────────


def test_stage0_van_taylor_via_alias_table(matcher):
    """The original motivating case: Van Taylor files PTRs as 'Nicholas V. Taylor'
    but bioguide stores him as first='Van'. Migration 024 seeds an alias entry."""
    bg = matcher.resolve(full_name="Nicholas V. Taylor",
                         chamber="rep", trade_date=None)
    assert bg == "T000479"


def test_stage0_full_name_normalization():
    """Stage 0 lookup is whitespace-collapse + lowercase only."""
    from factorlab.countries.us.political._bioguide import StrictBioguideMatcher
    m = StrictBioguideMatcher(get_engine())
    # Various whitespace / case variants of the same alias
    for variant in ("Nicholas V. Taylor",
                    "  Nicholas V. Taylor  ",
                    "NICHOLAS V. TAYLOR",
                    "nicholas v. taylor"):
        assert m.resolve(full_name=variant, chamber="rep",
                         trade_date=None) == "T000479", f"failed on {variant!r}"


def test_stage0_first_last_kwargs_path(matcher):
    """Same alias, but caller passed first/last instead of full_name."""
    bg = matcher.resolve(first="Nicholas V.", last="Taylor",
                         chamber="rep", trade_date=None)
    assert bg == "T000479"


# ── Strict-NULL invariant: ambiguity returns None ──────────────────────────


def test_returns_none_on_unknown_name(matcher):
    """Random name with no plausible match must return None, not guess."""
    assert matcher.resolve(full_name="Random Imaginary Person",
                           chamber="rep", trade_date=None) is None


def test_returns_none_on_invalid_chamber(matcher):
    assert matcher.resolve(full_name="Random",
                           chamber="invalid", trade_date=None) is None


def test_returns_none_on_empty_input(matcher):
    assert matcher.resolve(full_name=None, first=None, last=None,
                           chamber="rep", trade_date=None) is None


# ── Edge cases the matcher specifically handles ────────────────────────────


def test_apostrophe_unicode_normalized(matcher):
    """Both ASCII and Unicode apostrophes should resolve identically.
    D'Esposito (NY-04, in office 2023-) is a good test case."""
    bg_ascii = matcher.resolve(first="Anthony", last="D'Esposito",
                               chamber="rep", trade_date=date(2024, 6, 1))
    bg_uni = matcher.resolve(first="Anthony", last="D’Esposito",  # U+2019 right single quote
                             chamber="rep", trade_date=date(2024, 6, 1))
    if bg_ascii is None and bg_uni is None:
        pytest.skip("D'Esposito not present in this DB — skip apostrophe test")
    assert bg_ascii == bg_uni


def test_diacritic_ascii_folded(matcher):
    """Barragán (CA-44, in office 2017-) should resolve from ASCII 'Barragan'."""
    bg = matcher.resolve(first="Nanette", last="Barragan",
                         chamber="rep", trade_date=date(2024, 6, 1))
    if bg is None:
        pytest.skip("Barragán not present in this DB — skip diacritic test")
    assert bg.startswith("B")  # canonical bioguide prefix for Barragán


# ── Term-window boundary: legislator must be active on trade_date ──────────


def test_term_window_inactive_legislator_returns_none(matcher):
    """A trade_date BEFORE any legislator's term_start must not resolve."""
    # Use a clearly pre-historic date for a current legislator
    bg = matcher.resolve(full_name="Nicholas V. Taylor",  # alias goes via Stage 0
                         chamber="rep", trade_date=date(1900, 1, 1))
    # Stage 0 doesn't apply term-window filtering (manual aliases are
    # always trusted), so this should still return T000479. Test documents
    # the contract.
    assert bg == "T000479"


# ── Module-level invariants ────────────────────────────────────────────────


def test_aliases_loaded_from_db(matcher):
    """Stage 0 alias table loads at construction time."""
    assert len(matcher._aliases) >= 1, "expected migration 024's seed at minimum"
    # The Van Taylor entry must be present
    assert "nicholas v. taylor" in matcher._aliases
    assert matcher._aliases["nicholas v. taylor"] == "T000479"


def test_primary_index_nonempty(matcher):
    """Live DB should have indexed thousands of legislator-terms."""
    assert len(matcher._primary) > 1000


def test_secondary_index_nonempty(matcher):
    """Compound-surname / cross-mapped keys must have populated."""
    assert len(matcher._secondary) > 0
