# US Equities — Market Structure & Hours

> Comprehensive reference for US trading session structure, regulatory framework, and quant data implications.
> Pairs with vendor-specific docs in `docs/data-sources/`.

---

## TL;DR

- **Regular session**: 09:30–16:00 ET (Mon–Fri, 6.5 hours = 390 minutes)
- **Pre-market**: 04:00–09:30 ET (extended via ECNs since 2007)
- **Post-market**: 16:00–20:00 ET
- **Overnight (8 PM–4 AM ET)**: Blue Ocean ATS (Sun-Thu) since 2021; NYSE Arca + NASDAQ extending to 22+ hours pending SIP/DTCC alignment
- **Reg NMS / NBBO**: Regular hours only. Extended hours = per-venue quotes, no consolidated best
- **Settlement**: T+1 (since 2024-05-28), uniform across all sessions
- **FactorLab convention**: Ingest all sessions, tag with `session ∈ {pre, regular, post}` enum, default factor pipelines query `WHERE session='regular'`

## History (chronological)

### Single-session era (1817–1990)

| Date | Event | Driver |
|------|-------|--------|
| **1792** | Buttonwood Agreement — 24 brokers in NYC | Need for orderly bilateral trading |
| **1817** | NYSE founded; **call-market** format, two daily sessions | Orderly price discovery in illiquid market |
| **1871** | Continuous trading replaces call market | Post-Civil War volume; telegraph + ticker compress info cycles |
| late 1800s–1952 | Saturday half-day sessions (10:00 AM–12:00 PM) | Six-day workweek |
| **1952** | Saturday trading abolished; 5-day Mon-Fri week begins | Back-office labor costs, white-collar work patterns |
| **1971** | NASDAQ launches as electronic OTC market | SEC pressure to organize OTC fragmentation |
| **1974** | NYSE extends close to 16:00 ET | West Coast institutional demand, rising volume |
| **1985** | NYSE shifts open from 10:00 → **09:30 ET** | Sync with S&P futures (CME, 1982); regional/London competition |

### ECN + extended-hours era (1991–2007)

| Date | Event | Driver |
|------|-------|--------|
| **1991** | NYSE Crossing Sessions I & II launch (16:15–17:15 ET) | Index funds + program trading want closing-price executions |
| Mid-1990s | ECNs proliferate: Instinet, Island (Datek), Archipelago, BRUT | SEC Order Handling Rules (1997) force ECN quote display |
| **1999** | SEC Reg ATS effective; brokers extend retail to 18:30–20:00 ET | Dot-com retail demand; legitimized ECNs as ATSs |
| **2000–2003** | Boom + shakeout (Wit Capital, MarketXT fold) | Market correction; consolidation around NASDAQ ECNs |
| **2005** | Reg NMS adopted (effective 2007); NASDAQ acquires INET | Order Protection Rule defines NBBO = regular hours |
| **2006** | NYSE-Archipelago merger; NYSE becomes electronic-capable | NYSE forced electronification by ECN competition |
| **2007** | NYSE Arca pre-market opens at **04:00 ET**; current 04:00–20:00 envelope locks in | BATS founded 2005; global index linkages |

### 24/5 era (2021–present)

| Date | Event | Driver |
|------|-------|--------|
| **2021** | Blue Ocean ATS launches overnight 20:00–04:00 ET | Asian retail demand for US equities during their day |
| **Feb 2025** | Schwab makes 24-hour trading available to all retail | Crypto-style 24/7 expectations, retail demand |
| **Oct 2024** | NYSE files for 22-hour weekday trading on Arca (01:30–23:30 ET) | 24X National Exchange approved Mar 2024; SIP catch-up |
| **Apr 2026** | SEC approves NASDAQ 23/5 trading | Rollout pending SIP/DTCC alignment as of 2026-05-01 |

**SIP coverage of overnight tape is unresolved**: 2025 SIP votes failed (4× no); SEC granted exemption through May 2028 while SIP committees negotiate. Real-time overnight prints currently disseminated via Pyth, Bloomberg vendor feeds — not the public SIP. Blue Ocean trades reported to NYSE TRF for next-morning consolidation.

## Session structure (2026)

