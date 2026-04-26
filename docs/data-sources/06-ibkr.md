# Interactive Brokers (IBKR) — Portfolio, Paper Trading, Data, Execution

> Status: `[design]`
> **Special role:** IBKR is the **portfolio system of record** for FactorLab. Positions, fills, NAV, and PnL live in IBKR (paper today, live later). Postgres mirrors what IBKR says — IBKR is canonical.

## Why IBKR is special

Most data sources in FactorLab are read-only feeds. IBKR is different — it is simultaneously:

1. **Portfolio store** — the source of truth for what we own, at what cost, across what accounts. The Postgres tables under `market.ibkr_positions_snapshot` and `market.ibkr_executions` are *mirrors* of broker state, not the master.
2. **Execution venue** — the only place orders actually get placed.
3. **Paper-trading sandbox** — full account simulating real fills, used as a deployment dry-run for live signals.
4. **Multi-asset, multi-region market data** (US equities/options/futures, European, much of APAC) — once you pay for the right subscriptions.

This dual role (data + portfolio + execution) means IBKR sits at the integration-test boundary of the platform. Every signal that would go live must round-trip through IBKR paper first. The day backtest predictions and IBKR paper fills systematically agree, the platform is ready for live capital.

---

## 1. Auth & Connection

IBKR's API model is unusual: you do **not** call a REST endpoint over the open internet. Instead, you run **TWS** (Trader Workstation) or **IB Gateway** locally, log in interactively, and connect to it via a TCP socket on `localhost`.

### TWS vs IB Gateway

| | TWS | IB Gateway |
|--|------|------------|
| GUI | Full trading platform | Minimal connection-status only |
| Memory | ~1 GB | ~200 MB |
| Use case | Manual trading + API | API-only, headless servers |
| Auth | Same login | Same login |

**For FactorLab: use IB Gateway.** TWS is overkill for headless data/execution.

### Paper vs Live ports (default)

| Mode | TWS port | Gateway port |
|------|----------|--------------|
| Paper | 7497 | 4002 |
| Live | 7496 | 4001 |

Each account (paper, live) gets its own login. **Paper account credentials are different from live** — IBKR will issue them once you enable paper trading in Account Management. They share market-data subscriptions only if the live account is funded.

### Daily login & 2FA

- IB Gateway / TWS auto-logs-out daily (security policy). You log in each morning.
- 2FA via IBKR Mobile is the default; can be replaced with read-only API tokens for some workflows but **not** for the full TWS API.
- Auto-restart can be configured (`config/jts.ini`) but you'll still hit a weekly forced-relogin. Plan for it.

### Library choice

| Library | Pros | Cons |
|---------|------|------|
| **`ib_async`** (maintained fork of `ib_insync`) | Async, pythonic, sane | Original maintainer stepped back; community fork actively maintained |
| **`ibapi`** (official) | Authoritative | Callback-based, painful, low-level |
| **`ib-gateway-docker`** + `ib_async` | Headless deployment | Docker image is community-maintained |

**Recommendation: `ib_async`.** API surface is identical to the established `ib_insync` you'll find in tutorials.

```python
from ib_async import IB, Stock
ib = IB()
ib.connect('127.0.0.1', 4002, clientId=1)   # 4002 = Gateway paper
contract = Stock('AAPL', 'SMART', 'USD')
ib.qualifyContracts(contract)
bars = ib.reqHistoricalData(
    contract,
    endDateTime='',
    durationStr='1 Y',
    barSizeSetting='1 day',
    whatToShow='TRADES',
    useRTH=True,
)
```

### `clientId` discipline

Every connection needs a unique `clientId`. The connection that has `clientId=0` is the "master" — it sees orders placed by all other clients. Reserve `0` for a monitoring/admin connection; assign deterministic IDs to your services:

```
0   = master / monitoring
1   = research notebooks
2   = market-data ingester
3   = signal generator (production)
4   = order manager
5   = portfolio reconciler
```

Two connections with the same `clientId` cannot coexist. This will bite you when running a notebook while a daemon is up.

### `.env`

