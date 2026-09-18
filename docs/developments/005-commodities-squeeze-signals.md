# 005 — Commodity squeeze-signal sleeve

> Status: `[proposed]` — drafted 2026-05-15. Independent of the political sleeve (003) and the live-US-data sleeve (002). Depends on the IBKR Lite → Pro upgrade for direct futures feeds; ships in degraded form on EODHD alone until that lands.

## Decision

Build a cross-market commodity sleeve focused on detecting **price squeezes** — supply/demand/positioning imbalances visible *before* the headline price move. The sleeve ingests futures prices + forward curves, exchange inventories, positioning (CFTC COT / LME COTR), physical-market reports (AWEX, USDA WASDE, EIA), weather grids, and listed-miner equity proxies into a unified `market.commodities_*` namespace, and rolls them up into `derived.commodity_squeeze_signals` — one row per (commodity, day) with a composite z-score and the contributing legs.

Australian wool is the **first** vertical to land end-to-end. Iron ore, LNG, lithium, and grains follow once the wool path is proven.

## Why

Commodity squeezes are among the highest sharpe regimes in the asset class — they are typically:
- **Telegraphed in fundamentals** 1–6 weeks before the price move (inventory drawdowns, weather anomalies, COT positioning extremes)
- **Mispriced in equity proxies** by 1–3 days vs the underlying (miners lag/lead the metal)
- **Concentrated in a handful of squeezeable contracts** (iron ore, LNG, lithium, nickel, cocoa, wool, coking coal) where physical supply is genuinely tight

Existing infrastructure already covers half the inputs:
- Equity proxies (BHP, RIO, FMG, PLS, MIN, S32, WDS) ride on the existing EODHD `.AX` plan
- India MCX (gold, silver, crude, copper, zinc, nickel) rides on the existing Upstox V3 instrument master with one segment flag flip
- IBKR Pro, when it lands, unlocks SGX iron ore, ICE Newcastle coal, LME metals, COMEX, CBOT, NYMEX

The missing pieces are: schema for futures curves + inventory + weather, ingest for the physical-market reports, and a derived view that fuses them.

## Case study: Australian wool

Wool is the headline case study because it exercises every leg of the architecture in a relatively low-volume, low-noise contract:

| Property | Value |
|---|---|
| Benchmark | AWEX Eastern Market Indicator (EMI), AUc/kg clean |
| Frequency | Weekly sales, Thursdays (~45 sale weeks/yr) |
| Categories | 17, 19, 21, 23, 25, 28 micron — fine wool carries a premium |
| Dominant buyer | China (~75% of Australian export volume) |
| Listed proxies (ASX) | Very limited; AWN is a levy body, not an investable. ASX 24 has Greasy Wool futures (19/21/23 micron) but they trade <100 lots/day — usable as signal, not execution |
| Drivers | AUD/USD, China textile/apparel demand, drought (flock size), shearing labour, sale-week clearance rate |
| Weather signal | NSW + Victoria + Tasmania rainfall (BOM gridded), ENSO/IOD outlook |
| Squeeze precedents | 2017–2018 China premium boom (EMI +60% peak-to-peak), 2022 post-COVID rebound |

What makes wool an excellent first-cut signal target:

