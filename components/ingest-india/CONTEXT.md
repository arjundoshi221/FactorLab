# ingest-india

> NSE equities and nearest stock futures from Upstox into ClickHouse v2 (1-minute bars).

## Purpose

`ingest-india` runs the production India collector: it polls Upstox during NSE hours,
archives every HTTP response to `raw.archive`, writes curated 1-minute bars, keeps the
expected-series inventory and fills history gaps. It is a writer component (image
`ghcr.io/arjundoshi221/factorlab-ingest-india`, manifest [component.yaml](component.yaml)).

## Owns and does not own

- Owns: the `factorlab-ingest-india` CLI ([cli.py](src/factorlab/components/ingest_india/cli.py)),
  [collector](src/factorlab/components/ingest_india/collector.py) and
  [premarket job](src/factorlab/components/ingest_india/premarket.py), plus the shipped providers
  ([providers.py](src/factorlab/components/ingest_india/providers.py): `upstox`).
- Does not own: the Upstox daily login and token storage (the Cloudflare Workers
  [upstox-auth-worker](../../cloudflare/upstox-auth-worker/) and
  [upstox-oauth-callback-worker](../../cloudflare/upstox-oauth-callback-worker/)), delivery of
  the token to the container (`secrets-agent`), the ClickHouse writers
  ([`V2IndiaStorage`](../../libs/storage/src/factorlab/storage/v2_india.py) in `libs/storage`),
  the schema (`schema-migrator`), and the provider package `providers/upstox`.

## Entry points

- Console script `factorlab-ingest-india` with subcommands:
  - `daemon`: the production collector (`--universe`, `--daemon`, `--once`, plus
    quote-batch and recovery-skip options; `daemon --help`).
  - `premarket`: refresh the NSE instrument master and reference data (`--exchange`, default `NSE`).
  - `engine`: the provider-agnostic engine from `factorlab.orchestration.cli`
    (validate, run, daemon, replay) restricted to this component's providers.
- Compose service `ingest-india` ([deploy/compose.yaml](deploy/compose.yaml)):
  `daemon --universe ${UPSTOX_UNIVERSE:-full_nse_eq} --daemon`, `mem_limit: 1500m`, network
  `backend`, no published ports; waits on `bootstrap` (`service_completed_successfully`).

## Configuration and secrets

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| Universe | `UPSTOX_UNIVERSE` (`production.env`) | `full_nse_eq` | names in `configs/universes/india.yaml` (baked into the image) |
| ClickHouse | `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_DATABASE`, `CLICKHOUSE_USERNAME` | compose: `clickhouse`, `8123`, `default`, `factorlab` | |
| ClickHouse password | secret `CLICKHOUSE_PASSWORD` | - | `india_runtime` tmpfs at `/run/secrets/app` |
| Upstox token | secret `UPSTOX_ACCESS_TOKEN` (+ `UPSTOX_ACCESS_TOKEN.expires_at`) | - | rendered by `secrets-agent` only while valid |
| Token persistence | `FACTORLAB_PERSIST_SECRETS` | `true` in code, `"false"` in compose | production never writes token files |
| Local OAuth only | `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`, `UPSTOX_REDIRECT_URL` | - | interactive login path; not used by the daemon |

## Data

- Writes `raw.archive` (`source` = `upstox`; endpoint in `source_channel`),
  `market.bars`,
  `market.futures_contract_bars`, `ref.listings` / `ref.securities` / `ref.contracts` /
  `ref.identifier_aliases`, `meta.expected_series` (source `upstox`),
  `meta.unresolved_entities` and `meta.ingestion_runs`. Requires the NSE row in
  `ref.exchanges` (seeded reference data).
- Pipelines (frozen strings): `india_intraday_1min`, `india_historical_1min_recovery`,
  `india_reference_premarket`; run `source` = `upstox`.
- Host path `/var/lib/factorlab/app-data` -> `/app/data` (instrument cache under
  `data/upstox/instruments`), shared with `ingest-us`.

## Dependencies and contracts

