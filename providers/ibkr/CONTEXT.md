# factorlab-provider-ibkr

> Read-only Interactive Brokers adapter that mirrors positions, account state, executions and open orders from the paper and live IB Gateways into `broker.*`.

## Purpose

This is a provider member built on `ib_async` over the TWS socket API, not REST. It has two entry shapes:

- `IBKRBrokerProvider` ([provider.py](src/factorlab/sources/ibkr/provider.py)) is the `collect(ctx)` provider that the production snapshot job runs.
- `IbkrBrokerSnapshot` ([snapshot_source.py](src/factorlab/sources/ibkr/snapshot_source.py)) is the same capture -> archive -> normalize path as an engine dataset source for `broker.snapshot`. It is registered on import ([`__init__.py`](src/factorlab/sources/ibkr/__init__.py)).

## Owns and does not own

- Owns: read-only Gateway connections ([client.py](src/factorlab/sources/ibkr/client.py)), deterministic JSON capture envelopes ([capture.py](src/factorlab/sources/ibkr/capture.py)), and pure normalization to broker shapes ([normalize.py](src/factorlab/sources/ibkr/normalize.py)). It also owns pacing helpers ([pacing.py](src/factorlab/sources/ibkr/pacing.py)), a historical-bar fetcher ([historical.py](src/factorlab/sources/ibkr/historical.py)) and a contract cache ([contracts.py](src/factorlab/sources/ibkr/contracts.py)). Nothing in the repo calls the last two.
- Does not own: order placement, which is out of scope ([docs/data-sources/us/ibkr.md](../../docs/data-sources/us/ibkr.md) §4). It also does not own the record shapes (`factorlab.ingest.datasets.broker`, re-exported by [shapes.py](src/factorlab/sources/ibkr/shapes.py)), the `broker.*` writers (`factorlab-storage` `V2BrokerStorage`), the snapshot scheduler and heartbeat (`components/ingest-broker`), or Gateway login and 2FA (IBC in the Gateway container, [ibkr-gateway-setup.md](../../docs/operations/ibkr-gateway-setup.md)).

## Entry points

- `IBKRBrokerProvider` (`source = "ibkr"`, `pipeline = "ibkr_broker_snapshot"`, `market_code = "USA"`) with `SnapshotConfig` (modes, `client_id=2`, a 1-day executions lookback).
- `IbkrBrokerSnapshot` (`provider = "ibkr"`, `dataset = "broker.snapshot"`) with `IbkrSettings`. `plan` makes one unit per `<mode>:<dataset>` for `positions`, `account_state`, `executions` and `open_orders`.
- Connection helpers: `connect`, `connect_with_retry`, `connected`, `gateway_config`.
- Through the component: `factorlab-ingest-broker snapshot --daemon --run-on-start` (production), or `factorlab-ingest-broker engine run --dataset broker.snapshot --market USA --dry-run`.

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

The component also reads `IBKR_SNAPSHOT_TIMES`, `IBKR_SNAPSHOT_MODES` and `IBKR_SNAPSHOT_LOCK`. Those belong to the component, not the provider. The client-id plan (0 manual, 1 research, 2 snapshot, 3 historical, 4 watchers, 10+ trade engine) is in `ibkr.yaml`.

## Data

- Binding in [bindings.yaml](../../configs/ingestion/bindings.yaml): `{dataset: broker.snapshot, market: USA, provider: ibkr, role: shadow, params: {modes: [paper, live]}}  # -> primary`
- The tables are `broker.positions_snapshot`, `broker.account_state_snapshot`, `broker.executions` and `broker.open_orders_snapshot`. They have no `source` in their key, so the shadow fetches, archives and normalizes but writes nothing.
- `source` is `ibkr`. The legacy provider's `source_channel` is `paper_gateway` or `live_gateway`. The engine source's is `<instance>:paper_gateway` or `<instance>:live_gateway`. Switching paths changes that column in `broker.*` (07 §15.1).
- Captures have `transport="tcp_socket"`, the envelope `{"schema": "ibkr.<kind>.v1", "kind", "mode", "fetched_at", "request", "data"}`, and kinds `portfolio`, `account_values`, `executions` and `open_orders`.

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
