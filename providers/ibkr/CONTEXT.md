# factorlab-provider-ibkr

> Read-only Interactive Brokers adapter that mirrors positions, account state, executions and open orders from the paper and live IB Gateways into `broker.*`, plus Flex Web Service statements (fills, executed orders, cash, end-of-day positions, and every statement row kept whole).

## Purpose

This is a provider member built on `ib_async` over the TWS socket API, not REST. It has two entry shapes:

- `IBKRBrokerProvider` ([provider.py](src/factorlab/sources/ibkr/provider.py)) is the `collect(ctx)` provider that the production snapshot job runs.
- `IbkrBrokerSnapshot` ([snapshot_source.py](src/factorlab/sources/ibkr/snapshot_source.py)) is the same capture -> archive -> normalize path as an engine dataset source for `broker.snapshot`. It is registered on import ([`__init__.py`](src/factorlab/sources/ibkr/__init__.py)).
- `IbkrFlexActivity` ([flex_source.py](src/factorlab/sources/ibkr/flex_source.py)) is the engine source for `broker.flex_activity`. It runs a saved Activity Flex Query over HTTPS ([flex.py](src/factorlab/sources/ibkr/flex.py)) and needs no Gateway, login or 2FA. [flex_normalize.py](src/factorlab/sources/ibkr/flex_normalize.py) turns the archived XML into executions, order events and cash flows.

## Owns and does not own

- Owns: read-only Gateway connections ([client.py](src/factorlab/sources/ibkr/client.py)), deterministic JSON capture envelopes ([capture.py](src/factorlab/sources/ibkr/capture.py)), and pure normalization to broker shapes ([normalize.py](src/factorlab/sources/ibkr/normalize.py)). It also owns pacing helpers ([pacing.py](src/factorlab/sources/ibkr/pacing.py)), a historical-bar fetcher ([historical.py](src/factorlab/sources/ibkr/historical.py)) and a contract cache ([contracts.py](src/factorlab/sources/ibkr/contracts.py)). Nothing in the repo calls the last two.
- Does not own: order placement, which is out of scope ([docs/data-sources/us/ibkr.md](../../docs/data-sources/us/ibkr.md) §4). It also does not own the record shapes (`factorlab.ingest.datasets.broker`, re-exported by [shapes.py](src/factorlab/sources/ibkr/shapes.py)), the `broker.*` writers (`factorlab-storage` `V2BrokerStorage`), the snapshot scheduler and heartbeat (`components/ingest-broker`), or Gateway login and 2FA (IBC in the Gateway container, [ibkr-gateway-setup.md](../../docs/operations/ibkr-gateway-setup.md)).

## Entry points

- `IBKRBrokerProvider` (`source = "ibkr"`, `pipeline = "ibkr_broker_snapshot"`, `market_code = "USA"`) with `SnapshotConfig` (modes, `client_id=2`, a 1-day executions lookback).
- `IbkrBrokerSnapshot` (`provider = "ibkr"`, `dataset = "broker.snapshot"`) with `IbkrSettings`. `plan` makes one unit per `<mode>:<dataset>` for `positions`, `account_state`, `executions` and `open_orders`.
- Connection helpers: `connect`, `connect_with_retry`, `connected`, `gateway_config`.
- `IbkrFlexActivity` (`provider = "ibkr"`, `dataset = "broker.flex_activity"`) with `IbkrFlexSettings` (`timeout` 30s, `poll_interval` 5s, `poll_attempts` 24). `plan` makes one unit, `flex:statement`.
- Through the component: `factorlab-ingest-broker snapshot --daemon --run-on-start` (production), or `factorlab-ingest-broker engine run --dataset broker.snapshot --market USA --dry-run`. Flex history is on demand: `factorlab-ingest-broker engine run --dataset broker.flex_activity --market USA` (add `--dry-run` first).

## Configuration and secrets

