# Interactive Brokers (IBKR) — Read-Only Data & Portfolio Mirror

> Status: `[active — playground validated 2026-09-19]`
> **Role in FactorLab: READ-ONLY, DUAL ACCOUNT.**
> Mirrors **both paper and live IBKR accounts** in parallel to Postgres:
> - **Paper** — forward-testing sandbox for the trade engine (real orders on paper, we score PnL vs backtest)
> - **Live** — real portfolio system of record
>
> **Order placement is out of scope.** Execution — on either account — happens in a separate trade engine (future repo). This module never calls `placeOrder`, `cancelOrder`, or `modifyOrder`.

---

## 1. Role & Architecture

### What IBKR does for FactorLab

Every mirror table below has an `account_mode` column and is written **twice per snapshot cycle** — once for the paper Gateway, once for the live Gateway. Queries filter by `account_mode` (or group by it for compare).

| Concern | IBKR | Postgres mirror | Tagged by `account_mode` |
|---|---|---|---|
| Positions (broker truth) | ✅ Source | `market.ibkr_positions_snapshot` | ✅ |
| Account NAV / margin / buying power | ✅ Source | `market.ibkr_account_values_snapshot` | ✅ |
| Executions / fills | ✅ Source | `market.ibkr_executions` (PK: exec_id) | ✅ |
| Open orders placed by any client | ✅ Source | `market.ibkr_open_orders_snapshot` | ✅ |
| Historical daily bars for validation | ✅ Deep (40+ yrs some names) | `market.candles_daily` with `source='ibkr'` | n/a (data is account-independent) |
| Contract metadata enrichment | ✅ Rich (44 fields) | `ref.securities`, `ref.security_aliases` | n/a |
| **Order placement / cancellation** | ❌ **Never from this codebase** | — | — |

### Forward testing flow

```
Trade engine  ─── proposes signal ───►  places order on PAPER Gateway (port 4002)
                                              │
                                              ▼
                                        IBKR paper fills
                                              │
                                              ▼
FactorLab ingests fills ────────────►  market.ibkr_executions (account_mode='paper')
FactorLab snapshots positions ─────►  market.ibkr_positions_snapshot (account_mode='paper')
                                              │
                                              ▼
                              Score forward-test PnL vs backtest predictions
                              → derived.forward_test_attribution (future)
```

Live flow is identical with `account_mode='live'` and port 4001.

### Why read-only

Two hard rules make this the correct posture:
1. **Data / research code stays away from money.** Same principle as [`feedback_no_schwab_trading.md`](../../memory/feedback_no_schwab_trading.md).
2. **Trade engine is a separate concern.** A different codebase/service will own execution, guards, kill-switches, and audit trails.

### System diagram

```
                              ┌────────────────────┐
                              │  IB Gateway (TWS)  │  <- daily 2FA login
                              │  localhost:4001/2  │
                              └─────────┬──────────┘
                                        │  ib_async socket
                                        │  (readonly=True)
                        ┌───────────────┼────────────────┐
                        │               │                │
                        ▼               ▼                ▼
             FactorLab ingesters   Trade engine    (Ad-hoc research
             (this codebase)       (future repo)    notebooks)
                        │               │                │
                        ▼               ▼                │
              ┌─────────────────────────────────┐        │
              │       Postgres (canonical)      │◀───────┘
              │                                 │
              │  market.ibkr_positions_snapshot │
              │  market.ibkr_account_values_snapshot
              │  market.ibkr_executions         │
              │  market.ibkr_open_orders_snapshot
              │  market.candles_daily (src=ibkr)│
              │  ref.securities / _aliases      │
              └─────────────────────────────────┘
                        ▲
                        │
              Trade engine writes (executes elsewhere,
              persists its own view — e.g. proposed_orders
              in experiments schema, decision logs, etc.)
```

**Contract with the trade engine**: it reads positions/executions from these tables to close its own decision loop; it never expects FactorLab to place its orders.

---

## 2. Connection: IB Gateway (headless) + `ib_async`