### Standard day windows

```
04:00 ET ─────────── 09:30 ET ───────────── 16:00 ET ───────────── 20:00 ET
       Pre-market           Regular session             Post-market
   (5.5 hrs)               (6.5 hrs, 390 min)            (4.0 hrs)

20:00 ET ────────────── 04:00 ET (next day)
            Overnight (Blue Ocean ATS, Sun-Thu only)
```

- **Closes** the prior calendar day at 20:00 ET (Friday post-market closes 20:00 ET; no Friday overnight session — Blue Ocean only opens Sun-Thu)
- **Opens** at 04:00 ET Monday after the weekend gap

### Half-days (2026)

| Date | Event | Regular close | Extended close |
|------|-------|---------------|----------------|
| Friday Nov 27 | Day after Thanksgiving | 13:00 ET | 17:00 ET |
| Thursday Dec 24 | Christmas Eve | 13:00 ET | 17:00 ET |

### Holidays (full closures)

New Year's Day, MLK Day, Presidents' Day, Good Friday, Memorial Day, Juneteenth, July 4, Labor Day, Thanksgiving, Christmas. Use `exchange_calendars.get_calendar("XNYS")` for canonical schedule.

## Retail broker access (2026)

| Broker | Pre-market | Post-market | 24-hour overnight |
|--------|-----------|-------------|-------------------|
| **Schwab** | 07:00–09:25 ET | 16:05–20:00 ET | EXTO via thinkorswim, S&P 500 + Nasdaq-100 + select ETFs |
| Fidelity | 07:00–09:28 ET | 16:00–20:00 ET | None |
| Robinhood | 07:00–09:30 ET | 16:00–20:00 ET | ~900 names, Sun 20:00–Fri 20:00 ET |
| IBKR | 04:00–09:30 ET | 16:00–20:00 ET | Overnight Pro 20:00–03:50 ET on >10,000 symbols |
| E*TRADE | 07:00–09:30 ET | 16:00–20:00 ET | None |

**Schwab's pre-market floor of 07:00 ET** (not 04:00) means our intraday puller will only see pre-market bars from 07:00 onwards — not the full ECN window.

### Account-type restrictions

- IRAs: extended hours OK but no margin/short
- Margin-call accounts: typically blocked overnight
- Options: regular session only — options markets do not extend

## Regulatory framework

### Reg NMS (2005, effective 2007)

- **Defines RTH** as 09:30–16:00 ET (17 CFR §242.600(b)(88))
- **Order Protection Rule (611)**: trade-through prohibition only applies during RTH
- **NBBO is regulatory only during RTH** — extended hours = per-venue inside quotes, no consolidated best

### Reg ATS

- Governs ECNs/ATSs including Blue Ocean
- ATSs operating outside RTH have less stringent obligations than national exchanges

### FINRA Rule 2265

Members must furnish written extended-hours risk disclosure before customer trades extended hours, covering:
- Volatility (lower liquidity → bigger moves)
- Changing prices (gap risk between sessions)
- Unlinked markets (no consolidated NBBO)
- News (releases concentrated in extended hours)
- Lower liquidity, wider spreads

### SEC Rule 605/606 (2024 amendments)

Expanded "covered orders" to **explicitly include extended-hours orders**. Large broker-dealers (100k+ accounts) must collect extended-hours execution-quality data starting **August 1, 2026**.

### Settlement (T+1)

- Effective **2024-05-28**: all US equities settle T+1
- Extended-hours trades settle T+1 same as RTH (no special treatment)
- DTCC announced 24×5 clearing rollout in 2026 to support overnight ATS — infrastructure-only change, settlement date unchanged

## Order-type rules

| Order type | RTH | Pre/post |
|------------|-----|----------|
| Limit | ✓ | **✓ (only allowed)** |
| Market | ✓ | ✗ (no NBBO to peg to) |
| Stop / Stop-limit | ✓ | ✗ |
| FOK / IOC / AON | ✓ | ✗ |
| MOC (Market-on-Close) | ✓ — submit ≤15:50 ET (NYSE) / 15:55 (NASDAQ) | n/a |
| LOC (Limit-on-Close) | ✓ — submit ≤15:50 ET (NYSE) / 15:58 (NASDAQ) | n/a |
| MOO/LOO | ✓ — submit ≤09:28 ET | n/a |
| GTC | ✓ | typically does NOT execute extended; queues for next RTH unless explicitly flagged |

