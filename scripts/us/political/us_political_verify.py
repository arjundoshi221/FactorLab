"""Integrity battery for alt_political_us schema.

Run after every significant backfill. Reports:
  1. Row counts per table
  2. Source breakdown for legislator_trades + gov_contracts
  3. NULL-bioguide attribution rate per source
  4. Top 15 attributed legislators (sanity check — no historical ghosts)
  5. FK resolution rates (bioguide_id, ticker, security_id)
  6. Cross-source contract overlap (USASpending vs Finnhub for sample tickers)
  7. Flagship conjunction query: relevant-committee × has-contracts × recent-purchase
  8. Top 10 most-recent trades joined to all conjunction layers
  9. raw_archive coverage

Run:
    python scripts/us/political/us_political_verify.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Path layout: scripts/us/political/<file>.py (multi-vendor orchestrator)
#   parents[0]=political, [1]=us, [2]=scripts, [3]=repo root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
assert (PROJECT_ROOT / "pyproject.toml").exists(), (
    f"PROJECT_ROOT misresolved: {PROJECT_ROOT}"
)
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sqlalchemy import text  # noqa: E402

from factorlab.storage.db import get_engine  # noqa: E402

log = logging.getLogger(__name__)


HEADER = "\n" + "=" * 78 + "\n  {}\n" + "=" * 78


def _hdr(s: str) -> None:
    print(HEADER.format(s))


def _row_counts(c) -> None:
    _hdr("1. Row counts per table")
    # Whitelist of valid identifier characters as a defense in depth even though
    # the source (information_schema) is trusted. Anything outside [a-z0-9_] is
    # rejected so a hostile DBA-controlled table name like 'x;DROP TABLE…' can't
    # slip into our SQL.
    import re
    _IDENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
    for row in c.execute(text("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'alt_political_us' ORDER BY table_name
    """)):
        if not _IDENT_RE.match(row.table_name):
            print(f"  SKIP unsafe identifier: {row.table_name!r}")
            continue
        # Identifier is whitelisted; quote it for SQL using "..."
        n = c.execute(text(
            f'SELECT COUNT(*) FROM "alt_political_us"."{row.table_name}"'
        )).scalar()
        print(f"  {row.table_name:40s} {n:>10,}")


def _source_breakdown(c) -> None:
    _hdr("2. Source breakdown (via endpoint -> vendor JOIN chain)")
    print("  legislator_trades:")
    for row in c.execute(text("""
        SELECT e.code AS source, COUNT(*) AS n,
               MIN(transaction_date) AS first_dt,
               MAX(transaction_date) AS last_dt
        FROM alt_political_us.legislator_trades lt
        JOIN ref.data_endpoints e ON e.id = lt.endpoint_id
        GROUP BY e.code ORDER BY e.code
    """)):
        print(f"    {row.source:35s} n={row.n:>6,}  {row.first_dt}..{row.last_dt}")
    print("  gov_contracts:")
    for row in c.execute(text("""
        SELECT e.code AS source, COUNT(*) AS n,
               MIN(action_date) AS first_dt, MAX(action_date) AS last_dt
        FROM alt_political_us.gov_contracts gc
        JOIN ref.data_endpoints e ON e.id = gc.endpoint_id
        GROUP BY e.code ORDER BY e.code
    """)):
        print(f"    {row.source:35s} n={row.n:>6,}  {row.first_dt}..{row.last_dt}")


def _bioguide_rate(c) -> None:
    _hdr("3. Bioguide resolution per source")
    for row in c.execute(text("""
        SELECT e.code AS source, COUNT(*) AS total,
               COUNT(bioguide_id) AS resolved,
               ROUND(100.0 * COUNT(bioguide_id) / NULLIF(COUNT(*),0), 1) AS pct
        FROM alt_political_us.legislator_trades lt
        JOIN ref.data_endpoints e ON e.id = lt.endpoint_id
        GROUP BY e.code ORDER BY e.code
    """)):
        print(f"  {row.source:35s} total={row.total:>6,}  resolved={row.resolved:>6,}  ({row.pct}%)")