### Why Gateway not TWS

| | TWS | **IB Gateway** |
|--|------|----------------|
| GUI | Full trading platform | Minimal connection panel |
| Memory | ~1 GB | ~200 MB |
| Purpose | Manual trading | API-only |

Use Gateway. TWS's trading UI is dead weight for a read-only data pipeline.

### Ports (defaults)

| Mode | TWS port | Gateway port |
|------|----------|--------------|
| Paper | 7497 | **4002** |
| Live | 7496 | **4001** |

Paper account username is prefixed `DU…` (e.g. `DUE375963`). Live has no prefix.

### Daily lifecycle

- Gateway auto-logs-out daily; you re-authenticate each morning via IBKR Mobile 2FA push.
- Weekly forced re-login regardless of auto-restart config.
- **Consequence for deployment**: production keeps the Gateway (and the IBKR login) on the operator's own machine. The VPS `ibkr-snapshot` daemon reaches it read-only over Tailscale, so no IBKR credentials live on the server; snapshots taken while that machine is off are recorded as `partial`/`failed` and alerted. For unattended collection, IBC-managed Gateway containers on the VPS are an optional alternative, but live still needs an IBKR Mobile approval after each weekly re-authentication. Setup: [`docs/operations/ibkr-gateway-setup.md`](../../operations/ibkr-gateway-setup.md).

### Library

**`ib_async` 2.1.0** (community-maintained fork of `ib_insync`). Async, pythonic, sane. Installed:

```bash
"C:/Users/arjd2/.conda/envs/factorlab/python.exe" -m pip install ib_async
```

### Minimal connect

```python
from ib_async import IB
ib = IB()
ib.connect('127.0.0.1', 4002, clientId=1, readonly=True, timeout=15)
```

The `readonly=True` flag causes the API server to **reject any order-mutation call at the socket boundary**. Belt vs suspenders alongside code-level enforcement.

### `clientId` allocation

IBKR forbids two connections sharing a `clientId` **per Gateway**. Live and paper are separate Gateways, so the same ID can be reused across modes.

| ID | Service | Purpose |
|----|---------|---------|
| 0 | (reserved) | Never use — master client sees orders from all others |
| 1 | Playground / ad-hoc research | Interactive sessions, notebooks |
| 2 | `us_portfolio_ibkr_snapshot` | Daily portfolio mirror job |
| 3 | `us_ibkr_historical` | Historical bar sampler |
| 4 | `us_ibkr_openorders_watcher` | Intraday open-orders / execution poll |
| 10+ | Trade engine services | Reserved for the external trade engine |

Since IDs are per-Gateway, `us_portfolio_ibkr_snapshot` uses `clientId=2` when it connects to paper *and* `clientId=2` when it connects to live — sequentially or in parallel, no collision.

Running a notebook (id=1) while the same-Gateway daemon is up (id=2) is fine; two notebooks both on id=1 against the same Gateway is not.

### Gateway API config (one-time)

`Configure → Settings → API → Settings`:
- ✅ Enable ActiveX and Socket Clients
- ✅ **Read-Only API** ← extra safety, matches our contract
- ✅ Download open orders on connection
- Socket port: `4001` (live) or `4002` (paper)
- Master API client ID: blank

`Configure → Settings → API → Trusted IPs`: add `127.0.0.1`. Also check ☑ *Allow connections from localhost only*.

---

## 3. Environment (`.env`)

```
# IBKR — read-only client, dual-account (playground + ingest scripts)
IBKR_HOST=127.0.0.1                  # IBKR_HOST_PAPER / IBKR_HOST_LIVE override per mode
IBKR_PORT_PAPER=4002                 # paper Gateway
IBKR_PORT_LIVE=4001                  # live Gateway (run alongside)
IBKR_CLIENT_ID=1                     # per-service; unique *per Gateway*
IBKR_DEFAULT_MODE=paper              # 'paper' | 'live' — default for ad-hoc scripts
```

