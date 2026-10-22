# factorlab-provider-ishares

> iShares ETF holdings files as a membership proxy (IWV for the Russell 3000) and the source of GICS sectors.

## Purpose

A provider member. It implements `plan -> fetch -> normalize`
([07 §6](../../docs/architecture/07-ingestion-provider-abstraction.md)) for
`ref.universe_membership` and `ref.security_sectors` in market `USA`. Both datasets come
from the same `latest-holdings.csv`. Importing `factorlab.sources.ishares` registers
`IsharesUniverse` and `IsharesSectors`.
Methodology: [ADR-0017](../../docs/decisions/0017-sector-index-methodology.md).

## Owns and does not own

- Owns:
  - fetching holdings files from `www.ishares.com` (HTTPS only, redirects must stay on
    that host);
  - parsing the preamble and table (`parse_holdings`);
  - the equity filter and ticker normalization (`BRK B` -> `BRK-B`);
  - the iShares label -> GICS level-2 map (`GICS_SECTORS`).
- Does not own:
  - Schwab validation and publication of the roster (`ingest-us` `universe-us`);
  - effective dating (`factorlab-storage` sinks);
  - the index computation (`factorlab-indexes`).

## Entry points

- Registered sources: `IsharesUniverse` (`ref.universe_membership`) and `IsharesSectors`
  (`ref.security_sectors`).
- Production runs it through `universe-us` when the universe YAML has `provider: ishares`
  and `sector_provider: ishares`
  ([universe/engine.py](../../components/ingest-us/src/factorlab/components/ingest_us/universe/engine.py)).
- Generic: `factorlab-ingest-us engine run --dataset ref.security_sectors --market USA --instance ishares --dry-run`.

## Configuration and secrets

[configs/sources/ishares.yaml](../../configs/sources/ishares.yaml). Each `funds` key is a
universe code. The universe YAML builds the same settings for `universe-us`
(`ishares_settings`).

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| `funds.<code>.url` | - | - | `https://www.ishares.com/us/products/<id>/<slug>/latest-holdings.csv` |
| `funds.<code>.minimum_constituents` | - | - | `r3k_iwv`: 2400 |
| `funds.<code>.max_age_days` | - | 7 | the "Fund Holdings as of" date vs `fetched_at` |
| `funds.<code>.exchanges` | - | NYSE, NASDAQ, Nyse Mkt Llc, Cboe BZX | iShares `Exchange` labels kept |
| `funds.<code>.path` | - | empty | read this absolute local file instead of fetching `url` (still the provenance); a missing file is transient |
| `timeout` | - | 30 s | |

No secrets.

iShares' CDN (Akamai) returns HTTP 403 to the VPS's datacenter address, so production sets `path`:
[deploy/ishares-relay.ps1](../../deploy/ishares-relay.ps1) downloads the file on an operator machine,
checks it, and installs it as `/etc/factorlab/constituents/iwv-holdings.csv` (mounted read-only into
`universe-us` at `/app/config/constituents/`). Captures from it have `transport: file`.

## Data

- `source` is `ishares`; `source_channel` is `<instance>:holdings`.
- Universe code is `r3k_iwv`.
- Records carry bare tickers (`InstrumentRef("", "", "", symbol, "US")`). `universe-us`
  rewrites them to validated `schwab_symbol` aliases before writing.
- Kept rows have `Asset Class == Equity`, a listed US exchange, a usable ticker, and a
  name without RIGHTS/WARRANT/CVR/ESCROW/WHEN ISSUED. On 2026-10-19 holdings that is
  2,580 of 2,596 equity lines.

## Dependencies and contracts

- Workspace: `factorlab-ingest`. Third-party: `pydantic`, `requests`.
- Rule R8: no storage, no other provider.
- `normalize` is pure. A missing header, stale date, duplicate ticker or too few
  equities raises `NormalizationError`.

## Observability

- Engine pipelines `ref.universe_membership:ishares` and `ref.security_sectors:ishares`.
- In production both run inside the `us_universe_sync` run. Captures are in `raw.archive`.

## Tests

`uv run pytest providers/ishares`. Fixtures are trimmed verbatim lines of the live
2026-10-19 IWV file, covering every filtered line type. Regenerate expectations with
`uv run python -m factorlab.testkit.conformance regen ishares`.

## Release and rollback

Ships in the `ingest-us` image (`providers` in its manifest). Rollback class `writer`.

## Pitfalls

- IWV samples the Russell 3000. It is not the licensed roster, which is why the universe
  is named `r3k_iwv`.
- The holdings files are free to download but not openly licensed: internal research
  use only.
- iShares labels the communications sector `Communication`, not GICS's
  `Communication Services`.
- The older `...ajax?fileType=csv` URL now returns an HTML page. Use
  `latest-holdings.csv`.