Gateway endpoints are read with `get_secret`, so they come from a secret file or the env. They are not read from YAML.

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| host | `IBKR_HOST_PAPER` / `IBKR_HOST_LIVE`, then `IBKR_HOST` | `127.0.0.1` | Per mode |
| port | `IBKR_PORT_PAPER` / `IBKR_PORT_LIVE` | 4002 / 4001 | On the VPS: socat relays 4004 / 4003 (`ibkr.yaml`) |
| client id | `IBKR_CLIENT_ID` | 1 | Used only when the caller passes none. The snapshot passes `SNAPSHOT_CLIENT_ID = 2`. |
| mode | `IBKR_DEFAULT_MODE` | `paper` | Used only when no mode is passed |
| `IbkrSettings.modes`, `client_id`, `executions_lookback_hours` | - | `("paper","live")`, 2, 24 | Loaded from [configs/sources/ibkr.yaml](../../configs/sources/ibkr.yaml). The YAML does not set them, so the defaults apply. |
| `connect_attempts` / `connect_timeout` | - | 3 / 15s | Backoff starts at 5s, doubles, and is capped at 60s |
| Flex token | secret `IBKR_FLEX_TOKEN` | none | Cloudflare Secrets Store -> secrets agent -> `ibkr_client_runtime`. Never logged or put in `source_url` |
| Flex query id | `IBKR_FLEX_QUERY_ID` | none | `production.env`, passed through compose. The saved Activity query (XML) needs Trades (Executions + Orders) and Cash Transactions |

The component also reads `IBKR_SNAPSHOT_TIMES`, `IBKR_SNAPSHOT_MODES` and `IBKR_SNAPSHOT_LOCK`. Those belong to the component, not the provider. The client-id plan (0 manual, 1 research, 2 snapshot, 3 historical, 4 watchers, 10+ trade engine) is in `ibkr.yaml`.
After connecting, blocking requests use the connection timeout (default 15s),
including execution requests. Gateway outages cannot leave those requests waiting forever.

## Data

**Simulation:** `SimulatedGateway`/`SimulatedBrokerProvider` reuse capture and normalization for synthetic US/USD paper accounts without network calls. Runs use `ibkr_broker_simulation` / `ibkr_simulation`; file captures are marked simulated, channels are account-specific, and fill IDs/times are stable. Snapshot recording is optional for old sinks.

**Trade stream:** `stream_simulation.py` supplies an event-driven cash ledger and IBKR-shaped reads for long-only USD stocks. Partial fills, fees, weighted cost, realized/unrealized P&L, cancellations, and quotes feed the same provider path; scenario validation rejects impossible fills. The component owns pacing, checkpoints, and storage.

- Binding in [bindings.yaml](../../configs/ingestion/bindings.yaml): `{dataset: broker.snapshot, market: USA, provider: ibkr, role: shadow, params: {modes: [paper, live]}}  # -> primary`
- The tables are `broker.positions_snapshot`, `broker.account_state_snapshot`, `broker.executions` and `broker.open_orders_snapshot`. They have no `source` in their key, so the shadow fetches, archives and normalizes but writes nothing.
- `source` is `ibkr`. The legacy provider's `source_channel` is `paper_gateway` or `live_gateway`. The engine source's is `<instance>:paper_gateway` or `<instance>:live_gateway`. Switching paths changes that column in `broker.*` (07 §15.1).
- Flex (`broker.flex_activity`): binding `role: primary`. It writes `broker.executions`, `broker.order_events`, `broker.cash_flows`, `broker.positions_snapshot` and `broker.statement_rows`. `source_channel` is `<instance>:flex_statement`, and the capture is the statement XML exactly as received (`transport="http"`, `application/xml`). Flex `dateTime` is US/Eastern wall time, converted to UTC. `ibCommission` is negated to match the Gateway sign. `ibOrderID` is the `perm_id`. `order_id` is 0 and `placed_by_client` is 0 when `isAPIOrder="N"`, otherwise `None`. Mode comes from the account id (`D...` is paper). Executed orders become one `filled` event at `event_seq = 2**32-1`. Cash `SUMMARY` rows and `(Ca.)` cancellation legs are skipped. `<OpenPosition levelOfDetail="SUMMARY">` rows become positions stamped at the `reportDate` 16:00 New York close (`LOT` rows and zero positions are dropped): `avg_cost` = `costBasisPrice`, `market_value` = `positionValue`, `unrealized_pnl` = `fifoPnlUnrealized`, `realized_pnl_ytd` from the statement's `MTDYTDPerformanceSummaryUnderlying` by conid, `market_value_usd` only for USD positions, `fx_rate_to_base` = `fxRateToBase`. Every element of every section, the `<FlexStatement>` header included, also becomes a `StatementRow` with all attributes in a map; `row_key` is a digest of the element and its attributes (`#n` for identical repeats), so a re-fetched statement replaces its rows.
- Gateway captures have `transport="tcp_socket"`, the envelope `{"schema": "ibkr.<kind>.v1", "kind", "mode", "fetched_at", "request", "data"}`, and kinds `portfolio`, `account_values`, `executions` and `open_orders`.
- Open-order captures explicitly call `reqAllOpenOrders()` to retrieve a fresh
  snapshot across clients on read-only connections. The call has a 15-second
  timeout, restores the connection's prior timeout afterward, and propagates
  timeout failures to the dataset unit instead of archiving an empty result.
  No master-client setting or startup order cache is required for this snapshot.