Notes:
- Client IDs are per-Gateway; `clientId=2` on paper does NOT collide with `clientId=2` on live (separate sockets, separate servers).
- No `IBKR_TRADING_MODE`, no `IBKR_LIVE_TRADING_ENABLED` — this codebase is read-only regardless of which Gateway it talks to.
- The trade engine will define its own env prefix (e.g. `TRADE_ENGINE_IBKR_*`) so there's no accidental ambient sharing.
- Username / password never in `.env` or the repo. Each Gateway handles auth via its own login window + IBKR Mobile 2FA. Only the optional VPS-hosted Gateways use stored passwords (Cloudflare Secrets Store → Gateway-only tmpfs).

### Two Gateway installations

Both Gateways run simultaneously. Standard setup:
- **Paper Gateway** — logged in with Trading Mode = *Paper Trading*, listens on 4002
- **Live Gateway** — logged in with Trading Mode = *Live Trading*, listens on 4001
- Same install directory works for both (Gateway remembers last profile per login), or use two shortcuts pointing at the same executable with different profile prefs.

Morning ritual: log both Gateways in (2× 2FA push on IBKR Mobile). Ingest scripts iterate over both.

---

## 4. Read-Only Enforcement — belt and braces

Three layers, so a single mistake can't place an order:

1. **Socket-level** — `ib.connect(readonly=True)`. Gateway server refuses `placeOrder` messages from this client.
2. **Adapter-level** — the `IBKRReadOnlyClient` class under `src/factorlab/sources/ibkr/` does not expose `place_order`, `cancel_order`, `modify_order`, or any `MarketOrder` / `LimitOrder` constructor. Nothing to call.
3. **Repo-level** — CI grep guard: any commit that adds `placeOrder(`, `cancelOrder(`, `modifyOrder(`, or `from ib_async import.*Order` to `src/factorlab/` fails the build.

Layer 3 is `tests/sources/ibkr/test_readonly_grep.py`, which scans every module under `src/factorlab/sources/ibkr/` in CI.

---

## 5. Data Endpoints — what IBKR gives us

Playground validation on 2026-09-19 (see `playground/data/ibkr/*.json` for raw payloads).

### 5.1 Historical bars — `reqHistoricalData`

**Signature**
```python
bars = ib.reqHistoricalData(
    contract,
    endDateTime='',                # '' = now; else 'YYYYMMDD HH:MM:SS'
    durationStr='1 Y',             # '1 D', '2 W', '3 M', '5 Y', ...
    barSizeSetting='1 day',        # secs, mins, hours, day, week, month
    whatToShow='TRADES',           # see grid below
    useRTH=True,                   # regular trading hours only
    formatDate=1,                  # 1=string, 2=epoch
)
```

**`whatToShow` grid**

| Value | Content | FactorLab use |
|-------|---------|---------------|
| `TRADES` | OHLC of executed trades + volume + wap + bar_count | Baseline for factor research |
| `ADJUSTED_LAST` | Split/dividend-adjusted (US equities only) | Long-history factor research |
| `MIDPOINT` | Bid-ask midpoint | FX, illiquid options |
| `BID` / `ASK` | One-sided | Spread analysis |
| `BID_ASK` | Both + hi/lo per bar | Microstructure (2× pacing cost) |
| `HISTORICAL_VOLATILITY` | IV proxy | Options work |
| `OPTION_IMPLIED_VOLATILITY` | Same | Options work |

**Bar record shape** (per bar returned):
```
{ date, open, high, low, close, volume, wap, bar_count }
```
where `wap` is per-bar VWAP-like average and `bar_count` is the number of underlying trades aggregated into the bar.

**Head timestamps** (earliest available, US equities sample from probe 06):

| Ticker | TRADES | BID/ASK/MID |
|--------|--------|-------------|
| AAPL | 1980-12-12 | 2004-01-23 |
| IBM | 1980-03-17 | 2004-01-23 |
| SPY | 1993-01-29 | 2004-01-23 |
| NVDA | 1999-01-22 | 2004-01-23 |
| TSLA | 2010-06-29 | 2010-06-29 |
| META | 2012-05-18 | 2012-05-18 |

