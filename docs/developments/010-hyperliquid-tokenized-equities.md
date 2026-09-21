# 010 — Hyperliquid tokenized-equity perps as an after-hours signal source

> Status: `[proposed]` — drafted 2026-06-23. Independent of all other sleeves. Treat as an alt-data probe, not a price source.

## Decision

Ingest a narrow slice of Hyperliquid's HIP-3 builder-deployed equity perps as **alt-data**, keyed by the underlying US-listed ticker. Persist four derived series per (underlying, hl_dex, hl_coin, ts): **mark, basis vs cash close, funding rate, HL notional volume**. Do not treat the HL price as a substitute for the EODHD / Schwab / IBKR cash quote, and do not study the HL order book as a microstructure object.

First cut: top ~25 names where the HL listing overlaps the existing US universe and 24h HL volume clears $10M.

## Why

Hyperliquid launched HIP-3 in early 2026 — a permissionless framework letting deployers stake 500k HYPE (~$25M) to spin up a perpetual-futures dex. Several deployers now run tokenized-equity venues quoted in USDC, trading 24/7 against an oracle the deployer maintains. As of the snapshot below, eight HIP-3 dexs are live, one of which (`xyz`, deployed by trade.xyz) lists ~50 US single-name perps with **$2.54B daily volume and $3.08B open interest** — enough size to matter as a signal source even if it can't compete with NMS during regular hours.

What this venue offers that nothing in the current ingest does:

1. **24/7 marks on US single names** — overnight, weekend, holiday. The only continuous reference on TSLA/NVDA/AAPL outside RTH/extended hours.
2. **Funding rate** — directional positioning expressed as a rate participants pay to hold the view. No analogue exists in cash equities short of CFTC COT, which doesn't cover single names.
3. **Crypto-native flow** — a participant cohort that doesn't appear in TRACE, SIP, or any equity flow data we ingest. Orthogonal to TradFi retail / institutional flow.

What it does **not** offer:

1. Price discovery during RTH — the perp follows the cash market via deployer oracle, with seconds of lag.
2. Microstructure research — different fee/rebate structure, no PFOF, no dark pools, different participant population; book imbalance and queue signals don't generalise back to the cash market.
3. Multi-year history — HIP-3 launched ~Feb 2026, so ~4 months of data. Not enough for any factor with annual seasonality or regime training.

## Venue snapshot (2026-06-22)

Pulled live from `POST https://api.hyperliquid.xyz/info` with `{"type":"perpDexs"}` and `{"type":"metaAndAssetCtxs","dex":"<name>"}`:

| Dex | Total assets | Focus | Notes |
|---|---|---|---|
| `xyz` (XYZ / trade.xyz) | **93** | US singles + indices + commodities + FX + ETFs | $2.54B 24h, $3.08B OI — the only one worth ingesting at scale |
| `km` (Markets by Kinetiq) | 22 | US equities + ETFs + macro | Funding-interest-rate-based (different model) |
| `cash` (dreamcash) | 15 | Mag7 + macro proxies | Mid-size |
| `flx` (Felix Exchange) | 15 | Mixed equities/commodities | Funding multiplier set to 0 — different anchoring |
| `vntl` (Ventuals) | 15 | **Pre-IPO names** — OpenAI, Anthropic, SpaceX, plus thematic baskets | Unique exposure; oracle integrity unverified |
| `hyna` (HyENA), `para`, `mkts`, `abcd` | varies | Crypto / synthetic / sparse | Skip for equity research |

On `xyz` specifically, single-name equities listed include: AAPL, AMD, AMZN, ARM, ASML, AVGO, BABA, BX, COIN, COST, CRCL, CRWV, DELL, DKNG, EBAY, GME, GOOGL, HIMS, HOOD, IBM, INTC, LLY, META, MRVL, MSFT, MSTR, MU, NBIS, NFLX, NOW, NVDA, ORCL, PLTR, QCOM, RIVN, RKLB, SNDK, TSLA, TSM, WDC, ZM, plus indices (SP500, XYZ100, NIFTY, JP225, VIX, DXY), ETFs (SMH, XLE, EWJ/EWY/EWZ/EWT), and ~15 commodities/FX. 56 of the 93 listings cleared $1M 24h volume; 25 cleared $10M; 8 cleared $100M.

