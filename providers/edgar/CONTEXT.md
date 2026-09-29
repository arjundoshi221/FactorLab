# factorlab-provider-edgar

> SEC EDGAR adapter. The engine source turns XBRL company facts into `fundamentals.filings`, and the package also carries a throttled client with helpers for the other EDGAR hosts.

## Purpose

This is a provider member. `EdgarCompanyFacts` ([fundamentals_source.py](src/factorlab/sources/edgar/fundamentals_source.py)) is the P8 "new provider plus new dataset" proof ([07 §15.1](../../docs/architecture/07-ingestion-provider-abstraction.md)). It is registered when `factorlab.sources.edgar` is imported ([`__init__.py`](src/factorlab/sources/edgar/__init__.py)).
The rest of the package is a library over `www.sec.gov`, `data.sec.gov` and `efts.sec.gov`. Nothing else in the repo calls it yet.

## Owns and does not own

- Owns: the companyfacts fetch with the SEC User-Agent and a 10 req/s limiter. It also owns the mapping from facts to `FundamentalFilingRecord` (one per accession) and `LineItemRecord` (one per taxonomy, tag, unit and period).
- Also owns: `EdgarClient` ([client.py](src/factorlab/sources/edgar/client.py)) and its helpers in [companyfacts.py](src/factorlab/sources/edgar/companyfacts.py), [filings.py](src/factorlab/sources/edgar/filings.py), [submissions.py](src/factorlab/sources/edgar/submissions.py), [search.py](src/factorlab/sources/edgar/search.py) and [tickers.py](src/factorlab/sources/edgar/tickers.py).
- Does not own: issuer resolution. The DB service resolves by the `cik` alias, falls back to the ticker hint's listing, then records the alias at `medium` confidence. It also does not own `filing_id`, the fundamentals sink (`factorlab-storage` `sinks/fundamentals.py`) or the dataset contract (`factorlab.ingest.datasets.fundamentals`).

## Entry points

- `EdgarCompanyFacts` (`provider = "edgar"`, `dataset = "fundamentals.filings"`). It makes one unit per `CompanyRef` in a `CompanyRequest`.
- Run it through the component: `factorlab-ingest-us engine run --dataset fundamentals.filings --market USA --cik 320193:AAPL --dry-run`. The `--cik` format is `<cik>[:<ticker>]`.
- Library: `EdgarClient().get_www / get_data / get_search`, `get_company_facts`, `get_submissions`, `get_quarterly_index`, `resolve_cik`, `search`.

## Configuration and secrets

`EdgarSettings` loads [configs/sources/edgar.yaml](../../configs/sources/edgar.yaml) but reads only top-level keys. The YAML's nested `api:` and `rate_limits:` blocks are ignored (`extra="ignore"`), so the code defaults below are what run.

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| `user_agent_env` | `EDGAR_USER_AGENT` | same | Required by SEC fair access, e.g. `"Name admin@example.com"`. If missing, the source raises `AuthRequired` and `EdgarClient()` raises `OSError`. |
| `requests_per_second` | - | 10 (1 to 10) | SEC's per-IP ceiling across all hosts |
| `base_url_data` / `base_url_www` | - | `https://data.sec.gov` / `https://www.sec.gov` | |
| `taxonomies` | - | `("us-gaap", "ifrs-full")` | |
| `tags` | - | `()` | Empty means every concept |
| `timeout` | - | 60.0 | |

`EdgarClient` has its own constants: a 100 ms minimum interval, up to 5 attempts (`RETRY_ATTEMPTS`) on 429 and 5xx with backoff that honours `Retry-After`, and a 60s timeout.

## Data

- `source` is `edgar`. `source_channel` is `<instance>:companyfacts`. Unit names are `companyfacts:<10-digit CIK>`.
- Binding in [bindings.yaml](../../configs/ingestion/bindings.yaml): `{dataset: fundamentals.filings, market: USA, provider: edgar, role: shadow}   # -> primary`
- The tables are `fundamentals.filings` and `fundamentals.line_items`. They have no `source` in the key, so the shadow normalizes and counts only. No production writer exists yet.
- Records: `issuer=EntityRef("cik", <padded cik>, "issuer", entityName)`, plus an optional ticker `issuer_hint`. `period_type` is `annual` for FY, `quarterly` for Q1 to Q4, and `other` otherwise. `is_amendment` is true when the form ends in `/A`. `filing_url` points at the Archives folder.
- Company facts carry only the filed date, so the sink stamps `filed_at` and `accepted_at` at midnight UTC and `statement` is `unclassified` (07 §15.1).

## Dependencies and contracts

- Workspace: `factorlab-core` (`get_secret`) and `factorlab-ingest`. Third-party: `pydantic`, `requests`.
- R1/R8 ([test_boundaries.py](../../tests/architecture/test_boundaries.py)): no storage import. `EdgarClient` has an optional duck-typed `storage` hook for archiving.
- `normalize` is pure. It skips facts with no `accn`, `end`, `val` or `filed`, and facts whose `start` is after `end`. Output is sorted, so it is deterministic.
- Vendor notes: [docs/data-sources/20-edgar-sec-filings.md](../../docs/data-sources/20-edgar-sec-filings.md).

## Observability

- `EdgarClient` logs under `factorlab.sources.edgar.client`. The engine source does no logging of its own.
- Engine pipeline `fundamentals.filings:edgar`. The engine writes `meta.ingestion_runs`, `raw.archive`, and parks unknown issuers in `meta.unresolved_entities`. Use the `factorlab-logs` and `factorlab-clickhouse` skills.

## Tests

`uv run pytest providers/edgar`

- [test_conformance.py](tests/test_conformance.py) runs the shared kit against `tests/fixtures/fundamentals.filings/` (`aapl_companyfacts`).
- [test_edgar_fundamentals.py](tests/test_edgar_fundamentals.py) covers unit mapping, missing-UA `AuthRequired`, learning the CIK alias, and parking. It imports storage and testkit, so run `uv sync --all-packages` first.
- Regenerate expectations after an intentional change: `uv run python -m factorlab.testkit.conformance regen edgar`.

## Release and rollback

- A provider is never released alone. It ships inside the `ingest-us` image ([component.yaml](../../components/ingest-us/component.yaml) `providers: [schwab, eodhd, github_csv, edgar]`, [providers.py](../../components/ingest-us/src/factorlab/components/ingest_us/providers.py)). Rollback class is `writer`.
- There is no legacy path and no production service for EDGAR yet. Bindings changes are config, not code. `configs/` is baked into the image, so they ship with an `ingest-us` image.

## Pitfalls

- `EDGAR_USER_AGENT` is not in `ingest-us`'s `secrets:` list ([component.yaml](../../components/ingest-us/component.yaml)). Add it there before running EDGAR in that container, or every unit fails `AuthRequired`.
- The 10 req/s ceiling is per IP across all SEC hosts. The engine source and `EdgarClient` each have their own throttle, so running both at once can exceed it.
- Editing `rate_limits` or `api.user_agent_env` in `edgar.yaml` changes nothing. Set the top-level `EdgarSettings` field names instead.
- An empty `tags` pulls every concept, and a large filer's companyfacts payload is big. Narrow `tags` for experiments.
