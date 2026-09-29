# factorlab-provider-house-clerk

> US House Clerk STOCK Act disclosures: the annual filing index and per-filing PTR PDFs, parsed into `alt.political_filings` and `alt.political_trades`.

## Purpose

This is a provider member with two sources, `HouseClerkFilings` and `HouseClerkTrades` ([sources.py](src/factorlab/sources/house_clerk/sources.py)), for market `USA`.
Importing `factorlab.sources.house_clerk` registers them ([`__init__.py`](src/factorlab/sources/house_clerk/__init__.py)).
The pure parsers in [parse.py](src/factorlab/sources/house_clerk/parse.py) are shared with the legacy political bootstrap, which is still the production writer.

## Owns and does not own

- Owns: HTTPS fetches restricted to `disclosures-clerk.house.gov`, the ZIP/XML index parser, PDF text extraction and the PTR transaction parser (`PARSER_VERSION = "house_ptr_v1"`), and ticker canonicalisation (`BRK.B` -> `BRK-B`).
- Does not own: resolving filers to legislators (the DB service resolves the `legislator_name` hint, using legislators from [congress-legislators](../congress-legislators/CONTEXT.md)), choosing which filings to fetch trades for (the orchestration CLI builds the `FilingsRequest` from `recent_filings`, `--recent-filings N`), ClickHouse writes, or the production bootstrap (`components/ingest-political` legacy).

## Entry points

- `HouseClerkFilings` (`alt.political_filings`): one unit per year from `params.years`, defaulting to the current UTC year.
- `HouseClerkTrades` (`alt.political_trades`): one unit per filing in a `FilingsRequest`.
- Parsers: `parse_house_filing_index`, `parse_house_ptr_text`, `pdf_text` ([parse.py](src/factorlab/sources/house_clerk/parse.py)). `canonical_ticker` is in [sources.py](src/factorlab/sources/house_clerk/sources.py).
- Run it through the component: `factorlab-ingest-political engine run --dataset alt.political_trades --market USA --recent-filings 20 --dry-run`.

## Configuration and secrets

`HouseClerkSettings` loads `configs/sources/house_clerk.yaml`. That file does not exist, so the code defaults apply.

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| `index_url` | - | `https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.ZIP` | |
| `timeout` | - | 60.0 | |

There are no secrets and no auth. The PTR PDF URL comes from each filing's `filing_url`, which the index parser builds from `_PTR_URL`. [configs/sources/political.yaml](../../configs/sources/political.yaml) documents the legacy sources (`polite_delay_sec: 0.2`, `year_range`), but this package does not read it and has no rate limiter.

## Data

- `source` is `house_clerk`. `source_channel` is `<instance>:filing_index` or `<instance>:ptr`. Unit names are `index:<year>` and `ptr:<filing_id>`.
- Bindings in [bindings.yaml](../../configs/ingestion/bindings.yaml):
  - `{dataset: alt.political_filings, market: USA, provider: house_clerk, role: shadow}     # -> primary`
  - `{dataset: alt.political_trades, market: USA, provider: house_clerk, role: shadow}      # -> primary`
- These tables have no `source` in their key. A shadow run fetches, archives and normalizes, but only counts rows and writes nothing (07 §9.2).
- The index parser keeps only `FilingType == "P"` (PTR) rows.
- Filing records carry `filer=EntityRef("legislator_name", key, "person_legislator", raw)` from `legislator_name_key`. Trade records carry `instrument=InstrumentRef("", "", "", ticker, "US")` and keep `ticker_raw`.

## Dependencies and contracts

- Workspace: only `factorlab-ingest` (datasets, political records, `legislator_name_key`, errors, `raise_for_status`). Third-party: `requests`, `pdfplumber`, and `defusedxml`, which refuses entity expansion in remote XML.
- R1/R8 ([test_boundaries.py](../../tests/architecture/test_boundaries.py)): no storage, no other provider, no dataframe, DB or web stack.
- `normalize` is pure over the archived ZIP or PDF bytes. A corrupt PDF or unreadable index raises `NormalizationError`, and the capture stays archived for replay.

## Observability

- The module does no logging of its own. Engine logs use pipelines `alt.political_filings:house_clerk` and `alt.political_trades:house_clerk`, including "shadow normalized N row(s); not written".
- The engine writes `meta.ingestion_runs` and `raw.archive`. The `ingest-political` health check is `exit-zero`. Use the `factorlab-logs` and `factorlab-clickhouse` skills.

## Tests

`uv run pytest providers/house-clerk`

- [test_conformance.py](tests/test_conformance.py) runs the shared kit against `tests/fixtures/alt.political_filings/` (`index_2026`) and `alt.political_trades/` (`ptr_20000001`).
- Regenerate expectations after an intentional parser change: `uv run python -m factorlab.testkit.conformance regen house_clerk`.

## Release and rollback

- A provider is never released alone. It ships inside the `ingest-political` image: `providers: [house_clerk, congress_legislators]` in [component.yaml](../../components/ingest-political/component.yaml) and [providers.py](../../components/ingest-political/src/factorlab/components/ingest_political/providers.py). Rollback class is `writer`.
- Production is `ingest-political bootstrap --ptr-limit 20`, scheduled `15 2 * * *` through [legacy/bootstrap.py](../../components/ingest-political/src/factorlab/components/ingest_political/legacy/bootstrap.py).
- Bindings changes are config, not code. `configs/` is baked into the image, so they ship with an `ingest-political` image. Cutover follows 07 §15.2.

## Pitfalls

- A change to [parse.py](src/factorlab/sources/house_clerk/parse.py) changes production today. [legacy/house_clerk.py](../../components/ingest-political/src/factorlab/components/ingest_political/legacy/house_clerk.py) re-exports its parsers and URL constants. Bump `PARSER_VERSION` when parse output changes.
- The parser has a known quirk: it reads `BILL` as a ticker from "US Treasury Bill [GS]". It is tracked in 06 §15 Wave 4 and was kept for parity.
- `parse.py` and `__init__.py` docstrings still say the legacy fetchers live in `sources.political.house_clerk`. The real module is the component's `legacy/house_clerk.py`.
- Keep the host allow-list. `_get` refuses any URL that is not `https://disclosures-clerk.house.gov`.
