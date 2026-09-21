"""Phase 1→4 backfill driver.

Phase 1 — reference dims (legislators YAMLs)
Phase 2 — trade events (SSW historical, House Clerk PTRs, Senate eFD PTRs)
Phase 3 — conjunction layers (USASpending, Finnhub contracts, LDA, FEC*, Congress*)
Phase 4 — verify (ANALYZE + count queries)

* FEC + Congress.gov are stubs in this PR; they no-op without real keys.

Each phase is independently runnable and idempotent. Crashes mid-stream
resume cleanly via per-source state files.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)


@dataclass
class PhaseResult:
    phase: int
    name: str
    sources_run: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def run_phase_1(engine: Engine, *, dry_run: bool = False) -> PhaseResult:
    """Reference dimensions: legislators + committees + assignments."""
    log.info("=== PHASE 1: reference dimensions ===")
    res = PhaseResult(phase=1, name="reference dims")
    from factorlab.countries.us.political.legislators.ingest import ingest_legislators
    leg = ingest_legislators(engine, dry_run=dry_run)
    res.sources_run.append("legislators")
    res.notes.append(
        f"legislators={leg.legislators_n}  terms={leg.legislator_terms_n}  "
        f"fec_ids={leg.legislator_fec_ids_n}  committees={leg.committees_n}  "
        f"assignments={leg.committee_assignments_n}"
    )
    return res


def run_phase_2(
    engine: Engine,
    *,
    house_clerk_years: list[int] | None = None,
    house_clerk_limit: int | None = None,
    senate_efd_from: str | None = None,
    senate_efd_to: str | None = None,
    senate_efd_headless: bool = True,
    senate_efd_max_filings: int | None = None,
    skip_senate_efd: bool = False,
    dry_run: bool = False,
) -> PhaseResult:
    """Trade events: SSW historical → House Clerk → Senate eFD."""
    log.info("=== PHASE 2: trade events ===")
    res = PhaseResult(phase=2, name="trade events")

    from factorlab.countries.us.political.senate_stock_watcher.ingest import ingest_senate_stock_watcher
    from factorlab.countries.us.political.house_clerk.ingest import ingest_house_clerk

    # 2a. SSW historical (one-shot, fast)
    ssw = ingest_senate_stock_watcher(engine, dry_run=dry_run)
    res.sources_run.append("senate_stock_watcher")
    res.notes.append(f"ssw: {ssw.trades_inserted} trades")

    # 2b. House Clerk
    hc = ingest_house_clerk(
        engine,
        years=house_clerk_years,
        limit_total=house_clerk_limit,
        dry_run=dry_run,
    )
    res.sources_run.append("house_clerk")
    res.notes.append(
        f"house_clerk: {hc.ptrs_with_trades}/{hc.ptrs_attempted} PTRs with trades, "
        f"{hc.trades_inserted} rows inserted "
        f"(bioguide resolved {hc.bioguide_resolved}/{hc.bioguide_resolved + hc.bioguide_unresolved})"
    )

    # 2c. Senate eFD (Playwright local)
    if not skip_senate_efd:
        if not (senate_efd_from and senate_efd_to):
            log.warning("[phase2] senate_efd skipped: missing date window")
            res.notes.append("senate_efd: skipped (no date window)")
        else:
            from factorlab.countries.us.political.senate_efd.ingest import ingest_senate_efd
            sf = ingest_senate_efd(
                engine,
                date_from=senate_efd_from, date_to=senate_efd_to,
                headless=senate_efd_headless,
                max_filings=senate_efd_max_filings,
                dry_run=dry_run,
            )
            res.sources_run.append("senate_efd")
            res.notes.append(
                f"senate_efd: {sf.html_filings} html filings, {sf.paper_filings} paper, "
                f"{sf.trades_inserted} trades inserted"
            )
    else:
        res.notes.append("senate_efd: skipped (--skip-senate-efd)")

    return res


def run_phase_3(
    engine: Engine,
    *,
    contract_tickers: list[str] | None = None,
    contract_fy: int = 2025,
    contract_fy_range: list[int] | None = None,
    lda_years: list[int] | None = None,
    lda_max_pages_per_year: int | None = None,
    fec_cycles: list[int] | None = None,
    fec_include_unresolved_pacs: bool = False,
    fec_skip_mode_a: bool = False,
    fec_skip_mode_b: bool = False,
    congress_numbers: list[int] | None = None,
    congress_skip_deep: bool = False,
    congress_skip_hearings: bool = False,
    skip_finnhub: bool = False,
    skip_usaspending: bool = False,
    skip_lda: bool = False,
    skip_fec: bool = False,
    skip_congress: bool = False,
    dry_run: bool = False,
) -> PhaseResult:
    """Conjunction layers: contracts (USASpending + Finnhub), lobbying (LDA),
    campaign donations (FEC), bills + hearings (Congress.gov)."""
    log.info("=== PHASE 3: conjunction layers ===")
    res = PhaseResult(phase=3, name="conjunction layers")

    if not skip_usaspending:
        from factorlab.countries.us.political.usaspending.ingest import ingest_usaspending
        us = ingest_usaspending(engine, tickers=contract_tickers, fy=contract_fy,
                                fy_range=contract_fy_range, dry_run=dry_run)
        res.sources_run.append("usaspending")
        res.notes.append(f"usaspending: {us.contracts_inserted} contracts, {us.tickers_processed} tickers")

    if not skip_finnhub:
        from factorlab.countries.us.political.finnhub_contracts.ingest import ingest_finnhub_contracts
        fh = ingest_finnhub_contracts(engine, tickers=contract_tickers, fy=contract_fy,
                                      fy_range=contract_fy_range, dry_run=dry_run)
        res.sources_run.append("finnhub_contracts")
        if fh.skipped_no_key:
            res.notes.append("finnhub_contracts: skipped (FINNHUB_API_KEY missing)")
        else:
            res.notes.append(f"finnhub_contracts: {fh.contracts_inserted} contracts, {fh.tickers_processed} tickers")

    if not skip_lda:
        from factorlab.countries.us.political.lda.ingest import ingest_lda
        lda = ingest_lda(engine, years=lda_years,
                         max_pages_per_year=lda_max_pages_per_year, dry_run=dry_run)
        res.sources_run.append("lda")
        res.notes.append(
            f"lda: filings={lda.filings_n} activities={lda.activities_n} "
            f"targets={lda.targets_n} lobbyists={lda.lobbyists_n} "
            f"aliases_learned={lda.client_aliases_learned}"
        )

    if not skip_fec:
        from factorlab.countries.us.political.fec.ingest import ingest_fec
        fec_r = ingest_fec(
            engine,
            do_committees=True,
            do_mode_a=not fec_skip_mode_a,
            do_mode_b=not fec_skip_mode_b,
            cycles=fec_cycles,
            include_unresolved_pacs=fec_include_unresolved_pacs,
            dry_run=dry_run,
        )
        if not fec_r.skipped_no_key:
            res.sources_run.append("fec")
            cr = fec_r.committees
            dr = fec_r.donations
            res.notes.append(
                f"fec: committees={cr.fec_committees_n if cr else 0} "
                f"tickers_resolved={cr.tickers_resolved if cr else 0} "
                f"mode_a={dr.rows_inserted_mode_a if dr else 0} "
                f"mode_b={dr.rows_inserted_mode_b if dr else 0}"
            )
        else:
            res.notes.append(f"fec: skipped ({fec_r.note})")

    if not skip_congress:
        from factorlab.countries.us.political.congress_gov.ingest import ingest_congress_gov
        cg_r = ingest_congress_gov(
            engine,
            congresses=congress_numbers,
            do_list=True, do_detail=True,
            do_deep=not congress_skip_deep,
            do_hearings=not congress_skip_hearings,
            dry_run=dry_run,
        )
        if not cg_r.skipped_no_key:
            res.sources_run.append("congress_gov")
            br = cg_r.bills
            hr = cg_r.hearings_res
            res.notes.append(
                f"congress: bills_listed={br.bills_listed if br else 0} "
                f"enriched={br.bills_enriched if br else 0} "
                f"cosponsors={br.cosponsors_inserted if br else 0} "
                f"committees={br.committees_inserted if br else 0} "
                f"actions={br.actions_inserted if br else 0} "
                f"hearings={hr.hearings_inserted if hr else 0}"
            )
        else:
            res.notes.append(f"congress: skipped ({cg_r.note})")

    return res


def run_phase_4(engine: Engine) -> PhaseResult:
    """Validation queries — final counts + integrity sanity."""
    log.info("=== PHASE 4: verification ===")
    res = PhaseResult(phase=4, name="verification")

    queries = [
        ("legislators", "SELECT count(*) FROM alt_political_us.legislators"),
        ("legislator_terms", "SELECT count(*) FROM alt_political_us.legislator_terms"),
        ("legislator_fec_ids", "SELECT count(*) FROM alt_political_us.legislator_fec_ids"),
        ("committees", "SELECT count(*) FROM alt_political_us.committees"),
        ("committee_assignments", "SELECT count(*) FROM alt_political_us.committee_assignments"),
        ("legislator_trades", "SELECT count(*) FROM alt_political_us.legislator_trades"),
        ("legislator_trades (with bioguide)",
         "SELECT count(*) FROM alt_political_us.legislator_trades WHERE bioguide_id IS NOT NULL"),
        ("gov_contracts", "SELECT count(*) FROM alt_political_us.gov_contracts"),
        ("lobbying_filings", "SELECT count(*) FROM alt_political_us.lobbying_filings"),
        ("lobbying_activities", "SELECT count(*) FROM alt_political_us.lobbying_activities"),
        ("contract_aliases", "SELECT count(*) FROM alt_political_us.contract_aliases"),
        ("lobby_client_aliases", "SELECT count(*) FROM alt_political_us.lobby_client_aliases"),
        # FEC
        ("fec_committees (total)",
         "SELECT count(*) FROM alt_political_us.fec_committees"),
        ("fec_committees (ticker-resolved)",
         "SELECT count(*) FROM alt_political_us.fec_committees WHERE sponsor_company_ticker IS NOT NULL"),
        ("campaign_donations",
         "SELECT count(*) FROM alt_political_us.campaign_donations"),
        ("campaign_donations (Mode A: corp PAC donor)",
         "SELECT count(*) FROM alt_political_us.campaign_donations WHERE donor_committee_id IS NOT NULL"),
        ("campaign_donations (Mode B: individual donor)",
         "SELECT count(*) FROM alt_political_us.campaign_donations WHERE donor_committee_id IS NULL"),
        # Congress.gov
        ("bills (total)", "SELECT count(*) FROM alt_political_us.bills"),
        ("bills (with policy_area)",
         "SELECT count(*) FROM alt_political_us.bills WHERE policy_area IS NOT NULL"),
        ("bill_sponsors (sponsor)",
         "SELECT count(*) FROM alt_political_us.bill_sponsors WHERE role='sponsor'"),
        ("bill_sponsors (cosponsor)",
         "SELECT count(*) FROM alt_political_us.bill_sponsors WHERE role='cosponsor'"),
        ("bill_committees", "SELECT count(*) FROM alt_political_us.bill_committees"),
        ("bill_actions", "SELECT count(*) FROM alt_political_us.bill_actions"),
        ("hearings", "SELECT count(*) FROM alt_political_us.hearings"),
        ("raw_archive", "SELECT count(*) FROM alt_political_us.raw_archive"),
    ]
    with engine.connect() as c:
        for label, q in queries:
            n = c.execute(text(q)).scalar()
            res.notes.append(f"{label:38s} {n:>10,}")
            log.info("  %s = %s", label, f"{n:,}")
    return res


def run_all(
    engine: Engine,
    *,
    phases: list[int] | None = None,
    **kwargs,
) -> list[PhaseResult]:
    """Run requested phases in order. Default: 1, 2, 3, 4."""
    phases = phases or [1, 2, 3, 4]
    results: list[PhaseResult] = []
    if 1 in phases:
        results.append(run_phase_1(engine, dry_run=kwargs.get("dry_run", False)))
    if 2 in phases:
        results.append(run_phase_2(engine, **{k: v for k, v in kwargs.items() if k in {
            "house_clerk_years", "house_clerk_limit",
            "senate_efd_from", "senate_efd_to", "senate_efd_headless",
            "senate_efd_max_filings", "skip_senate_efd", "dry_run",
        }}))
    if 3 in phases:
        results.append(run_phase_3(engine, **{k: v for k, v in kwargs.items() if k in {
            "contract_tickers", "contract_fy", "contract_fy_range",
            "lda_years", "lda_max_pages_per_year",
            "fec_cycles", "fec_include_unresolved_pacs",
            "fec_skip_mode_a", "fec_skip_mode_b",
            "congress_numbers", "congress_skip_deep", "congress_skip_hearings",
            "skip_finnhub", "skip_usaspending", "skip_lda",
            "skip_fec", "skip_congress",
            "dry_run",
        }}))
    if 4 in phases:
        results.append(run_phase_4(engine))
    return results