## Quote mechanics

- **NBBO consolidated only during RTH** (Reg NMS Rule 600)
- During extended hours, each ECN publishes its own top-of-book; **no consolidated best**
- Locked/crossed quotes are 0.3% during RTH but **11% locked / 24% crossed during pre-opening** (Tran 2015)
- **Implication for FactorLab**: extended-hours bid/ask spreads are per-venue artifacts, not consolidated quotes

## Microstructure: liquidity, spread, volatility

### Volume distribution

- Regular session: **U-shape** — heavy at 09:30 (open) and 16:00 (close auction), thin midday
- Pre-market: ~**1–3% of regular volume** for S&P 500 names
- Post-market: ~**3–5% of regular volume**, frontloaded — drops 80% from 16:00–16:30, another 85% from 16:30–17:00
- Closing auction: **7% of NYSE-listed ADV**, **10% of NASDAQ ADV** (BMLL 2018; rising due to passive/index flows; Russell rebalance day can hit 25–30%)
- Opening auction: ~1–3% of NYSE ADV

### Spreads

- Extended hours quoted spreads are **3–10× wider** than regular session for liquid names; much more for mid/small cap
- Effective spreads even more punitive — top-of-book size is tiny outside RTH

### Volatility

- Per-minute return variance in pre/post is higher than midday but **lower** than 09:30–09:45 / 15:50–16:00 windows of regular session
- High-vol pockets: **16:00–16:30 ET** (earnings dump in 16:01–16:05 ET) and **08:30–09:30 ET** (overnight news + macro prints priced in)
- Realized-vol estimators (Andersen-Bollerslev): use 79 × 5-min RTH intervals only — extended-hours noise dominates

## News catalysts

- Roughly **60% of S&P 500 earnings released AMC** (after market close), **35% BMO** (before market open), **5% other**
- **Why AMC dominates**: gives analysts/algos overnight to digest; reduces knee-jerk regular-session vol; Reg FD compliance is easier
- Macro releases:
  - 08:30 ET (pre-market): CPI, NFP, PPI, retail sales, GDP — pre-market futures absorb the print, cash open at 09:30 inherits the gap
  - 10:00 ET (regular): ISM, JOLTS
  - 14:00 ET (regular): FOMC statement
  - 14:30 ET (regular): Powell presser

## Quant workflow implications

### Industry conventions

- **CRSP** (academic gold standard): daily close = regular 16:00 close; pre/post excluded from `PRC, OPENPRC, BIDLO, ASKHI, VOL`
- **TAQ** (NYSE Daily TAQ Spec v4.2): tags pre/post prints with sale-condition codes `T` (extended hours) and `U` (out-of-sequence after-hours, late-reported >90s). Holden-Jacobsen (JF 2014) recommends dropping `T`/`U` prints for daily OHLCV / NBBO / spread analytics.
- **Bloomberg / FactSet / Refinitiv / Compustat**: all default to regular-session official close. Pre/post available as separate fields, never mixed into the canonical bar.
- **EODHD**: follows CRSP convention for daily bars.

### Factor-research conventions

| Factor family | Window |
|---------------|--------|
| Momentum (12-1, 6-1) | Close-to-close, regular session |
| Short-reversal | Close-to-close (regular); overnight gap as a *separate* signal (Lou-Polk-Skouras 2019) |
| Realized vol / RV-based betas | Regular 5-min only (Andersen-Bollerslev) |
| Liquidity (Amihud, Roll, Corwin-Schultz) | Regular session only |
| PEAD / event studies | **Need** pre/post for the announcement window |
| Overnight-return factor | Pre-open print or 09:30 open vs prior 16:00 close |

**Lou, Polk, Skouras (JFE 2019, "A Tug of War")**: momentum lives intraday, reversal lives overnight — strong own-period continuation, cross-period reversal. Implication: extended-hours/overnight prints carry orthogonal information — worth ingesting for factor research, but never mix into close-to-close calculations.