**Implication**: IBKR beats EODHD for pre-2000 US equity daily history. Use IBKR to backfill deep history for older names; EODHD for bulk / breadth.

**Intraday**: timestamps come with tz offset (e.g. `2026-09-14 09:30:00-04:00` for AAPL — America/New_York). Normalize to UTC at parser boundary.

### 5.2 Contract resolution — `qualifyContracts` + `reqContractDetails`

`qualifyContracts` mutates a `Stock('AAPL', 'SMART', 'USD')` stub in place, filling in `conId` and `primaryExchange`.

`reqContractDetails` returns 44 fields. Notable ones for `ref.securities` population:

| Field | Example | Use |
|-------|---------|-----|
| `contract.conId` | 265598 | Canonical IBKR ID (integer, stable) |
| `contract.symbol` | AAPL | Trading symbol |
| `contract.primaryExchange` | NASDAQ | Listing venue |
| `contract.localSymbol` | AAPL | Broker-side symbol |
| `contract.tradingClass` | NMS | Grouping |
| `longName` | APPLE INC | Legal name |
| `industry` / `category` / `subcategory` | Technology / Computers / Computers | GICS-ish |
| `stockType` | COMMON | ETF / ADR / COMMON |
| `secIdList` | `[TagValue(tag='ISIN', value='US0378331005')]` | **ISIN** for cross-source join |
| `minTick` | 0.01 | Tick size |
| `sizeIncrement` | 0.0001 | Fractional-share support |
| `tradingHours` / `liquidHours` | `20260919:CLOSED;20260920:...` | Session detection |
| `timeZoneId` | US/Eastern | Session normalization |
| `validExchanges` | SMART,NYSE,ARCA,... | Routing options |
| `marketRuleIds` | 4563,4563,... | Tick-rule table refs |

**Caching**: `qualifyContracts` counts against pacing. Cache `symbol → conid` in `ref.security_aliases` and only re-qualify on cache miss.

**Ambiguity**: `BRK B` and other multi-class tickers need `contract.primaryExchange` set before `qualifyContracts` (SMART alone is ambiguous). Similarly for dual-listed names.

### 5.3 Portfolio state — `positions()`, `portfolio()`

- `positions()` — light per-account list: `{account, contract, position, avgCost}`.
- `portfolio()` — richer per-account list: adds `marketPrice`, `marketValue`, `unrealizedPNL`, `realizedPNL`. Requires an active market-data subscription for the underlying to get non-null mkt prices.

Both are streamable — subscribe to `ib.positionsEvent` / `ib.updatePortfolioEvent` for live updates during a session.

### 5.4 Account values — `accountValues()`, `accountSummary()`

- `accountValues()` — **~144 unique tags** per account. Every tag comes in multiple flavors:
  - Bare (`NetLiquidation`) — aggregated in base currency
  - With suffix `-S` (Securities), `-C` (Commodities), `-P` (Paxos/crypto) — per-segment
  - With currency dimension: `USD`, `BASE`, `''`
- `accountSummary()` — clean 24-row subset per account (NetLiquidation, TotalCashValue, BuyingPower, GrossPositionValue, AvailableFunds, ExcessLiquidity, MaintMarginReq, InitMarginReq, DayTradesRemaining, Leverage, etc.). **Prefer this for daily snapshots.**

Full tag list captured in `playground/data/ibkr/account_values_full.json` — reference when designing the snapshot schema.

### 5.5 Executions & fills — `reqExecutions()`

- Default: **today only** (per IBKR).
- `reqExecutions(ExecutionFilter(time="YYYYMMDD HH:MM:SS"))` — window filter.
- Returns `Fill` objects with `contract`, `execution` (exec_id, order_id, perm_id, side, shares, price, exchange, time), and `commissionReport`.
- **`perm_id` is the durable key** — `order_id` resets per Gateway session.

### 5.6 Open orders — `openTrades()`, `reqOpenOrders()`