## Dependencies and contracts

- Workspace: `factorlab-core` and `factorlab-ingest`. Third-party: `pydantic` and `ib_async`, which is imported lazily inside `connect`.
- R1/R8 ([test_boundaries.py](../../tests/architecture/test_boundaries.py)): no storage import. `BrokerStorage` is a Protocol, and the component injects the writer.
- Read-only is not opt-out. `connect(readonly=False)` raises `IBKRReadOnlyViolation`, and [test_readonly_grep.py](tests/test_readonly_grep.py) fails on `placeOrder(`, `cancelOrder(`, `MarketOrder`, `LimitOrder` and similar in this package.
- `normalize` reads only the archived bytes. IBKR unset sentinels and non-finite numbers become `None`.

## Observability

- Logger names are `factorlab.sources.ibkr.*`. They log connect and disconnect, connect retries, and pacing sleeps.
- `ingest-broker` health is a heartbeat with `service: ibkr_broker_snapshot`, `max_age: 300` ([component.yaml](../../components/ingest-broker/component.yaml)). Each snapshot is one `meta.ingestion_runs` row. A down Gateway fails only its own units, and the run ends `partial`. Use the `factorlab-logs` and `factorlab-clickhouse` skills.

## Tests

`uv run pytest providers/ibkr`

- Unit tests cover the client, pacing, shapes, portfolio, executions, open orders, capture and normalize, the provider, the snapshot source and the read-only grep. Gateway mocks come from `factorlab.testkit.ibkr` via [conftest.py](tests/conftest.py), so no Gateway is contacted.
- [test_conformance.py](tests/test_conformance.py) runs the shared kit against `tests/fixtures/broker.snapshot/` (`paper_*` captures). Regenerate with `uv run python -m factorlab.testkit.conformance regen ibkr`.

## Release and rollback

- A provider is never released alone. It ships only in the `ingest-broker` image: `providers: [ibkr]` in [component.yaml](../../components/ingest-broker/component.yaml) and [providers.py](../../components/ingest-broker/src/factorlab/components/ingest_broker/providers.py). Rollback class is `writer`.
- There is no `legacy/` directory. Production `ibkr-snapshot` imports `IBKRBrokerProvider` from this package ([snapshot.py](../../components/ingest-broker/src/factorlab/components/ingest_broker/snapshot.py)), so provider changes are production changes.
- Bindings changes are config, not code. `configs/` is baked into the image, so they ship with an `ingest-broker` image. Cutover follows 07 §15.2.

## Pitfalls

- Pacing (client side, [pacing.py](src/factorlab/sources/ibkr/pacing.py)): at most 50 requests per 600s against IBKR's hard 60. There is a 15s cooldown between identical requests, and `BID_ASK` weighs 2. On error 162 the backoff starts at 30s, doubles, and is capped at 300s. `RateLimiter` is not thread-safe.
- The engine source holds one connection per mode per run. A failed connect marks that mode failed (`PermanentError`) for its remaining units without reconnecting. `close()` disconnects.
- The live Gateway needs weekly IBKR Mobile 2FA. Until then, live units fail while paper units still land.
- The Flex token is IP-restricted in the IBKR Portal to the VPS address, so Flex runs only from the VPS. Elsewhere it fails with `AuthRequired` (code 1013). The token expires 2027-09-25; renew it in the Portal and update `IBKR_FLEX_TOKEN`. One statement covers at most 365 days, so older history needs a query with an explicit earlier date range.
- The broker writer keeps any `exec_id` already in `broker.executions` when the write comes from `flex_statement`, because Gateway rows carry the session order id and client id. Flex fills with an unknown API client get `execution_method_id = 'unmapped_api_client'`.