## Accuracy assessment

Mark-vs-oracle basis across all 93 `xyz` assets (live snapshot):

- **Median basis: 2.5 bps**
- **Mean basis: 2.8 bps**
- **Max absolute basis: 58 bps** (MINIMAX — thinly traded)

Top single-name overlap with the existing US universe:

| Asset | Mark | Oracle | Basis (bps) | 24h vol | OI | Funding/hr |
|---|---|---|---|---|---|---|
| xyz:MU   | 1181.30  | 1180.60  | +5.9  | $124M | $260M | 0.0036% |
| xyz:SNDK | 2309.40  | 2308.30  | +4.8  | $68M  | $65M  | 0.0006% |
| xyz:MRVL | 304.44   | 304.23   | +6.9  | $65M  | $83M  | 0.0025% |
| xyz:INTC | 138.84   | 138.70   | +10.1 | $65M  | $78M  | 0.0053% |
| xyz:NVDA | 208.87   | 208.70   | +8.1  | $50M  | $186M | 0.0039% |
| xyz:TSLA | 401.78   | 401.88   | −2.5  | $25M  | $30M  | −0.0025% |
| xyz:GOOGL| 345.07   | 344.77   | +8.7  | $21M  | $60M  | 0.0050% |

Single-digit bps basis on the deep names — tighter than the cash bid-ask spread in many cases.

**Critical caveat**: this measures mark vs **the deployer's oracle**, not mark vs the actual Nasdaq/NYSE print. Funding pulls mark toward oracle every block by design, so it will always look tight. What the basis number does *not* prove:

1. **Oracle integrity.** trade.xyz uses a composite of US exchange feeds during RTH, but the oracle is a single point of failure. Misconfigured splits, dividends, or halts would dislocate the perp with no NMS arb to correct it.
2. **Off-hours fidelity.** Outside RTH the oracle has no external print to track, so it effectively follows the HL book — circular. Overnight basis means "what HL traders believe," not "what the cash market would clear at."
3. **Cross-venue check.** Cash equities have SIP consolidation across 16 exchanges. A single-feed oracle can drift.

## What to ingest

Four series per (underlying ticker, hl_dex, hl_coin, ts):

1. **`mark`** — Hyperliquid's official reference price. From `activeAssetCtx` (WS) or `fastAssetCtxs` (compressed bulk, full dex per block). Updates ~70ms.
2. **`basis_bps`** — derived: `(mark / last_eod_close_underlying - 1) * 10000`. Recomputed on every mark tick; underlying close joined from `market.candles_daily`.
3. **`funding_rate_hourly`** — hourly funding paid by longs/shorts. From `/info` with `{"type":"fundingHistory","coin":"xyz:NVDA"}`. Also current rate in `activeAssetCtx`.
4. **`usd_volume`** — notional traded per bar. From `candle` WS channel (`v` × mid) or aggregated `trades`.

The signal artifact this lets you build:

```
overnight_signal(ticker, t) = basis_bps(t) * sigmoid(log(hl_volume_24h / cash_adv_20d))
```

Basis weighted by how much HL volume is supporting it, with funding used as a regime filter — skip the signal when funding is in the top/bottom decile because basis is then positioning-driven, not info-driven. Eval target: next-day open return on the underlying.

## Schema sketch

Goes in `alt_crypto_perp` (new schema, parallel to `alt_political_us`):

```sql
alt_crypto_perp.dex_registry        -- (dex_name, full_name, deployer, first_seen)
alt_crypto_perp.asset_registry      -- (dex_name, hl_coin, underlying_ticker, asset_class, first_seen, oracle_source)
alt_crypto_perp.marks               -- hypertable: (dex_name, hl_coin, ts, mark, oracle, mid, day_vol_usd, oi_usd)
alt_crypto_perp.funding             -- (dex_name, hl_coin, ts, funding_rate_1h, premium)
alt_crypto_perp.trades_agg          -- 1m candles: (dex_name, hl_coin, ts, o, h, l, c, v_base, v_usd, n_trades)
```

`underlying_ticker` is the join key against `ref.securities`. Pre-IPO names on `vntl` and pure-synthetic indices (XYZ100, H100) get `underlying_ticker = NULL` and a non-null `asset_class` for separate handling.