def _top_traders(c) -> None:
    _hdr("4. Top 15 attributed legislators (sanity check)")
    for row in c.execute(text("""
        SELECT lt.bioguide_id,
               COALESCE(l.first_name || ' ' || l.last_name, '?') AS name,
               COUNT(*) AS n,
               MIN(transaction_date) AS first_dt,
               MAX(transaction_date) AS last_dt
        FROM alt_political_us.legislator_trades lt
        LEFT JOIN alt_political_us.legislators l USING (bioguide_id)
        WHERE lt.bioguide_id IS NOT NULL
        GROUP BY lt.bioguide_id, l.first_name, l.last_name
        ORDER BY n DESC LIMIT 15
    """)):
        print(f"  {row.bioguide_id}  {row.name:30s}  n={row.n:>5,}  {row.first_dt}..{row.last_dt}")


def _fk_resolution(c) -> None:
    _hdr("5. FK + ticker resolution rates")
    n_trades, n_with_ticker, n_with_security = c.execute(text("""
        SELECT COUNT(*),
               COUNT(ticker),
               COUNT(security_id)
        FROM alt_political_us.legislator_trades
    """)).one()
    print(f"  legislator_trades   total={n_trades:>6,}  ticker={n_with_ticker:>6,}  security_id={n_with_security:>6,}")

    n_contracts, n_with_ticker, n_with_security = c.execute(text("""
        SELECT COUNT(*), COUNT(ticker), COUNT(security_id)
        FROM alt_political_us.gov_contracts
    """)).one()
    print(f"  gov_contracts       total={n_contracts:>6,}  ticker={n_with_ticker:>6,}  security_id={n_with_security:>6,}")

    n_filings, n_with_ticker = c.execute(text("""
        SELECT COUNT(*), COUNT(client_ticker) FROM alt_political_us.lobbying_filings
    """)).one()
    print(f"  lobbying_filings    total={n_filings:>6,}  client_ticker={n_with_ticker:>6,}")


