# Roadmap

> Status: `[design]`

Roadmap is **capability-ordered**, not date-ordered. Each phase hardens the previous; do not start a phase until prior is `[beta]`.

---

## Phase 1 — Foundations `[in progress]`
**Goal:** US daily prices flowing into Postgres, reproducibly.

- [ ] Postgres provisioned (local Docker)
- [ ] Alembic initialized
- [ ] `ref.securities` + `ref.security_aliases` tables
- [ ] EODHD adapter → `raw_eodhd` → `market.price_bars_daily`
- [ ] Smoke test: 50 large-cap US tickers, 5 years daily, full lifecycle
- [ ] CI: pytest + ruff on every commit

**Exit criterion:** Reload from raw produces byte-identical fact rows.

---

## Phase 2 — Universe & survivorship `[design]`
**Goal:** Ask "what was in the S&P 500 on 2018-03-15" and get the right answer.

- [ ] Index membership tables (effective-dated)
- [ ] Delisted-securities ingestion
- [ ] Corporate actions (splits, dividends, mergers)
- [ ] Universe API: `get_universe(name, as_of_date) -> list[security_id]`

**Exit criterion:** Backtest of a trivial signal does not show survivorship-bias inflation.

---

## Phase 3 — Vectorized backtester `[design]`
**Goal:** Cross-sectional factor backtest with realistic costs.

- [ ] Signal → ranking → weighting → constraints → execution layers
- [ ] Transaction cost model (bid/ask, market impact)
- [ ] Capacity / turnover analysis
- [ ] Results land in `experiments.runs` + `experiments.backtest_metrics`

**Exit criterion:** Reproduce a known factor (e.g. low-vol) with reasonable IR.

---

## Phase 4 — IBKR paper integration & portfolio system of record `[design]`
**Goal:** IBKR (paper) is the canonical portfolio store. Every signal that would go live also runs paper.

- [ ] IB Gateway running locally (paper port 4002)
- [ ] `ib_async` client, deterministic clientIds, reconnect logic
- [ ] Daily snapshot of paper positions → `market.ibkr_positions_snapshot`
- [ ] Daily executions pull → `market.ibkr_executions`
- [ ] Signal-to-order translator with proposed-vs-filled reconciliation
- [ ] Slippage analysis: backtest vs paper vs (later) live

**Exit criterion:** Two consecutive weeks of signal → paper-fill → reconciliation green.

This phase is intentionally early (before alt-data) because **IBKR paper is the integration test** for the entire signal pipeline. Without it, you don't know if your model survives contact with a real broker API.

---

## Phase 5 — Reproducibility infrastructure `[design]`
**Goal:** Any historical result re-runnable bit-for-bit.

- [ ] `experiments` schema fully populated
- [ ] Code-SHA tagging in every run
- [ ] Data snapshot identifiers (raw archive query that defines a snapshot)
- [ ] Notebook discipline: notebooks call package, never define logic

**Exit criterion:** Pick a 6-month-old run; reproduce in one command.

---

## Phase 6 — India integration `[design]`
**Goal:** NSE/BSE equities and F&O via Upstox, same code paths as US.

- [ ] Upstox adapter (port from `E:\...\upstoxAPI`)
- [ ] Daily token-refresh job
- [ ] Instrument-key mapping → `ref.security_aliases`
- [ ] Minute-bar storage decision (Postgres partitioned vs. Parquet+DuckDB)
- [ ] Multi-currency, multi-calendar handling

**Exit criterion:** US and IN factors run side-by-side from one config.

---

## Phase 7 — Alternative data ingestion `[design]`
**Goal:** Reddit, Twitter, Senator trades, arxiv flowing into respective schemas.

Subphases (parallelizable):
- 7a Reddit/Twitter (sentiment, attention)
- 7b Senate trade disclosures
- 7c arxiv q-fin/cs.LG papers

Each follows the same adapter/parser/raw-archive pattern. See `docs/data-sources/`.

**Exit criterion:** Each source has at least one derived signal feeding into a backtest.

---

## Phase 8 — Live signal pipeline `[design]`
**Goal:** Daily orchestrated job: fetch → compute → write signals → IBKR (paper, eventually live) → notify.

- [ ] Orchestrator chosen (Prefect/Airflow/cron)
- [ ] Idempotent jobs with retries
- [ ] Monitoring: data freshness, signal stats, vendor anomaly detection
- [ ] Deployed to Railway
- [ ] Live-trading kill-switch + manual `LIVE=1` opt-in

**Exit criterion:** Two consecutive weeks of green daily runs without intervention. Paper portfolio matches expected positions; PnL reconciles to broker statement.

---

## Phase 9 — Event-driven backtester `[design]`
**Goal:** Execution-sensitive strategies.

Postponed deliberately. Vectorized covers cross-sectional factor research, which is the bulk of the work. Build event-driven only when an actual strategy demands it.

---

## What is explicitly out of scope (for now)

- Public web app
- Multi-tenant authentication
- Real-time streaming (sub-daily) for US
- Options pricing models (until India F&O signal demands it)
- Custom hardware acceleration