## Pipeline shape

```
WSS wss://api.hyperliquid.xyz/ws       → alt_crypto_perp.marks (1s decimation, hypertable)
                                       → alt_crypto_perp.trades_agg (1m bars)
REST /info fundingHistory (hourly poll) → alt_crypto_perp.funding
REST /info perpDexs (daily)            → alt_crypto_perp.dex_registry, asset_registry
                                                       ↓
                              derived.hl_overnight_signal (per underlying, per day)
```

One WS connection per dex covers all coins via `allMids` + `fastAssetCtxs` + per-coin `candle`. Per-IP limits (10 conns, 1000 subs, 2000 msgs/min) leave plenty of headroom. Funding is once-per-hour REST. Total ingest footprint is small — order of magnitude smaller than the political pipeline.

## What we are explicitly NOT doing

1. **Not using HL as a price source for backtests.** Cash close from EODHD remains the canonical mark for the underlying. HL is a side channel.
2. **Not ingesting all 93 `xyz` listings.** Filter to assets where (a) `underlying_ticker` joins to `ref.securities`, (b) 24h HL volume > $10M, (c) underlying ADV > 5× HL volume. Names below this floor get registered in `asset_registry` but no time-series ingest.
3. **Not researching microstructure.** The book is a different market; queue/imbalance/OFI research stays on Schwab and IBKR.
4. **Not touching the other dexs** (`km`, `cash`, `flx`, `vntl`, etc.) in phase 1. `vntl` pre-IPO names are interesting but oracle integrity is unproven — separate development if it earns its place.
5. **Not trading on HL.** Pure data ingest. Any execution would need a separate compliance and counterparty review (US-person access is gated at trade.xyz's frontend but not at the L1; legal status of synthetic equity exposure for a US LP is ambiguous).

## Tradeoffs considered

- **Ingest all dexs vs. xyz only**: `xyz` carries ~95% of liquidity. Adding the others quintuples schema complexity for marginal coverage.
- **Tick storage vs. 1m bars**: marks update every block (~70ms), so raw tick would be ~12 records/sec/coin × 25 coins = 300/sec sustained. 1m bars cut storage by 3000× with no signal loss for an overnight horizon. Use 1m as the default; keep tick optional for the top 5 names.
- **Pull funding live vs. EOD**: funding is determined hourly per asset, so an hourly poll is sufficient. Live subscription not needed.
- **Map by ticker vs. by ISIN**: HL coins are ticker-named with no ISIN field. Map by ticker, with a manual override table for corner cases (e.g. dual-listed ADRs).

## Open questions

1. **Oracle source disclosure.** trade.xyz hasn't published a precise spec of which exchanges/feeds compose the oracle, fallback behavior on halts, or how splits/specials are handled. Need a manual audit of historical corp actions on the listed names to validate.
2. **How long until SEC action.** US-person access is gated at the frontend, but enforcement posture could shift. If the venue disappears, the four series have no successor. Build the schema, but don't make any downstream factor *depend* on it.
3. **Pre-IPO names on `vntl`.** OpenAI/Anthropic/SpaceX marks are genuinely novel data. Worth a separate exploration as a phase 2, contingent on volume clearing $10M.
4. **Does the overnight signal actually predict?** Pure empirical. Needs 6+ months of data and proper out-of-sample design before any portfolio role.

## Phase 1 deliverables

1. `playground/explore/hyperliquid/` — probe scripts: dex enumeration, asset universe snapshot, sample WS subscription, sample funding pull. (Per the explore-first rule.)
2. `docs/data-sources/crypto/hyperliquid.md` — vendor integration spec.
3. `src/factorlab/sources/hyperliquid/` — client, ingest, schema mappings.
4. Alembic migration adding the `alt_crypto_perp` schema and tables.
5. `scripts/cross/hyperliquid/hl_marks_live.py` — long-running WS ingest for the filtered top-25 list.
6. `scripts/cross/hyperliquid/hl_funding_hourly.py` — hourly funding poll.
7. `configs/sources/hyperliquid.yaml` — dex allowlist, asset filter thresholds, ticker overrides.

Task Scheduler registration is out of scope per the standing rule — script lands with documented cadence; user handles the install.