def _contract_overlap(c) -> None:
    _hdr("6. Cross-source contract overlap (USASpending vs Finnhub)")
    rows = list(c.execute(text("""
        WITH src AS (
            SELECT gc.ticker,
                   gc.action_date,
                   gc.awarding_agency,
                   e.code AS endpoint_code
            FROM alt_political_us.gov_contracts gc
            JOIN ref.data_endpoints e ON e.id = gc.endpoint_id
            WHERE gc.ticker IS NOT NULL
              AND e.code IN ('usaspending_direct','finnhub_usa_spending')
        )
        SELECT ticker,
               SUM(CASE WHEN endpoint_code='usaspending_direct'  THEN 1 ELSE 0 END) AS usa,
               SUM(CASE WHEN endpoint_code='finnhub_usa_spending' THEN 1 ELSE 0 END) AS fin
        FROM src
        GROUP BY ticker
        HAVING SUM(CASE WHEN endpoint_code='usaspending_direct'  THEN 1 ELSE 0 END) > 0
            OR SUM(CASE WHEN endpoint_code='finnhub_usa_spending' THEN 1 ELSE 0 END) > 0
        ORDER BY (
            SUM(CASE WHEN endpoint_code='usaspending_direct'  THEN 1 ELSE 0 END) +
            SUM(CASE WHEN endpoint_code='finnhub_usa_spending' THEN 1 ELSE 0 END)
        ) DESC
        LIMIT 10
    """)))
    if not rows:
        print("  (no contracts ingested)")
        return
    print(f"  {'ticker':10s}  {'usaspending':>12s}  {'finnhub':>12s}")
    for r in rows:
        print(f"  {r.ticker:10s}  {r.usa:>12,}  {r.fin:>12,}")

    both = [r.ticker for r in rows if r.usa > 0 and r.fin > 0]
    if not both:
        print("  No tickers in both sources yet — overlap measurement deferred until USASpending backfill expands.")
        return
    for t in both[:3]:
        clusters = c.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT gc.action_date, gc.awarding_agency
                FROM alt_political_us.gov_contracts gc
                JOIN ref.data_endpoints e ON e.id = gc.endpoint_id
                WHERE gc.ticker = :t
                  AND e.code IN ('usaspending_direct','finnhub_usa_spending')
                GROUP BY gc.action_date, gc.awarding_agency
                HAVING COUNT(DISTINCT e.code) = 2
            ) s
        """), {"t": t}).scalar()
        print(f"  {t}: same-day+same-agency clusters in BOTH sources = {clusters}")


def _flagship(c) -> None:
    _hdr("7. Flagship conjunction: committee × contract × recent purchase")
    rows = list(c.execute(text("""
        WITH recent_buys AS (
            SELECT lt.bioguide_id, lt.ticker, lt.transaction_date, lt.amount_mid,
                   lt.transaction_type
            FROM alt_political_us.legislator_trades lt
            WHERE lt.transaction_type IN ('purchase','exchange')
              AND lt.bioguide_id IS NOT NULL
              AND lt.ticker IS NOT NULL
              AND lt.transaction_date >= CURRENT_DATE - INTERVAL '180 days'
        ),
        contractors AS (
            SELECT DISTINCT ticker FROM alt_political_us.gov_contracts WHERE ticker IS NOT NULL
        ),
        relevant_committees AS (
            SELECT DISTINCT ca.bioguide_id
            FROM alt_political_us.committee_assignments ca
            JOIN alt_political_us.committee_sector_map csm USING (country_code, committee_id)
            WHERE csm.signal_strength IN ('high','very_high','extreme','medium_high')
        )
        SELECT rb.bioguide_id,
               COALESCE(l.first_name || ' ' || l.last_name, '?') AS name,
               rb.ticker, rb.transaction_date, rb.amount_mid, rb.transaction_type
        FROM recent_buys rb
        JOIN contractors c USING (ticker)
        JOIN relevant_committees rc USING (bioguide_id)
        LEFT JOIN alt_political_us.legislators l USING (bioguide_id)
        ORDER BY rb.transaction_date DESC, rb.amount_mid DESC NULLS LAST
        LIMIT 20
    """)))
    if not rows:
        print("  (no conjunction matches yet — expected before House/Senate full backfills)")
        return
    print(f"  {'bioguide':10s}  {'name':25s}  {'ticker':6s}  {'date':12s}  {'amount':>10s}  type")
    for r in rows:
        amt = f"${r.amount_mid:,}" if r.amount_mid else ""
        print(f"  {r.bioguide_id:10s}  {r.name[:25]:25s}  {r.ticker:6s}  {r.transaction_date}  {amt:>10s}  {r.transaction_type}")


def _recent_trades(c) -> None:
    _hdr("8. 15 most-recent trades — ALL (ticker + non-ticker)")
    for r in c.execute(text("""
        SELECT lt.transaction_date,
               COALESCE(l.last_name, lt.legislator_name_raw) AS name,
               lt.ticker, lt.asset_name_raw, lt.transaction_type, lt.amount_mid,
               lt.bioguide_id IS NOT NULL AS bio_ok,
               lt.security_id IS NOT NULL AS sec_ok,
               EXISTS(SELECT 1 FROM alt_political_us.gov_contracts gc
                      WHERE gc.ticker = lt.ticker) AS has_contracts,
               EXISTS(SELECT 1 FROM alt_political_us.lobbying_filings lf
                      WHERE lf.client_ticker = lt.ticker) AS has_lobbying
        FROM alt_political_us.legislator_trades lt
        LEFT JOIN alt_political_us.legislators l USING (bioguide_id)
        ORDER BY lt.transaction_date DESC NULLS LAST
        LIMIT 15
    """)):
        flags = ("B" if r.bio_ok else "-") + ("S" if r.sec_ok else "-") + \
                ("C" if r.has_contracts else "-") + ("L" if r.has_lobbying else "-")
        amt = f"${r.amount_mid:,}" if r.amount_mid else ""
        # Show ticker if we have it; else fall back to asset_name_raw (truncated)
        asset = r.ticker if r.ticker else (r.asset_name_raw or "?")[:30]
        print(f"  {r.transaction_date}  {(r.name or '?')[:18]:18s}  {asset[:30]:30s}  "
              f"{r.transaction_type[:8]:8s}  {amt:>12s}  [{flags}]")


def _no_ticker_breakdown(c) -> None:
    _hdr("8b. Non-ticker trades — breakdown")
    print("  By asset_type_code:")
    for r in c.execute(text("""
        SELECT COALESCE(lt.asset_type_code, '(none)') AS code,
               COALESCE(atc.name, '(uncoded — mostly SSW)') AS label,
               COUNT(*) AS n
        FROM alt_political_us.legislator_trades lt
        LEFT JOIN alt_political_us.asset_type_codes atc
               ON atc.country_code = lt.country_code AND atc.code = lt.asset_type_code
        WHERE lt.ticker IS NULL
        GROUP BY lt.asset_type_code, atc.name
        ORDER BY n DESC
    """)):
        print(f"    {r.code:8s}  {r.label[:55]:55s}  n={r.n:>6,}")

    # Heuristic categorization of uncoded asset_name_raw — fund / bond / paper-PTR / other
    print("  Heuristic categorization of uncoded SSW non-ticker descriptions:")
    rows = list(c.execute(text("""
        SELECT
          CASE
            WHEN lower(asset_name_raw) LIKE '%this filing was disclosed via scanned pdf%'
                THEN 'paper_ptr_placeholder'
            WHEN lower(asset_name_raw) LIKE '%bond%'
              OR lower(asset_name_raw) LIKE '%note%'
              OR lower(asset_name_raw) LIKE '%treasury%'
              OR lower(asset_name_raw) LIKE '%t-bill%'
                THEN 'bond_or_note'
            WHEN lower(asset_name_raw) LIKE '%mutual fund%'
              OR lower(asset_name_raw) LIKE '%fund%'
              OR lower(asset_name_raw) LIKE '%etf%'
              OR lower(asset_name_raw) LIKE '%vanguard%'
              OR lower(asset_name_raw) LIKE '%fidelity%'
              OR lower(asset_name_raw) LIKE '%blackrock%'
              OR lower(asset_name_raw) LIKE '%ishares%'
              OR lower(asset_name_raw) LIKE '%spdr%'
                THEN 'fund_or_etf'
            WHEN lower(asset_name_raw) LIKE '%option%'
              OR lower(asset_name_raw) LIKE '%call%'
              OR lower(asset_name_raw) LIKE '%put%'
                THEN 'option'
            ELSE 'unparsed_equity_likely'
          END AS bucket,
          COUNT(*) AS n
        FROM alt_political_us.legislator_trades
        WHERE ticker IS NULL AND asset_type_code IS NULL
        GROUP BY 1
        ORDER BY 2 DESC
    """)))
    for r in rows:
        print(f"    {r.bucket:25s}  n={r.n:>6,}")

    print("  Top 10 fund/ETF descriptions (sampled):")
    for r in c.execute(text("""
        SELECT asset_name_raw, COUNT(*) AS n
        FROM alt_political_us.legislator_trades
        WHERE ticker IS NULL
          AND (
            lower(asset_name_raw) LIKE '%fund%'
            OR lower(asset_name_raw) LIKE '%etf%'
            OR lower(asset_name_raw) LIKE '%vanguard%'
            OR lower(asset_name_raw) LIKE '%fidelity%'
            OR lower(asset_name_raw) LIKE '%blackrock%'
            OR lower(asset_name_raw) LIKE '%ishares%'
            OR lower(asset_name_raw) LIKE '%spdr%'
          )
        GROUP BY asset_name_raw
        ORDER BY n DESC
        LIMIT 10
    """)):
        print(f"    n={r.n:>4}  {r.asset_name_raw[:80]}")


def _archive(c) -> None:
    _hdr("9. audit.raw_archive coverage (post-Phase-B unified)")
    for row in c.execute(text("""
        SELECT v.code AS vendor, e.code AS endpoint, COUNT(*) AS n,
               MIN(a.fetched_at) AS first_fetched,
               MAX(a.fetched_at) AS last_fetched
        FROM audit.raw_archive a
        JOIN ref.data_endpoints e ON e.id = a.endpoint_id
        JOIN ref.vendors v ON v.id = e.vendor_id
        GROUP BY v.code, e.code
        ORDER BY v.code, e.code
    """)):
        print(f"  {row.vendor:25s} -> {row.endpoint:30s} "
              f"n={row.n:>6,}  {row.first_fetched} .. {row.last_fetched}")


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    engine = get_engine()
    with engine.connect() as c:
        _row_counts(c)
        _source_breakdown(c)
        _bioguide_rate(c)
        _top_traders(c)
        _fk_resolution(c)
        _contract_overlap(c)
        _flagship(c)
        _recent_trades(c)
        _no_ticker_breakdown(c)
        _archive(c)
    print()


if __name__ == "__main__":
    main()
