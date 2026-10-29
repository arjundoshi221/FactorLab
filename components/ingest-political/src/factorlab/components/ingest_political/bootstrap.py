"""Collect congressional references, filing indexes and PTRs through dataset sources."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from factorlab.components.ingest_political.providers import PROVIDERS
from factorlab.core.logging import configure_logging
from factorlab.ingest.bindings import Binding, load_bindings, validate_bindings
from factorlab.ingest.datasets import ReferenceRequest
from factorlab.ingest.datasets.political import FilingsRequest, congress_number
from factorlab.ingest.engine import collect_binding
from factorlab.ingest.provider import RunContext, ingestion_run, run_config
from factorlab.ingest.registry import REGISTRY, load_providers, source_for
from factorlab.storage.sinks import ClickHouseSinks

DATASETS = ("ref.legislators", "alt.political_filings", "alt.political_trades")


def collect(
    ctx: RunContext,
    sink: ClickHouseSinks,
    bindings: dict[str, Binding],
    *,
    year: int,
    congress: int,
    ptr_limit: int,
) -> None:
    """Keep reference dependencies ordered and defer downstream work on failure."""
    for dataset in DATASETS:
        if dataset == "alt.political_trades" and ptr_limit == 0:
            continue
        binding = bindings[dataset]
        if dataset == "ref.legislators":
            request = ReferenceRequest("USA", params={**binding.params, "congress": congress})
        elif dataset == "alt.political_filings":
            request = ReferenceRequest("USA", params={**binding.params, "years": [year]})
        else:
            filings = sink.recent_filings(chamber="house", limit=ptr_limit, filing_year=year)
            request = FilingsRequest("USA", tuple(filings), params=dict(binding.params))
            if len(filings) < ptr_limit:
                ctx.fail_unit(
                    "ptr:unavailable",
                    f"only {len(filings)} of {ptr_limit} requested filings exist for {year}",
                )
        outcomes = collect_binding(ctx, binding, source_for(binding), sink, request)
        if any(not outcome.ok for outcome in outcomes):
            for deferred in DATASETS[DATASETS.index(dataset) + 1 :]:
                if deferred != "alt.political_trades" or ptr_limit:
                    ctx.fail_unit(deferred, f"deferred after {dataset} failed")
            return


def main(argv: list[str] | None = None) -> int:
    configure_logging(component="ingest-political", service="ingest-political")
    parser = argparse.ArgumentParser(description="Ingest congressional references and House PTRs")
    parser.add_argument("--year", type=int, default=datetime.now(UTC).year)
    parser.add_argument("--ptr-limit", type=int, default=0)
    parser.add_argument("--congress", type=int, default=congress_number(datetime.now(UTC).date()))
    args = parser.parse_args(argv)
    if args.ptr_limit < 0 or args.congress < 1 or not 1900 <= args.year <= 9999:
        parser.error(
            "--ptr-limit must be nonnegative, --congress positive, and --year between 1900 and 9999"
        )
    load_providers(PROVIDERS)
    configured = load_bindings().for_providers(PROVIDERS)
    validate_bindings(configured, REGISTRY)
    bindings = {}
    for dataset in DATASETS:
        candidates = [
            b for b in configured.enabled(dataset=dataset, market="USA") if b.role == "primary"
        ]
        if len(candidates) != 1:
            raise ValueError(f"political bootstrap requires one primary binding for {dataset}")
        bindings[dataset] = candidates[0]
    sink = ClickHouseSinks.from_environment("USA")
    try:
        with ingestion_run(
            sink,
            pipeline="political_bootstrap",
            source="political",
            market_code="ALT_POLITICAL",
            universe="house",
            requested_series=4 + args.ptr_limit,
            metadata={"year": args.year, "congress": args.congress, "ptr_limit": args.ptr_limit},
            config=run_config(ptr_limit=args.ptr_limit, year=args.year, congress=args.congress),
        ) as ctx:
            collect(
                ctx,
                sink,
                bindings,
                year=args.year,
                congress=args.congress,
                ptr_limit=args.ptr_limit,
            )
        return 0 if ctx.status == "success" else 2
    finally:
        sink.close()