- With `Download open orders on connection` enabled + master API enabled, `openTrades()` shows orders placed by any client (including the trade engine).
- Read-only from our side — we just observe them for reconciliation.

### 5.7 What we do NOT use

- Market-data streaming (`reqMktData`) — Schwab is cheaper for real-time US quotes; skip unless we need IBKR's direct feed.
- News (`reqNewsProviders` etc.) — Briefing.com articles; not structured; separate econ-calendar sourcing decision (see below).
- Economic calendar — **IBKR API does not expose it as structured data.** Use EODHD's `/economic-events` endpoint or FRED for macro instead.

---

## 6. Pacing & Rate Limits — the IBKR speed bump

Official limits (verified with IBKR docs, 2026):

| Rule | Limit | Error |
|------|-------|-------|
| Global cap | **60 requests per any 10-minute window** | 162 |
| Identical-request cooldown | **15 sec** between identical requests | 162 |
| Same contract burst | Max **6 requests** for same contract/exchange/tickType in 2 sec | 162 |
| Concurrent open historical requests | Max **50 simultaneous** | 162 |
| BID_ASK double-count | Each request counts as **2** toward all limits | — |

**No way to raise these** — they apply globally, per account, per client.

### Design implications

- Client-side rolling-window rate limiter with cap = **50 req / 600 s** (headroom under 60).
- Async semaphore on per-contract concurrency = 1.
- Exponential backoff on Error 162.
- For long backfills: **prefer EODHD** for bulk daily data. IBKR only for:
  - Deep-history names EODHD lacks
  - Validation samples (N random tickers/day cross-check)
  - Intraday backfill (where EODHD is weaker)

If a request stalls for minutes, cancel with `ib.cancelHistoricalData()` — abandoning it wastes both concurrency and throttle budget.

---

## 7. Postgres Schema (proposed — pending migration)

> **Superseded.** The implemented sink is ClickHouse `broker.positions_snapshot`,
> `broker.account_state_snapshot`, `broker.executions` and
> `broker.open_orders_snapshot` (Wave 7, `sql/clickhouse/v2/wave_07_schema.sql`),
> written by `factorlab.storage.v2_broker.V2BrokerStorage`. Every snapshot is a
> `meta.ingestion_runs` row; every Gateway response is archived to `raw.archive`
> (`transport='tcp_socket'`) and rows are normalized from those archived bytes.
> The Postgres design below is kept for history only.

### `market.ibkr_positions_snapshot`
```sql
snapshot_at        timestamptz NOT NULL,   -- UTC snapshot time
account            text        NOT NULL,   -- 'DUE375963' (paper) or live acct id
account_mode       text        NOT NULL,   -- 'paper' | 'live'
contract_conid     bigint      NOT NULL,   -- IBKR conId (canonical)
security_id        uuid        REFERENCES ref.securities(security_id),
symbol             text        NOT NULL,
sec_type           text        NOT NULL,   -- 'STK', 'FUT', 'OPT', ...
currency           char(3)     NOT NULL,
position           numeric(18,4) NOT NULL, -- can be negative (short)
avg_cost           numeric(18,6),
market_price       numeric(18,6),          -- from portfolio() (if mkt-data sub'd)
market_value       numeric(18,4),
unrealized_pnl     numeric(18,4),
realized_pnl       numeric(18,4),
PRIMARY KEY (snapshot_at, account, contract_conid)
);
```
Append-only. Timescale hypertable on `snapshot_at` at 1-week chunks.

### `market.ibkr_account_values_snapshot`
```sql
snapshot_at        timestamptz NOT NULL,
account            text        NOT NULL,
account_mode       text        NOT NULL,
tag                text        NOT NULL,   -- 'NetLiquidation', 'BuyingPower', ...
segment            text        NOT NULL,   -- '' (agg) | 'S' | 'C' | 'P'
currency           char(4)     NOT NULL,   -- 'USD', 'BASE', 'NONE'
value              text        NOT NULL,   -- IBKR returns strings; cast in queries
PRIMARY KEY (snapshot_at, account, tag, segment, currency)
);
```