- Workspace: `factorlab-core`, `-runtime`, `-calendars`, `-ingest`, `-storage`,
  `-orchestration`, `factorlab-provider-upstox`. Third-party: `pandas`, `pyyaml`, `requests`,
  `exchange-calendars` (calendar `XBOM`), `tzdata` ([pyproject.toml](pyproject.toml)).
- Contracts: the v2 write shape behind `data_contract: 1`; the pipeline and `source`
  strings above (the API and hub filter on them); the token file names the agent renders.
- Market window (UTC): open 03:45, sweeps until 10:02 (2-minute grace after the 09:59
  candle); full-universe quotes every minute plus five seconds, named-universe equities every 300 s, futures every 600 s, recovery retry 900 s.

## Observability

- Logs: `/var/log/factorlab/ingest-india/ingest-india.jsonl` and
  `ingest-india-premarket.jsonl`; `run_id` / `pipeline` bind to `meta.ingestion_runs`.
  `uv run python tools/read_logs.py run --run-id <run_id>` or `tail --component ingest-india`.
- `meta.ingestion_runs` and `meta.expected_series` are the health signal; query them with
  the `factorlab-clickhouse` skill ([SKILL.md](../../.claude/skills/factorlab-clickhouse/SKILL.md)).
- Health kind `running` (image `HEALTHCHECK NONE`): the deployer requires the container to
  stay running with no restarts for the stabilization window.

## Tests

[tests/](tests/): full-universe mode, historical recovery, candles, rotating session.

```bash
uv run pytest components/ingest-india
```

## Release and rollback

`.\deploy\release.ps1 -Component ingest-india [-Bump ...] [-DryRun]` -> tag
`ingest-india/vX.Y.Z` -> [component-release.yml](../../.github/workflows/component-release.yml)
(tests, image build/verify) -> `deploy-component.sh ingest-india ...` via
[_deploy.yml](../../.github/workflows/_deploy.yml). The closure includes `configs/` and the
workspace libraries ([tools/affected.py](../../tools/affected.py)). Rollback class `writer`
with `data_contract: 1`: on failed verification the deployer restores the previous release
only if the contract is unchanged, otherwise stops `ingest-india` and reports
`fix-forward-required` ([factorlab_deploy.py](../../deploy/host/factorlab_deploy.py)). Bump
`data_contract` whenever what is written changes shape or meaning. The monolith path
(`release/*`, `deploy-release.sh`) still runs production until the rollout completes.

## Pitfalls

- The Upstox token expires daily at 03:30 IST. Without the daily login in the auth Worker
  the agent removes the token and the daemon waits (retry every 60 s); it never logs in
  interactively in production.
- `--universe` defaults to `demo` in the parser; production relies on the compose command.
- [`component-rollback.yml`](../../.github/workflows/component-rollback.yml) redeploys a
  recorded version without checking `data_contract`; do not roll back across a contract bump.
- The daemon installs SIGTERM handling and stops after the current batch; there is no
  `stop_grace_period` in the fragment (Docker default 10 s).
- Pipeline and `source` strings are frozen: renaming one orphans history in `meta.*` and
  breaks hub filters.

**UDP service monitoring:** The manifest declares expected liveness. Set FACTORLAB_MONITOR_ADDR=monitor:9125 after the monitor rollout; empty disables sending. The process reports crashes and deliberate stops, and daemon waits report idle liveness through runtime helpers. IndiaSink reports progress after each completed reference batch in the long startup reference sync; blocked work still stops heartbeat delivery.

Primary Upstox bindings drive the shared engine. Full-universe mode preserves
the existing absence of automatic historical recovery; named universes recover
inclusively from their last stored session. Missing requested quote instruments
fail the unit while retaining valid finalized bars.

**Collection plan (F-045):** every run records `metadata_json["config"]`: `mode` (`full`/`named`), `quote_interval_s`, `futures_interval_s` (`FUTURES_SWEEP_SECONDS`), `quote_batch_size`, `recovery` and `window_utc`. Named-universe sweeps use `NAMED_SWEEP_SECONDS` (300).