### Pitfalls to avoid

1. **Wrong close** — never use last extended print as official close. Anchor to 16:00 auction.
2. **Volume contamination** — pre/post is ~4–6% of consolidated volume on average but can spike to 30%+ on earnings days. Distorts ADV-based liquidity factors.
3. **Amihud mismatch** (Bernhardt et al. 2019): standard ILLIQ uses close-to-close return (embeds overnight) over regular volume — denominator/numerator span different windows. Use open-to-close return / regular volume.
4. **Thin-print extremes**: a single 100-share pre-market print can set a bogus high/low. Never rank cross-sectionally on extended-hours OHLC extremes.
5. **ADR / dual-listing skew**: ADRs trade pre-market driven by home-market close.

## FactorLab schema decision

**Option B (chosen): Single `market.candles_intraday` table with `session` enum column.**

```sql
CREATE TYPE session_kind AS ENUM ('pre', 'regular', 'post');

CREATE TABLE market.candles_intraday (
    instrument_id   BIGINT NOT NULL,
    trade_time      TIMESTAMPTZ NOT NULL,
    frequency       VARCHAR(8) NOT NULL,    -- '1min', '5min'
    session         session_kind NOT NULL,
    open            NUMERIC,
    high            NUMERIC,
    low             NUMERIC,
    close           NUMERIC,
    volume          BIGINT,
    as_of_time      TIMESTAMPTZ NOT NULL,
    source          VARCHAR(16) NOT NULL,   -- 'schwab'
    PRIMARY KEY (instrument_id, trade_time, frequency)
);
-- Hypertable + index on (session, frequency) for fast filter
```

**Reasoning:**
- Schwab returns all bars in one stream — splitting into two tables wastes a join on every query
- `(security_id, ts, session)` indexed → default research view `WHERE session='regular'` is one-line filter, partial index keeps it cheap
- PEAD / overnight-gap / earnings-reaction studies just drop the filter — no schema branch
- TimescaleDB compresses repeated enum values near-perfectly
- Mirrors TAQ's approach (one Trade table, sale-condition column) — proven at scale

**Daily-bar derivation** from intraday: build `market.bars_daily` from `WHERE session='regular' AND ts BETWEEN 09:30 AND 16:00 ET`, with volume summed over regular only.

### Puller behavior

- Fetch with `need_extended_hours_data=True` (Schwab returns it anyway with the dup quirk — keep it)
- **Dedup on `datetime`** — Schwab's 1m/5m responses contain each timestamp 2× (regular bars duplicated). Trivial fix at ingest.
- Tag each bar's `session` from the timestamp:
  - `<09:30 ET → pre`
  - `≥09:30 ET AND <16:00 ET → regular`
  - `≥16:00 ET → post`
- Write all bars; default factor pipelines filter `session='regular'`

## References

- Lou, Polk, Skouras — "A Tug of War: Overnight vs Intraday Expected Returns", JFE 2019. https://personal.lse.ac.uk/polk/research/TugOfWar.pdf
- Andersen-Bollerslev — "The Distribution of Realized Stock Return Volatility", JFE 2001
- Bernard-Thomas — Post-Earnings Announcement Drift, JAR 1989
- Holden-Jacobsen — "Liquidity Measurement Problems in Fast, Competitive Markets: Expensive and Cheap Solutions", JF 2014
- Bernhardt et al. — "The Night and Day of Amihud's Liquidity Measure", Warwick WP 2019
- Bloomberg Tradebook — "Pre- and Post-Market Trading for US Stocks"
- BMLL — "Into the Close: Closing Auction Dynamics" 2018
- NYSE Daily TAQ Client Specification v4.2 (Aug 2025)
- CRSP US Stock & Indexes Database Data Descriptions Guide
- Tran (2015) — "Locks, Crosses, and the Limit Order Book"
- FINRA Rule 2265 — Extended-Hours Trading Risk Disclosure
- 17 CFR §242.600(b)(88) — Reg NMS RTH definition
- SEC Rule 605/606 (2024 amendments) extending coverage to extended-hours orders
- SEC T+1 Final Rule (2024-05-28)