### `market.ibkr_executions`
```sql
exec_id            text        PRIMARY KEY,       -- IBKR exec id (immutable)
account            text        NOT NULL,
account_mode       text        NOT NULL,
order_id           int         NOT NULL,          -- per-session
perm_id            bigint      NOT NULL,          -- durable across sessions
contract_conid     bigint      NOT NULL,
security_id        uuid        REFERENCES ref.securities(security_id),
symbol             text        NOT NULL,
sec_type           text        NOT NULL,
currency           char(3)     NOT NULL,
side               text        NOT NULL,          -- 'BUY' | 'SELL'
quantity           numeric(18,4) NOT NULL,
price              numeric(18,6) NOT NULL,
exchange           text        NOT NULL,
exec_time          timestamptz NOT NULL,          -- UTC
commission         numeric(12,4),
commission_ccy     char(3),
realized_pnl       numeric(18,4),
raw_payload_id     uuid,
ingested_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON market.ibkr_executions (exec_time DESC);
CREATE INDEX ON market.ibkr_executions (account, exec_time DESC);
CREATE INDEX ON market.ibkr_executions (contract_conid, exec_time DESC);
```
Append-only. `exec_id` is the immutable PK; re-fetching the same execution is idempotent.

### `market.ibkr_open_orders_snapshot`
```sql
snapshot_at        timestamptz NOT NULL,
account            text        NOT NULL,
account_mode       text        NOT NULL,
order_id           int         NOT NULL,
perm_id            bigint      NOT NULL,
contract_conid     bigint      NOT NULL,
symbol             text        NOT NULL,
sec_type           text        NOT NULL,
side               text        NOT NULL,
order_type         text        NOT NULL,          -- 'LMT', 'MKT', 'MOC', ...
quantity           numeric(18,4) NOT NULL,
filled_quantity    numeric(18,4),
remaining_quantity numeric(18,4),
limit_price        numeric(18,6),
aux_price          numeric(18,6),
status             text        NOT NULL,          -- 'Submitted', 'PreSubmitted', ...
placed_by_client   int,                           -- API clientId that placed it
PRIMARY KEY (snapshot_at, account, perm_id)
);
```

### `ref.security_aliases` (existing — add IBKR vendor rows)
```sql
-- already exists; we add rows with vendor='ibkr', vendor_id = str(conid)
```

---

## 8. Ingest Pipeline

### Cadence

| Job | Frequency | Client ID | What |
|-----|-----------|-----------|------|
| `us_portfolio_ibkr_snapshot` (morning) | Daily 06:00 ET | 2 | Snapshot positions + account values pre-open |
| `us_portfolio_ibkr_snapshot` (EOD) | Daily 16:30 ET | 2 | Snapshot positions + account values post-close |
| `us_ibkr_executions_pull` | Every 15 min during session | 4 | Pull new fills into `market.ibkr_executions` (idempotent by exec_id) |
| `us_ibkr_openorders_watcher` | Every 5 min during session | 4 | Snapshot open orders (for observing trade engine activity) |
| `us_ibkr_historical` | Manual / on demand | 3 | Deep history backfill for named tickers |
| `us_ibkr_validation_sample` | Daily 17:00 ET | 3 | N random tickers cross-checked vs Schwab/EODHD |

Task Scheduler registration is out of scope per [`feedback_no_task_scheduler.md`](../../memory/feedback_no_task_scheduler.md) — scripts describe cadence; user owns install.

### Idempotence

- Positions / account values: PK includes `snapshot_at` — natural append.
- Executions: PK is `exec_id` — safe to re-pull the window without dupes.
- Open orders: PK includes `snapshot_at` + `perm_id` — natural append.
- Historical bars: `(security_id, trade_date, source)` — upsert by (conid, date, 'ibkr').

### Reconciliation

Every morning before the trade engine starts:
1. Pull `positions()` from IBKR
2. Compare to latest `market.ibkr_positions_snapshot` for same `account`
3. If mismatch → write drift record + emit alert; **trade engine should refuse to start on drift**
4. If match → snapshot both and proceed