1. **Single weekly print** (AWEX Thursday clearance) — no streaming infra, no Schwab session, no tick storage. A simple scraper + parquet drop.
2. **Clean weather attribution** — wool-producing regions are a known small set of BOM grid squares. ERA5 + BOM ENSO outlook covers it cleanly.
3. **No equity execution problem to solve** — signals here go into the watchlist for cross-asset use (e.g. AUD pairs, China textile names, retail/apparel shorts), not for ASX-listed wool stocks (because there aren't any).
4. **Exercises every leg** — physical price, fundamentals (sale clearance %), weather, FX, end-buyer concentration. If wool's signal pipeline works, iron ore is the same shape with bigger numbers.

Wool's pipeline shape is:

```
AWEX scraper (weekly)        → market.commodities_physical
BOM gridded rainfall (daily) → market.weather_grid
BOM ENSO + IOD (weekly)      → market.weather_indices
RBA AUD/USD (daily)          → market.fx_rates
                                                  ↓
                              derived.commodity_squeeze_signals (wool row)
```

## Signal taxonomy

Six independent signal legs. Each is an additive z-score contributor to the composite — never multiplicative — so a missing leg degrades gracefully.

| # | Leg | Source | Cadence | Lead time |
|---|---|---|---|---|
| 1 | **Price + curve structure** | IBKR Pro futures / EODHD continuous | 5m (IBKR) or daily (EODHD) | concurrent (the price itself) |
| 2 | **Positioning** | CFTC Commitments of Traders (free), LME COTR, SGX disclosed | weekly | 1–4 weeks |
| 3 | **Inventory** | LME warehouse stocks (free daily), SHFE, COMEX, EIA Weekly Petroleum Status, USDA WASDE | daily / weekly / monthly | 1–8 weeks |
| 4 | **Physical-market reports** | AWEX (wool), USDA WASDE (grains), EIA STEO (energy), Platts (paid, deferred) | weekly / monthly | 1–4 weeks |
| 5 | **Weather** | Open-Meteo (forecast, free), ERA5 (history, free via Copernicus), BOM (AU), NOAA (US), IMD (IN) | daily | 1–12 weeks for ag, immediate for power/gas |
| 6 | **Equity proxy** | EODHD `.AX` + Schwab US + Upstox | daily / intraday | sometimes -3 to +3 days vs metal |

The composite is `z = w1·curve + w2·positioning + w3·inventory + w4·physical + w5·weather + w6·equity_lead` with weights initialized equal and re-fit per commodity from a 5-year out-of-sample backtest. Weight estimation lives in `derived`, not in the ingest path.

## Data sources

Mapped against existing infra:

| Source | Status | Covers | Notes |
|---|---|---|---|
| **IBKR Pro** | pending upgrade ([006](#) — separate dev doc owed) | SGX (iron ore, coking coal), ICE (Newcastle coal, Brent, sugar, cocoa, coffee), LME (Cu, Ni, Al, Zn, Pb, Sn), COMEX (Au, Ag, HG), NYMEX (CL, NG, RB, HO), CBOT (ZW, ZC, ZS) | Direct exchange feeds. Exchange subscriptions add ~$40–$100/mo total once enabled. |
| **EODHD** | live | Continuous front-month for all the above, plus ASX `.AX` equities and most commodity ETFs (USO, UNG, CORN, DBA, JJC) | Sufficient as price source until IBKR Pro lands; permanent fallback after. |
| **Schwab** | live | Commodity ETFs intraday | Already on the watchlist tier from [002](002-live-us-market-data.md). |
| **Upstox** | live | MCX (gold, silver, crude, NG, Cu, Zn, Pb, Ni, Al, lead, menthol), NCDEX agri F&O | Needs `MCX_FO` + `NCD_FO` flags added to `sync_instruments()` — one-line scope change. Same auth, same rate limit bucket. |
| **AWEX** | new | Australian wool weekly clearance + EMI | HTML/PDF scraper, weekly cron, low volume — ~50 KB/week. |
| **USDA WASDE** | new | Grain S&D balances monthly | Public PDF + JSON via USDA NASS Quick Stats API (free, keyed). |
| **EIA** | new | Weekly Petroleum Status, STEO, Natural Gas Storage | EIA Open Data API (free, keyed). |
| **LME warehouse stocks** | new | Daily Cu/Ni/Al/Zn/Pb/Sn inventory by location | LME publishes free CSV daily at 09:00 BST. |
| **SHFE warehouse stocks** | new | Daily Shanghai metals inventory | Free Friday weekly PDF; daily via paid vendors only. |
| **CFTC COT** | new | Weekly large-spec positioning, all CFTC-regulated futures | Free CSV, Fridays 15:30 ET. |
| **Open-Meteo** | new | Global gridded forecasts (GFS + ECMWF) | Free, no key. ~10 grid points cover all commodity regions we care about. |
| **ERA5 (Copernicus CDS)** | new | Hourly reanalysis 1950–present | Free, keyed. Used for historical backtests, not live signals. |
| **BOM** | new | Australian gridded rainfall, **ENSO + IOD outlooks** | Free. ENSO/IOD are the single highest-signal weather variables for AU ags + Indian monsoon spillover. |
| **NOAA CFS / CPC** | new | US weather, US drought monitor, ENSO ONI | Free. |
| **IMD** | new | India monsoon onset/withdrawal, sub-divisional rainfall | Free, clunky. Use for India MCX agri exposure if NCDEX onboarded. |

## Schema additions

All new tables live in `market` (raw facts) and `derived` (rollups). Migrations 029-onward (subject to whatever 003 takes — to be sequenced when both ship). All multi-country tables are country-tagged via `country_code` per the [redesign 2026-05-01](../memory/project_redesign_2026_05_01.md) convention.

### `market` schema (new tables)

| Table | Granularity | PK |
|---|---|---|
| `market.commodities` | One row per commodity (vendor-keyed contract spec) | `(commodity_code)` — e.g. `'IRON_ORE_62'`, `'WOOL_EMI'` |
| `market.commodity_prices` | Daily continuous front-month price (hypertable) | `(commodity_code, ts)` |
| `market.commodity_curves` | Full forward curve, one row per (commodity, contract month, observation date) | `(commodity_code, contract_month, ts)` |
| `market.commodity_inventories` | Daily exchange warehouse stocks | `(commodity_code, exchange_code, location, ts)` |
| `market.commodity_positioning` | Weekly COT / COTR positioning | `(commodity_code, report_kind, ts)` |
| `market.commodity_physical` | Physical-market reports (AWEX, WASDE, EIA, STEO) | `(commodity_code, report_kind, report_date)` |
| `market.weather_grid` | Daily gridded weather (hypertable, ~10 grid points × ~10 vars) | `(grid_id, var_code, ts)` |
| `market.weather_indices` | ENSO ONI, IOD DMI, NAO, PDO etc. | `(index_code, ts)` |
| `market.weather_grids` | Static grid-point catalog (lat/lon, region tag, commodities-of-interest tags) | `(grid_id)` |

### `derived` schema (one new view)

| View | Granularity | Purpose |
|---|---|---|
| `derived.commodity_squeeze_signals` | One row per (commodity, day) | Six-leg composite z-score with each contributing leg as a column |

## Universe configs (no migrations, just YAML)

| File | Holds |
|---|---|
| `configs/universes/commodities_futures.yaml` | Master list of all futures commodities, vendor symbol mappings (IBKR conId, EODHD continuous code, Upstox MCX instrument key) |
| `configs/universes/au_miners.yaml` | BHP, RIO, FMG, MIN, PLS, IGO, S32, WDS, STO, WHC, NHC, NST — `.AX` symbols |
| `configs/universes/us_commodity_etfs.yaml` | USO, UNG, CORN, DBA, JJC, WEAT, COPX, GDX, GDXJ |
| `configs/universes/mcx_commodities.yaml` | India MCX gold/silver/crude/copper/zinc/nickel/lead |
| `configs/universes/weather_grids.yaml` | ~10 grid points: WA wheat belt, NSW/Vic wool, QLD coal, US Midwest corn, Permian, Henry Hub, Brazil Cerrado, W. Africa cocoa, Maharashtra cotton, Indonesia palm |

## Implementation phases

| # | Ships | Migration | Independent? |
|---|---|---|---|
| **C1** | Five universe yamls + `market.commodities` master table seed | 029 | yes |
| **C2** | `market.commodity_prices` + `market.commodity_curves` schema + EODHD continuous-front-month ingest | 030 | needs C1 |
| **C3** | **Australian wool end-to-end**: AWEX scraper → `commodity_physical`, BOM rainfall + ENSO/IOD ingest → `weather_grid`/`weather_indices`, RBA AUD ingest, single-row composite signal for wool | 031 | needs C2 |
| **C4** | LME daily warehouse stocks + CFTC weekly COT ingest → `commodity_inventories` / `commodity_positioning` | 032 | yes (parallel with C3) |
| **C5** | Upstox `MCX_FO` + `NCD_FO` flag flip in `sync_instruments()` + MCX commodity universe populated | none | yes |
| **C6** | IBKR Pro futures ingest (replaces EODHD continuous as primary price source for futures we have direct on) — gated by IBKR Pro upgrade | none | needs IBKR Pro |
| **C7** | `derived.commodity_squeeze_signals` materialized view + weight-fitting backtest harness | 033 | needs C2–C5 |

Migration head verified at 028; 029–033 reserved (interleaves with 003's 029–032 — sequencing decided when both move from `[proposed]` to `[accepted]`).

## Things explicitly NOT being done

- **Tick-level commodities data** — 5-min bars max. Wool is weekly; iron ore moves slowly enough that 5m IBKR Pro is overkill. Tick is a paid-vendor problem (TrueData, GDFL, Platts) and not in scope.
- **Commodity options** — futures only in v1. Options surfaces add 10× storage and require IV modelling that doesn't pay off until the underlying signal works.
- **Paid weather/fundamentals** — no Platts, Argus, Wood Mackenzie, AccuWeather, Visual Crossing, Skymet. All v1 sources are free or already-paid (IBKR, EODHD, Upstox).
- **Real-time execution** — this is a signal sleeve, not an execution sleeve. Outputs feed the watchlist / equity proxy book and `derived` analytics; the actual trades happen on equities (Schwab/IBKR) or are flagged for manual review. No commodity-futures execution.
- **Sub-daily weather ingest** — ERA5 is hourly but we resample to daily on ingest. Sub-daily weather is needed only for power/gas day-ahead trading, which is out of scope.
- **AWEX/USDA/EIA historical deep-backfill at v1** — 5 years on each source is enough to fit the weight estimator. Deeper history is a follow-up if specific commodities need it.
- **Non-Australia first** — wool ships before iron ore not because wool is bigger but because it exercises the full pipeline (weather + physical + FX + equity-proxy-absence) in a low-volume, low-noise environment. Iron ore is C7+1.

## Open questions owed to user

1. **IBKR Pro upgrade timing** — C6 is gated on this. Confirm whether the $200 deposit + $10/mo Snapshot Bundle decision is still planned (per memory, this was decided April 2026 but not yet executed). C1–C5 ship without it; C6 is the only blocked phase.
2. **NCDEX scope** — Upstox supports `NCD_FO` but Indian ag futures (guar, chana, castor) are much thinner signals than MCX metals/energy. Include in v1 or defer? Default: defer, ship MCX-only (C5).
3. **Weather grid count** — ~10 grid points covers the commodities we care about today. Add Brazil coffee (Minas Gerais), Vietnam coffee (Central Highlands), Ivory Coast/Ghana cocoa specifically, or take the regional grids (which average them)? Default: regional.
4. **AWEX licensing** — AWEX publishes weekly summary PDFs publicly but the detailed sale-by-sale clearance data may have a redistribution clause. Confirm scraping is OK for internal research use; flag if a licence is needed.

## Verification gates

| Phase | Gate |
|---|---|
| C1 | All five yamls validate against a `commodity_universe.schema.yaml`; `market.commodities` seeded with ≥40 commodities across 5 sectors (energy, metals, ags, softs, exotics) |
| C2 | EODHD continuous-front pulls ≥20yr history for top-10 commodities; curve ingest works for ≥3 exchanges (CBOT, NYMEX, COMEX continuous) |
| C3 | Wool composite signal computes for every Thursday since 2018; signal flagged ≥1 standard deviation above zero in the 2017–18 and 2022 squeezes (known-event sanity check) |
| C4 | LME daily stocks ingest matches LME's published daily delta within ±1 tonne; CFTC COT ingest matches CFTC's published net-long for top-10 contracts |
| C5 | MCX instruments visible in `ref.instruments` and 5-min poller writes MCX bars to `market.candles_intraday` (no schema change — same hypertable as equities) |
| C6 | IBKR Pro front-month price for ≥5 contracts matches EODHD continuous within ±5 bps; switchover validated against 30-day overlap |
| C7 | Composite signal Sharpe vs equity-proxy basket ≥ 0.8 over 5-year backtest (out-of-sample on the weight-fit) |

## When this ships

When C1–C7 complete, this doc moves to `[shipped]` and pointers go to:
- `docs/data-sources/cross/commodities.md` (new — IBKR/EODHD/Upstox/AWEX/USDA/EIA/LME/CFTC source spec)
- `docs/data-sources/cross/weather.md` (new — Open-Meteo/ERA5/BOM/NOAA/IMD source spec)
- `docs/countries/australia-commodities.md` (new — wool, iron ore, coking coal, lithium, gold, ASX miners as proxies)
- `docs/architecture/commodities-schema.md` (new — `market.commodities_*` + `derived.commodity_squeeze_signals` current-state spec)
- `docs/operations/commodity-orchestrator.md` (new — weekly + daily cron schedule for AWEX/COT/LME/WASDE/EIA)

Australian wool end-to-end (C1+C2+C3) is the minimum viable cut and can ship without waiting on C4–C7.