```
IBKR_HOST=127.0.0.1
IBKR_PORT_PAPER=4002
IBKR_PORT_LIVE=4001
IBKR_CLIENT_ID_RESEARCH=1
IBKR_CLIENT_ID_INGESTER=2
IBKR_CLIENT_ID_SIGNAL=3
IBKR_CLIENT_ID_ORDER=4
IBKR_CLIENT_ID_RECONCILER=5
IBKR_USERNAME=...           # only if using IBC for auto-login
IBKR_PASSWORD=...           # only if using IBC for auto-login
IBKR_TRADING_MODE=paper     # 'paper' or 'live'
IBKR_LIVE_TRADING_ENABLED=0 # hard guard for any real-money order; flip to 1 manually
```

For headless auto-relogin, the community tool **IBC** (Interactive Brokers Controller, https://github.com/IbcAlpha/IBC) wraps Gateway with auto-login. Required for production deployment on Railway-equivalent infra.

---

## 2. Portfolio System of Record

Treat IBKR (whichever account is active) as the master. Postgres tables exist only to:

- **Mirror** broker state for fast querying and joining with research data
- **Audit-log** every snapshot we ever took (positions drift; we want history)
- **Reconcile** proposed signals vs. realized fills

### Daily portfolio sync (mandatory)

```python
# pseudocode — runs at end of US session
ib.connect(..., clientId=IBKR_CLIENT_ID_RECONCILER)

# 1) Snapshot positions
positions = ib.positions()                  # {account: [Position, ...]}
# 2) Pull executions (today's fills)
executions = ib.reqExecutions()             # ExecutionFilter() default = today
# 3) Account values (NAV, cash, margin)
account_values = ib.accountValues()
# 4) Open orders not yet filled
open_orders = ib.openOrders()

# Persist all four to Postgres with snapshot_at = now()
```

### Reconciliation rule
Every morning before signal generation:
```
positions_in_postgres == positions_in_ibkr   # MUST hold
```
If they diverge, **halt signal generation** and alert. A diverging portfolio means the previous day's run had an unrecorded effect (cancelled order that filled, partial fill, dividend received, etc.). Investigate before continuing.

### What lives where

| Concept | Master | Postgres mirror |
|---------|--------|-----------------|
| Open positions | IBKR account | `market.ibkr_positions_snapshot` (daily) |
| Executions / fills | IBKR | `market.ibkr_executions` (append-only) |
| Open orders | IBKR | `market.ibkr_open_orders_snapshot` (intra-day) |
| NAV / cash / margin | IBKR | `market.ibkr_account_values_snapshot` |
| Proposed signals | FactorLab signal generator | `experiments.proposed_orders` |
| Slippage = proposed − filled | computed | `derived.slippage_attribution` |

The Postgres tables are append-only history. To know "current" position, query the latest snapshot or, when stakes are high, hit IBKR live.

---

## 3. Market Data Coverage & Subscriptions

This is the part that surprises most people: **IBKR data is not free**, and the matrix is complicated.

### Subscription model
- Most non-US data requires a **monthly market-data subscription** (e.g. NYSE depth ~$1.50/mo, Nasdaq TotalView ~$70/mo, Eurex level-2 separate, etc.)
- Each subscription tier unlocks both *streaming* and *historical* data for that segment
- Subscriptions are per-account; paper account inherits if the live account is funded
- The Account Management → Settings → User Settings → Market Data Subscriptions page lists what you currently have

**Action item before depending on any market: confirm the subscription is active.** API will silently return "No data available" or hit you with `Error 354: requested market data is not subscribed`.

### Free / included with most accounts
- Delayed (15-min) data on most US exchanges via `marketDataType=3`
- US OPRA snapshot (delayed)
- IBKR's own consolidated tape (limited)

### Paid (relevant for FactorLab US Phase 1)
| Subscription | ~Cost/mo | What you get |
|--------------|----------|--------------|
| US Securities Snapshot and Futures Value Bundle | waivable if commissions ≥ $30/mo | Real-time NBBO US equities + futures |
| US Equity & Options Add-On Streaming Bundle | $4.50 | Real-time level-1 streaming |
| Nasdaq TotalView | ~$70 | Level-2 depth |
| NYSE OpenBook | ~$50 | NYSE depth |

For research with daily bars, the snapshot bundle is sufficient and often free if you trade enough. For intraday tick-level work, expect real costs.

### Non-US (relevant later)
- India: IBKR India is a separate entity; **not all symbols cleanly accessible** from a US account. NSE data subscription possible but limited to specific contract universes.
- Europe: per-exchange subscriptions (Deutsche Börse, LSE, Euronext, etc.)
- APAC: per-exchange (HKEX, ASX, JPX, etc.)

For India in particular, **Upstox is a better data source than IBKR** because of cleaner instrument coverage and no subscription gating. Use IBKR for non-US developed markets in Phase 6+.

### Snapshot vs streaming via API

```python
# Streaming (top of book) — requires subscription
ticker = ib.reqMktData(contract, '', False, False)
# ticker.bid, ticker.ask update asynchronously

# Snapshot (one-shot) — cheaper in pacing terms
ib.reqMktData(contract, '', True, False)

# Delayed (free)
ib.reqMarketDataType(3)   # 1=live, 2=frozen, 3=delayed, 4=delayed-frozen
```

Use snapshots for batch operations (e.g., refreshing 500 quotes once); subscribe to streams only for the names you actively trade.

---

## 4. Historical Data — `reqHistoricalData`

The most useful endpoint for FactorLab, and the one with the **most aggressive pacing rules** in the API world.

### Signature

```python
bars = ib.reqHistoricalData(
    contract,
    endDateTime='',                    # '' = now; or 'YYYYMMDD HH:MM:SS'
    durationStr='1 Y',                 # '1 D', '2 W', '3 M', '5 Y', etc.
    barSizeSetting='1 day',            # see size grid below
    whatToShow='TRADES',               # see whatToShow grid
    useRTH=True,                       # regular trading hours only
    formatDate=1,                      # 1 = string, 2 = epoch
)
```

### Bar sizes

`1 secs`, `5 secs`, `10 secs`, `15 secs`, `30 secs`, `1 min`, `2 mins`, `3 mins`, `5 mins`, `10 mins`, `15 mins`, `20 mins`, `30 mins`, `1 hour`, `2 hours`, `3 hours`, `4 hours`, `8 hours`, `1 day`, `1 week`, `1 month`.

### `whatToShow` (data type)

| Value | What |
|-------|------|
| `TRADES` | OHLC of executed trades |
| `MIDPOINT` | Bid-ask midpoint (no trades — useful for FX, illiquid options) |
| `BID` / `ASK` | One-sided |
| `BID_ASK` | Both, plus min/max in bar |
| `HISTORICAL_VOLATILITY` | IV proxies (options-related contracts) |
| `OPTION_IMPLIED_VOLATILITY` | Same |
| `ADJUSTED_LAST` | Split/dividend-adjusted (US equities only) |

For factor research on equities, use `TRADES` + `useRTH=True` for daily bars and a separate `ADJUSTED_LAST` pull when you need clean adjusted history.

### Pacing limits — the IBKR speed bump

These are notoriously strict. The official rules:

> 1. **No more than 60 historical-data requests in any 10-minute period.**
> 2. **No more than 6 identical historical-data requests for the same contract/exchange/tickType within 2 seconds.**
> 3. **No more than 2 simultaneous historical-data requests for the same contract.**
> 4. Bars of <30s or unusual aggregation may have additional limits.

If you violate, the response is `Error 162: Historical Market Data Service error message: Pacing violation`. The connection isn't dropped, but the request fails. Persistent abuse can flag your account.

**Practical implication:** building a 5-year daily-bar warehouse for 500 US stocks = 500 requests = 50 minutes minimum, just on pacing. Adapter design must respect this:

```python
# pseudocode
class IBKRHistoricalRateLimiter:
    # rolling window, max 50 requests per 600s (leave headroom under the 60 limit)
    # async semaphore on per-contract concurrency = 1
    # exponential backoff on Error 162
```

For long-history backfills, **prefer EODHD for US daily** and use IBKR only for intraday or non-US where EODHD doesn't reach.

### Date-range edge cases
- `endDateTime=''` resolves to "now" in the **server's timezone** (US/Eastern for US contracts). Always pass explicit timestamps for reproducibility.
- Maximum lookback varies by `barSizeSetting`. 1-second bars are limited to ~1800 bars per request; daily bars allow `15 Y`.
- Earliest available data for a contract: `ib.reqHeadTimeStamp(contract, 'TRADES', useRTH=True, formatDate=1)`. Use this to plan backfill chunks.

### Storage path
IBKR daily bars → same `market.price_bars_daily` table as EODHD, with `source='ibkr'`. Multi-source rows for the same `(security_id, trade_date)` are allowed; the `as_of_time` distinguishes them, and downstream queries pick a preferred source per region.

IBKR minute/tick bars → separate partitioned tables (`market.price_bars_minute`, `market.tick_data`).

---

## 5. Orders, Paper Trading, and Execution

### Why paper trading matters in a research platform

Paper trading is **not** a stand-in for execution research — IBKR's paper fills are simulated and optimistic. But it is the right tool for:

1. **Pipeline integration testing** — does your daily signal job actually translate signals into orders, against a realistic API surface?
2. **Order-type validation** — your VWAP order behaves correctly, your TWAP slices, etc.
3. **Cost-model calibration** — you can compare your backtester's predicted fills with paper fills, find systematic biases.
4. **Pre-deployment dry-run** — same code, same connection logic, just port 4002 instead of 4001.

Treat paper as the **system-test environment** for the live signal pipeline. Every daily signal that would be sent live in production is also sent to paper, and paper fills are recorded next to backtest predictions.

### Placing an order

```python
from ib_async import MarketOrder, LimitOrder, StopOrder

contract = Stock('AAPL', 'SMART', 'USD')
ib.qualifyContracts(contract)

# Market order
order = MarketOrder('BUY', 100)
trade = ib.placeOrder(contract, order)

# Limit order
order = LimitOrder('BUY', 100, 175.50)
trade = ib.placeOrder(contract, order)

# Wait for fill (or cancel)
ib.sleep(5)
print(trade.orderStatus.status, trade.fills)
```

`trade` is an `ib_async.Trade` object — it stays subscribed and updates as state changes.

### Useful order types
| Type | Use |
|------|-----|
| `MarketOrder` | Get done now (paper fills at midpoint approx) |
| `LimitOrder` | Default for research-driven entries |
| `StopOrder` / `StopLimitOrder` | Risk control |
| `MidPriceOrder` | Algo aiming for midpoint (US equities/options) |
| `MOC` / `MOO` | Market on close / open |
| `LOC` / `LOO` | Limit on close / open |
| Algos: `Adaptive`, `TWAP`, `VWAP`, `IS` | Native IBKR algos via `algoStrategy` field |

For factor strategies trading at close: **use `MOC` (market on close)**. Native exchange order, gets the official close print.

### Position & portfolio queries

```python
positions = ib.positions()           # list of Position objects
portfolio = ib.portfolio()           # PnL, average cost, etc.
account = ib.accountValues()         # cash, NAV, margin
```

Positions update live; subscribe to `ib.positionsEvent` for callbacks.

### Paper account quirks
- **Fills are heuristic**, not real-market: IBKR uses real bid/ask but doesn't simulate queue position, partial fills, or slippage realistically. Treat paper PnL as optimistic.
- **Resets quarterly**: IBKR resets paper account balance to $1M every ~3 months. Don't build long-running PnL dashboards on it.
- **Some order types don't work in paper** (specific exchange algos, complex spreads). Test before depending.
- **Market hours matter** — paper still requires a real session for fills. After-hours orders queue.

### Production code path
```python
mode = os.getenv('IBKR_TRADING_MODE')      # 'paper' or 'live'
port = int(os.getenv(f'IBKR_PORT_{mode.upper()}'))
ib.connect('127.0.0.1', port, clientId=int(os.getenv('IBKR_CLIENT_ID_ORDER')))

# Hard guard before any live-money order
if mode == 'live' and os.getenv('IBKR_LIVE_TRADING_ENABLED') != '1':
    raise RuntimeError("Live trading requires IBKR_LIVE_TRADING_ENABLED=1")
```

The same `place_orders(signals)` function runs in both. The only difference is the port and the explicit affirmative consent before any real-money trade.

---

## Schema mapping — IBKR-specific tables

### `market.ibkr_positions_snapshot`
```sql
snapshot_at       timestamptz NOT NULL
account           text NOT NULL                -- IBKR account id (e.g. 'DU1234567')
account_mode      text NOT NULL                -- 'paper' or 'live'
contract_conid    int NOT NULL                 -- IBKR's contract id
security_id       uuid REFERENCES ref.securities
position          numeric(18,4) NOT NULL
avg_cost          numeric(18,6)
market_value      numeric(18,4)
unrealized_pnl    numeric(18,4)
realized_pnl_today numeric(18,4)
PRIMARY KEY (snapshot_at, account, contract_conid)
```

### `market.ibkr_executions`
```sql
exec_id           text PRIMARY KEY             -- IBKR's exec id (immutable)
account           text NOT NULL
account_mode      text NOT NULL
order_id          int NOT NULL
perm_id           int NOT NULL                 -- IBKR's permanent id (survives restarts)
proposed_order_id uuid                         -- FK to experiments.proposed_orders, if any
contract_conid    int NOT NULL
security_id       uuid REFERENCES ref.securities
side              text NOT NULL                -- 'BUY' / 'SELL'
quantity          numeric(18,4) NOT NULL
price             numeric(18,6) NOT NULL
exchange          text NOT NULL
exec_time         timestamptz NOT NULL
commission        numeric(12,4)
realized_pnl      numeric(18,4)
raw_payload_id    uuid                         -- FK to raw archive
```

### `market.ibkr_account_values_snapshot`
```sql
snapshot_at       timestamptz NOT NULL
account           text NOT NULL
account_mode      text NOT NULL
key               text NOT NULL                -- 'NetLiquidation', 'CashBalance', etc.
value             text NOT NULL                -- IBKR returns string-typed; cast in queries
currency          char(3) NOT NULL
PRIMARY KEY (snapshot_at, account, key, currency)
```

### `experiments.proposed_orders` (the FactorLab side of the bridge)
```sql
proposed_order_id uuid PRIMARY KEY DEFAULT gen_random_uuid()
run_id            uuid REFERENCES experiments.runs
proposed_at       timestamptz NOT NULL
security_id       uuid REFERENCES ref.securities
side              text NOT NULL
quantity          numeric(18,4) NOT NULL
order_type        text NOT NULL                -- 'MOC', 'LMT', etc.
limit_price       numeric(18,6)
status            text NOT NULL                -- 'pending', 'sent', 'filled', 'cancelled', 'rejected'
ibkr_order_id     int                          -- once placed
ibkr_perm_id      int                          -- once placed
notes             text
```

### Reference: contract resolution
IBKR uses an internal `conid` (contract ID, integer) as the canonical identifier. Map to `ref.security_aliases` with `vendor='ibkr'`, `vendor_id=str(conid)`. Resolve once via `ib.qualifyContracts()` and cache aggressively — `qualifyContracts` is itself rate-limited.

---

## Pipeline (IBKR-specific)

```
boot:
    IB Gateway running, logged in, port open
    health-check: ib.reqCurrentTime()

morning (pre-market):
    1. snapshot positions  → market.ibkr_positions_snapshot
    2. snapshot account values → market.ibkr_account_values_snapshot
    3. reconcile positions vs end-of-yesterday snapshot
    4. if mismatch → halt, alert, investigate

on signal generation (post-close or pre-market depending on strategy):
    1. compute signals
    2. translate signals → orders (sized, with limit prices, etc.)
    3. log proposed orders to experiments.proposed_orders (status='pending')
    4. (paper or live) ib.placeOrder for each → status='sent', record ibkr_order_id
    5. wait for fills with timeout
    6. record actual fills → market.ibkr_executions (exec_id PK), update proposed status
    7. compare proposed vs filled → derived.slippage_attribution

end of day:
    1. final snapshot positions + account values
    2. compute daily PnL attribution
    3. nightly digest: signals proposed, signals filled, slippage, account drift
```

---

## Edge cases & gotchas

- **Connection stability** — Gateway hangs occasionally. Wrap operations with timeout + reconnect logic. `ib.disconnectedEvent` is your friend.
- **`SMART` routing vs direct exchange** — `SMART` lets IBKR pick the venue. Fine for research; for backtests-of-execution-quality you may want to pin specific exchanges.
- **Currency conversions** — orders in non-USD denominations require either an FX trade first or auto-conversion on settlement. Affects PnL accounting.
- **Short borrow** — IBKR's `reqContractDetails` includes shortable status, but locate vs already-borrowed is opaque. For shorting strategies, expect surprises.
- **Corporate actions handling** — IBKR adjusts positions automatically (splits, mergers). Your bookkeeping must too. Don't naïvely store positions as immutable rows.
- **PaperTradingAccountReset** — listen for the `accountResetEvent` (or check NAV resetting to $1M) so you don't spuriously alarm.
- **Order ID collisions** — `ib.client.getReqId()` for any user-facing request id; never reuse.
- **Time zone trap** — IBKR returns timestamps in **server time** (US/Eastern) by default for US contracts, exchange-local for others. Always normalize to UTC at parser boundary.
- **Subscription gaps** — `Error 354` (no subscription) and `Error 162` (pacing) are the two errors you will see most. Build observable counters for both.
- **`perm_id` vs `order_id`** — `order_id` resets per session, `perm_id` is durable. Always join executions on `perm_id`.

---

## Operational rituals

1. **Morning checklist** (manual at first, automate later):
   - Gateway logged in, port open
   - 2FA accepted
   - `clientId` not stuck in zombie state
   - Connection latency < 100 ms (`ib.reqCurrentTime()` round-trip)
   - Position reconciliation passed
2. **Pre-trade go/no-go** for live runs:
   - Connection healthy
   - Account NAV present and ≥ floor
   - No unexpected open orders
   - Latest position snapshot reconciles with broker
   - `IBKR_LIVE_TRADING_ENABLED=1` set deliberately for this session
3. **Post-trade audit**:
   - Every proposed order has an execution row OR a documented reason for non-fill
   - Slippage vs limit recorded
   - Daily PnL reconciles to broker statement (manually weekly to start)

---

## Open questions

- [ ] Headless deployment: run IB Gateway on Railway directly, on a small VPS, or on a home machine you trust to stay up?
   *(Railway can run the Gateway in a container, but daily 2FA and weekly forced-relogin are hostile to fully unattended ops; many people run a NUC at home.)*
- [ ] Subscription tier: which paid bundles for Phase 1 US, given the live account is funded?
- [ ] India coverage: stay on Upstox for India and use IBKR only for US/EU/APAC, or attempt unification under IBKR?
- [ ] Live-trading kill-switch policy: env-var guard plus what else? (Daily NAV-loss limits, position-size caps?)
- [ ] Order sizing: position-target-based (% of NAV) or absolute share count? (Cleaner: % of NAV, with min/max share guards.)
- [ ] Reconciliation cadence: daily vs intraday vs per-order? (Start daily; tighten if discrepancies appear.)
- [ ] Multi-account: separate IBKR sub-accounts for distinct strategies? (Useful for isolated PnL attribution; complicates aggregation.)

---

## Reference links

| Resource | URL |
|----------|-----|
| Official TWS API docs | https://interactivebrokers.github.io/tws-api/ |
| `ib_async` (maintained fork) | https://github.com/ib-api-reloaded/ib_async |
| `ib_insync` (original, less maintained) | https://github.com/erdewit/ib_insync |
| IBC (auto-login wrapper) | https://github.com/IbcAlpha/IBC |
| Gateway download | https://www.interactivebrokers.com/en/trading/ibgateway-stable.php |
| Market data subscriptions guide | https://www.interactivebrokers.com/en/index.php?f=14193 |
| Pacing limits (official) | https://interactivebrokers.github.io/tws-api/historical_limitations.html |