The drift check is FactorLab's job. The **response** to drift (halt trading, escalate, etc.) is the trade engine's job.

---

## 9. Downstream Consumer Contract (trade engine)

The trade engine is a separate repo/service. Its contract with FactorLab:

**Reads (from our Postgres):**
- `market.ibkr_positions_snapshot` — current & historical positions
- `market.ibkr_account_values_snapshot` — NAV, buying power, margin available
- `market.ibkr_executions` — fills for its own attribution
- `market.candles_daily` (any source) — signal inputs
- `derived.*` — signals, factor scores
- `ref.securities` / `ref.security_aliases` — for contract mapping

**Writes (to our Postgres):**
- `experiments.proposed_orders` (or trade-engine schema — TBD when built) — its own intent log
- Own decision logs

**Does NOT expect FactorLab to:**
- Place any order
- Cancel any order
- Watch its intraday risk in real time (its own concern)

**FactorLab guarantees to the trade engine:**
- Positions/executions in the mirror are ≤ 15 minutes stale during session
- Reconciliation drift is detected and surfaced via a queryable table (`derived.ibkr_reconciliation_drift` — TBD)
- Contract `conId`s in `ref.security_aliases` are stable across sessions

---

## 10. Src/ module layout (proposed)

> **Implemented layout** (built on `factorlab.shared.ingest.provider`):
> `client.py` (read-only connect, per-mode endpoint, bounded retry),
> `capture.py` (Gateway responses -> archivable JSON `RawCapture`),
> `normalize.py` (pure payload -> `shapes.py` rows), `provider.py`
> (`IBKRBrokerProvider`, one run unit per mode and dataset) and
> `storage/v2_broker.py` (identity, enrichment, lineage). `portfolio.py`,
> `executions.py` and `open_orders.py` are capture + normalize conveniences
> without archiving, for research use.

```
src/factorlab/sources/ibkr/
├── __init__.py
├── client.py              # IBKRReadOnlyClient (connect + lifecycle)
├── contracts.py           # qualify + cache to ref.security_aliases
├── portfolio.py           # snapshot positions + account values -> market.ibkr_*
├── executions.py          # pull fills + upsert market.ibkr_executions
├── open_orders.py         # snapshot market.ibkr_open_orders_snapshot
├── historical.py          # rate-limited daily-bar puller (validation only)
├── pacing.py              # rolling-window rate limiter, error-162 backoff
└── errors.py              # IBKRError, IBKRPacingError, IBKRReadOnlyViolation
```

```
scripts/us/ibkr/
├── us_portfolio_ibkr_snapshot.py    # daily morning + EOD snapshotter
├── us_ibkr_executions_pull.py       # 15-min executions poll
├── us_ibkr_openorders_watcher.py    # 5-min open-orders snapshot
├── us_ibkr_historical.py            # CLI: backfill history for a ticker list
└── us_ibkr_validation_sample.py     # cross-check N tickers vs Schwab/EODHD
```

Naming per [`docs/developments/008-script-naming-india-historical.md`](../developments/008-script-naming-india-historical.md).

---

## 11. Edge cases & gotchas

- **Connection stability** — Gateway hangs occasionally. Wrap operations with timeout + reconnect. Listen to `ib.disconnectedEvent`.
- **SMART vs pinned exchange** — SMART is fine for research; for corporate-action or venue-specific studies pin the exchange.
- **Time zone trap** — IBKR returns server-local timestamps (`US/Eastern` for US contracts, exchange-local for others). Normalize to UTC in the parser.
- **Paper vs live account values** — paper resets balance to $1M every ~3 months. Don't build long-running PnL dashboards off paper.
- **`perm_id` vs `order_id`** — always join executions on `perm_id`. `order_id` resets per session.
- **`exec_id` collisions** — none observed; IBKR guarantees uniqueness.
- **Delisted / rebranded symbols** — `qualifyContracts` may still resolve stale symbols to old `conid`s. On corporate actions, re-qualify.
- **Extended-hours bars** — `useRTH=False` includes pre (04:00-09:30) and post (16:00-20:00) US sessions. Volume distribution is heavily skewed to RTH.
- **`ADJUSTED_LAST` semantics** — adjusts for splits + regular dividends. Does NOT adjust for special dividends or spinoffs in all cases — validate against known corporate actions.
- **Bar count in daily bars** — `bar_count` is the number of underlying trades. Useful liquidity proxy.
- **Segment suffixes in account values** — `-S` (Securities), `-C` (Commodities/Futures), `-P` (Paxos/crypto). Query base tag (no suffix) for aggregated.

---

## 12. Operational rituals

**Morning:**
- Gateway logged in on paper (or live), port open
- `ib.reqCurrentTime()` round-trip < 100 ms
- Snapshot pre-open positions + account values
- Reconciliation: previous EOD vs current AM positions match

**During session (every 5-15 min):**
- Pull new executions
- Snapshot open orders

**End of day:**
- Final snapshot positions + account values
- Run validation-sample bar cross-check vs Schwab/EODHD
- Nightly digest (positions delta, new fills, validation results)

**Weekly:**
- Verify Gateway auto-restart config still holds
- Manual PnL reconcile: our tables ↔ IBKR PortfolioAnalyst report

---

## 13. Playground (reference implementations)

Working probes under `playground/explore/ibkr/`:

| Script | Purpose | Sample data written |
|--------|---------|---------------------|
| `_client.py` | Shared connect/disconnect helper | — |
| `01_connect.py` | Smoke test: server time, accounts, positions | — |
| `02_historical_bars.py` | Bar shape, adjustment, intraday | `hist_aapl_*.json` |
| `03_contract_resolve.py` | Ticker→conid + ContractDetails | `contracts_qualified.json`, `contract_details_aapl.json` |
| `04_executions.py` | reqExecutions + open trades | `executions_today.json`, `fills_window_30d.json`, `open_trades.json` |
| `05_account_values.py` | Full accountValues + summary + portfolio | `account_values_full.json`, `account_summary_full.json`, `portfolio_full.json` |
| `06_head_timestamp.py` | Earliest data per contract/whatToShow | `head_timestamps.json` |

Sample payloads are the authoritative reference when designing schema / adapter shapes. Do not migrate a table until the sample JSON has been re-run against the account you'll actually use.

---

## 14. Open decisions

- [ ] **Which account for portfolio SoR** — paper (`DUE375963`) fine for dev; live account required for real portfolio tracking. Switch when trade engine goes live.
- [ ] **Historical backfill scope** — which universe do we deep-backfill via IBKR (pre-2000 US names EODHD doesn't cover)? Start with S&P 500 members ever, or narrower?
- [ ] **Validation sampling policy** — N per day? Random or stratified by liquidity?
- [x] **Repo-level CI grep guard** — `tests/sources/ibkr/test_readonly_grep.py`.
- [ ] **Trade engine repo boot** — separate GitHub repo, or a service module inside FactorLab with a hard boundary? (Recommendation: separate repo — enforces the read-only rule structurally.)
- [x] **Deployment target for Gateway** — the operator's machine, reached from the VPS over Tailscale; VPS-hosted IBC containers remain optional (`ibkr-gateway` profile). See [`docs/operations/ibkr-gateway-setup.md`](../../operations/ibkr-gateway-setup.md).

---

## 15. Reference links

| Resource | URL |
|----------|-----|
| Official TWS API docs | https://interactivebrokers.github.io/tws-api/ |
| `ib_async` fork | https://github.com/ib-api-reloaded/ib_async |
| IBC (auto-login wrapper) | https://github.com/IbcAlpha/IBC |
| Gateway download | https://www.interactivebrokers.com/en/trading/ibgateway-stable.php |
| Market data subs (non-pro pricing) | https://www.interactivebrokers.com/en/index.php?f=14193 |
| Pacing limits (official) | https://interactivebrokers.github.io/tws-api/historical_limitations.html |
