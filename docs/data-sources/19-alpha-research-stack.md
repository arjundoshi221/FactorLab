# Alpha Research Data Stack

> Status: `[design]` · Last updated: 2026-09-20 · Owner: Arjun

## Purpose

Establish the ingestion surface for **alpha research and ideation** — equity (US primary, India secondary) with macro/commodity context that affects equities. Free-first, source-direct, LLM-ingestible. Target: hedge-fund-grade signal quality at a solo-shop budget (<$100/mo paid tier).

**Non-goals**: Portfolio management, execution, risk. Those live elsewhere (execution in the separate trade engine per `project_ibkr_role.md`).

**Explicit priorities in order:**
1. **Positioning inference — what CTAs / systematic funds / dealers are doing** (this is the sharpest edge for a small shop; you can't out-fundamental Fidelity, but you can front-run mechanical flows).
2. **Filings / insider / political** — text-heavy, LLM-native, structural edge.
3. **News / sentiment digest** — LLM synthesis layer over free RSS/wire.
4. **Macro context** — regime and commodity input costs that steer equity sectors.

---

## Design Principles

- **Source-direct over aggregators**. If CFTC publishes it free, we don't pay Quandl for it.
- **API/structured over PDF**. Anything requiring OCR or manual scrape is deprioritized unless the signal is unique.
- **LLM-ingestible**. Every source lands in a raw text/JSON table with `retrieved_at`, so a downstream LLM agent can digest it. No PDF-only sources at Tier 1.
- **Cadence-aware**. Weekly positioning data (COT) triggers weekly agent runs; real-time filings trigger event agents. Don't poll daily for weekly data.
- **Schema fit**: everything lands in `alt_*` schemas (per the 8-schema BCNF design). New sub-schemas as needed: `alt_positioning`, `alt_flow`, `alt_news`, `alt_macro`.

---

## Tier 0 — Free, Source-Direct (build these first)

### A. CTA & Positioning Inference

The single sharpest edge available at this budget. CTAs are ~$350B AUM and mechanical — their positioning is inferable from public data with a 1-week lag (US) or 1-day lag (India).

#### A1. CFTC Commitments of Traders — Traders in Financial Futures (TFF)
| Field | Detail |
|-------|--------|
| URL | https://www.cftc.gov/dea/futures/financial_lf.htm |
| API | https://publicreporting.cftc.gov/resource/gpe5-46if.json (Socrata) |
| Auth | None (App token optional, higher rate limit) |
| Cadence | Weekly, Fri 3:30pm ET (as of Tuesday) |
| Format | CSV bulk + JSON API |
| LLM-ready | Yes — small tabular, easy digest |

**Alpha thesis**: Managed money (proxy for CTAs) net long/short positioning in ES, NQ, YM, RTY, ZN, ZB, CL, GC, SI. Extreme z-scored positioning (>2σ) is a *mean-reversion* signal at the position level and a *momentum-continuation* signal within trend regimes. This is how you infer whether CTAs are near max long/short → likely forced sellers/buyers on regime break.

**Call pattern**:
```
GET https://publicreporting.cftc.gov/resource/gpe5-46if.json
    ?$where=report_date_as_yyyy_mm_dd>'2020-01-01'
    &market_and_exchange_names=E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE
    &$limit=5000
```

#### A2. CFTC Disaggregated COT (Commodities)
| URL | https://publicreporting.cftc.gov/resource/72hh-3qpy.json |
| Cadence | Weekly (same as TFF) |
| Coverage | Physical commodity futures — Managed Money, Producer/Merchant, Swap Dealer |

**Alpha thesis**: Producer hedging vs. managed money speculation in crude, nat gas, gold, silver, copper, wheat, corn, soybeans. Divergence between producer hedge ratio and MM net position → regime shift signal.

#### A3. NSE India — Participant-wise Open Interest (Daily)
| URL | https://www.nseindia.com/reports-indices-participant-wise-open-interest |
| Direct CSV | https://archives.nseindia.com/content/nsccl/fao_participant_oi_DDMMYYYY.csv |
| Auth | None, but requires browser-like headers + cookie warm-up |
| Cadence | **Daily**, ~6:00pm IST |
| Format | CSV |
| LLM-ready | Yes |

**Alpha thesis**: **India's COT-equivalent, but daily instead of weekly.** Client / FII / DII / Pro breakdown of long/short OI in index futures/options and stock futures/options. FII net long index futures crossing thresholds has historically preceded 3-5 day Nifty moves. This is the highest-frequency positioning dataset available anywhere for free.

**Call pattern**: Use `curl` with `User-Agent` + prior GET on nseindia.com to establish cookie, then fetch the CSV.

#### A4. NSE FII/DII Cash Segment Activity
| URL | https://www.nseindia.com/reports/fii-dii |
| Cadence | Daily EOD |
| Format | Web + CSV |

Net cash buy/sell by FII and DII. Combined with A3 for positioning-vs-flow divergence.

#### A5. NSDL FPI Segment Allocation
| URL | https://www.fpi.nsdl.co.in/ |
| Cadence | Monthly |
| Format | Excel |

Foreign portfolio investor allocation by sector/security. Slower but the definitive foreign-flow-by-sector view for India.

#### A6. SqueezeMetrics DIX & GEX (US)
| URL | https://squeezemetrics.com/monitor/dix (daily free print) |
| Cadence | Daily |
| Format | Chart + daily CSV via free scrape |

**Alpha thesis**: Dealer gamma exposure (GEX) determines whether market makers dampen or amplify moves. Negative gamma regime = amplified moves = higher realized vol → different strategy set (short vol strategies fail, breakout strategies work). Dark Index (DIX) tracks dark-pool short volume, contrarian bullish at extremes.

#### A7. SocGen CTA / Trend Indices
| URL | https://cib.societegenerale.com/en/prime-services-indices/ |
| Cadence | Daily |
| Format | Web (scrape) |

**Alpha thesis**: Direct daily NAV of SG CTA Index, SG Trend, SG Short-Term Traders. Back out CTA positioning by regressing index daily returns against major futures returns (Newedge-style attribution). Confirms/contradicts COT-based inference.

#### A8. OCC Daily Options Volume + FINRA Short Volume
| OCC URL | https://www.theocc.com/market-data/volume-statistics/daily |
| FINRA URL | https://cdn.finra.org/equity/regsho/daily/ |
| Cadence | Daily |
| Format | CSV |

Structural: OCC total put/call, contract mix. FINRA short volume per ticker (not short interest — daily *flow*).

### B. Filings / Insider / Political (LLM-native text)

#### B1. SEC EDGAR — Full-Text Search + Filing Feeds
| URL | https://efts.sec.gov/LATEST/search-index?q= |
| REST | https://data.sec.gov/submissions/CIK{cik}.json |
| RSS | https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom |
| Auth | None (User-Agent header required with contact) |
| Cadence | Real-time |
| Format | JSON + HTML/XBRL filings |
| LLM-ready | Yes — this is your primary text firehose |

**Alpha thesis**: 8-Ks are event catalysts (guidance, M&A, exec changes). 13D/G is activist positioning. Form 4 insider clusters (multiple insiders buying within 30 days) is one of the highest-Sharpe signals in academic literature (Cohen-Malloy-Pomorski). 10-Q/K MD&A text changes YoY, run through LLM diff, surfaces subtle guidance shifts.

**Note**: User-Agent header MUST include real contact per SEC ToS. Per user preference (memory: no email leakage), use `factorlab research (admin contact via github.com/arjundoshi)` — NOT personal email.

#### B2. Congress.gov API
| URL | https://api.congress.gov/v3/ |
| Auth | Free API key (register) |
| Cadence | Real-time |
| Format | JSON |

Already partially covered by your existing `alt_political_us` pipeline. Extension: bill text → LLM classification into GICS sectors → sector-level policy risk scores.

#### B3. BSE / NSE Corporate Filings (India)
| BSE URL | https://www.bseindia.com/corporates/ann.html |
| NSE URL | https://www.nseindia.com/companies-listing/corporate-filings-announcements |
| RSS | BSE offers RSS per company |
| Cadence | Real-time (market hours) |

**Alpha thesis**: Insider trades (SEBI PIT disclosure), block deals, bulk deals, results, board meetings. Bulk/block deal disclosures within 24h — track counterparty when named (many are institutional).

#### B4. SEBI Orders + Adjudication
| URL | https://www.sebi.gov.in/enforcement/orders.html |
| Cadence | Ad-hoc, ~daily |
| Format | PDF (Tier 2 — needs OCR) |

Enforcement actions are event risk. Companies named in insider trading orders often see 5-10% drawdowns.

### C. News / Sentiment (LLM Digest Layer)

#### C1. Reuters + AP + Bloomberg Opinion RSS
| Reuters RSS | https://www.reutersagency.com/feed/ |
| AP RSS | https://apnews.com/feed |
| Bloomberg Opinion RSS | https://www.bloomberg.com/opinion.rss |
| Cadence | Real-time push |
| LLM-ready | Yes |

#### C2. FT Alphaville
| URL | https://www.ft.com/alphaville (free w/ reg) |
| Cadence | Daily |

Best free source for macro plumbing / market structure color.

#### C3. Reddit API (WSB, r/investing, r/IndianStockMarket, r/SecurityAnalysis)
| URL | https://oauth.reddit.com/ |
| Auth | Free OAuth app |
| Cadence | Real-time |

**Alpha thesis**: WSB mentions as *contrarian* at extreme retail-crowded tickers; r/SecurityAnalysis as *idea flow* (curated fundamental writeups). Ticker-mention counts + sentiment as a factor, not standalone.

#### C4. StockTwits API
| URL | https://api.stocktwits.com/api/2/ |
| Cadence | Real-time |

Ticker-level bull/bear sentiment. Signal is in *changes*, not levels.

#### C5. Curated Substacks (Free Tiers)
Ingest via RSS, feed to weekly LLM digest agent:
- **Doomberg** — commodities/energy
- **The Diff** (Byrne Hobart) — cross-cutting business strategy
- **Net Interest** (Marc Rubinstein) — financials
- **Concoda** — rates / repo / plumbing
- **Chartbook** (Adam Tooze) — macro/political-economy
- **Macro Compass** (Alfonso Peccatiello) — free tier
- **The Transcript** — earnings call theme aggregation

#### C6. VIC (Value Investors Club) + SumZero Free
| VIC URL | https://valueinvestorsclub.com/ |
| Cadence | Ad-hoc, 45-day delay on free tier |

Delayed but the ideas are still educational; LLM can extract *pattern* (what constitutes a good long/short pitch), not just the current call.

### D. Macro (equity-regime context)

| Source | URL | Cadence | Notes |
|--------|-----|---------|-------|
| FRED API | https://api.stlouisfed.org/fred/ | Real-time | Free API key, every US macro series |
| BLS API | https://api.bls.gov/publicAPI/v2/ | Monthly | CPI/PPI/NFP with revisions |
| BEA API | https://apps.bea.gov/api/ | Quarterly | GDP components |
| Treasury TIC | https://home.treasury.gov/data/treasury-international-capital-tic-system | Monthly | Foreign holdings of USTs |
| Fed H.4.1 / H.8 / Z.1 | https://www.federalreserve.gov/data.htm | Weekly/Monthly/Qtly | Balance sheet, bank credit, flow of funds |
| RBI DBIE | https://dbie.rbi.org.in/ | Various | Free API, comprehensive India macro |
| EIA API | https://api.eia.gov/ | Weekly | Petroleum status, nat gas storage — free |
| USDA WASDE | https://www.usda.gov/oce/commodity/wasde | Monthly | Ag supply/demand |
| Baker Hughes rig count | https://rigcount.bakerhughes.com/ | Weekly | Oil/gas activity leading indicator |
| LME | https://www.lme.com/ | Daily | Free settlement prices |

**Alpha thesis**: Macro rarely gives direct stock-picking alpha, but the **regime** (rate-cut cycle vs hiking, dollar strength, energy price shock) determines which equity factors work. Feed macro state to a regime-classification agent that gates factor tilts.

---

## Tier 1 — Paid Under $100/mo

Recommended stack: **Unusual Whales + Quiver + flex slot = ~$88/mo**

### T1.1 — Unusual Whales (~$48/mo)
| URL | https://unusualwhales.com/api |
| Auth | API token (paid) |
| Coverage | Options flow (real-time), dark pool prints, GEX, insider Form 4 (real-time), congressional trades, ETF flows, short interest |
| LLM-ready | JSON API |

**Alpha thesis**: Single best sub-$50 source for **dealer/CTA-adjacent positioning inference intraday**. Options flow (block trades, sweep orders) reveals institutional intent before it hits the tape. Dark pool prints show accumulation/distribution invisible in lit markets. Real-time Form 4 beats EDGAR RSS latency by hours.

### T1.2 — Quiver Quantitative Premium (~$10/mo)
| URL | https://api.quiverquant.com/beta/ |
| Coverage | Congressional trades, lobbying spend, government contracts, patents, WSB sentiment, insider clusters, Wikipedia views |

**Alpha thesis**: Overlaps your existing `alt_political_us` (congressional trades) but adds **lobbying + contracts + patents at a fraction of building it yourself**. Government contracts data is under-exploited alpha for defense/gov-services names. Lobbying spend by sector as a policy-tailwind proxy.

### T1.3 — Flex Slot (~$30/mo) — pick one
- **TIKR Pro (~$30/mo)** — fundamentals + full transcripts + filings, LLM-ingestible export
- **Koyfin Plus (~$39/mo)** — closest sub-$50 Bloomberg-alt for screens & fundamentals
- **Benzinga Pro Basic (~$27/mo)** — real-time squawk headlines with API (news-latency edge)

**Default recommendation**: **TIKR Pro** — transcripts are a text goldmine for LLM ingestion and none of the free sources have full transcript coverage.

---

## Tier 2 — Deferred (Revisit at >$500/mo Budget)

- **Bloomberg / Refinitiv / FactSet** — obvious
- **RavenPack / AlphaSense** — real news NLP; $1k+/mo minimum
- **Sentieo** — merged with AlphaSense
- **BamSEC** — better EDGAR UX, not worth vs. building it
- **Tegus / AlphaSense expert calls** — junior seat $10k+/yr
- **Estimize** — crowdsourced estimates (partial coverage vs. paid IBES)

---

## LLM Integration Architecture

The whole point of source-direct + text-heavy sourcing is to make LLM synthesis the value-add layer. Four agent shapes:

### Agent 1 — Real-time Event Alerter
Trigger: SEC 8-K / Form 4 / 13D RSS + BSE/NSE announcements + Unusual Whales flow alert.
Filter: ticker ∈ watchlist universe.
LLM pass: classify + summarize + rate materiality (1-5).
Output: Telegram/email push if materiality ≥ 4.

### Agent 2 — Nightly Positioning Digest
Trigger: End of session (US 4:30pm ET, India 4:00pm IST).
Inputs: today's NSE participant OI, latest OCC volume, SqueezeMetrics DIX/GEX, Unusual Whales daily flow report.
LLM pass: "What changed today in positioning across US and India — anomalies, extremes, divergences?"
Output: markdown digest in `data/digests/YYYY-MM-DD.md`.

### Agent 3 — Weekly Regime + Research Memo
Trigger: Sunday evening.
Inputs: Latest CFTC COT (Fri release), macro releases week-over-week, Substack digest, top 20 8-Ks by materiality, congressional trades summary.
LLM pass: Regime classification + top 10 setups (long/short) with thesis.
Output: `docs/research/memos/YYYY-WW.md`.

### Agent 4 — Idea-Flow Digester
Trigger: Weekly.
Inputs: VIC 45-day-old pitches, SumZero free tier, r/SecurityAnalysis top posts, curated FinTwit list.
LLM pass: extract thesis + variant view + risk factors → normalize into a structured "ideas" table.
Output: `experiments.idea_pipeline` table (thesis, ticker, direction, catalyst, source, ingested_at).

---

## Schema Landing Zones

Extension to the existing 8-schema per-country BCNF layout:

| Data category | Schema | Table (new) |
|---------------|--------|-------------|
| CFTC COT | `alt_positioning_us` | `cftc_cot_tff`, `cftc_cot_disaggregated` |
| NSE participant OI | `alt_positioning_in` | `nse_participant_oi_daily` |
| NSE FII/DII cash | `alt_positioning_in` | `nse_fii_dii_cash_daily` |
| NSDL FPI | `alt_positioning_in` | `nsdl_fpi_monthly` |
| Options flow / GEX | `alt_flow_us` | `uw_options_flow`, `sqm_gex_dix_daily` |
| SEC filings text | `alt_news_us` | `sec_filings_raw`, `sec_filings_classified` |
| BSE/NSE announcements | `alt_news_in` | `india_corp_announcements` |
| News RSS raw | `alt_news_global` | `news_articles_raw` |
| Reddit/StockTwits | `alt_social_us`, `alt_social_in` | `reddit_mentions`, `stocktwits_sentiment` |
| Macro series | `alt_macro` | `fred_series`, `bls_series`, `rbi_series`, `eia_series` |
| LLM digests | `derived` | `llm_digests`, `idea_pipeline` |

New schemas (`alt_positioning_us`, `alt_positioning_in`, `alt_flow_us`, `alt_news_*`, `alt_macro`) to be added via Alembic migrations per DBA agent review.

---

## Implementation Phasing

**Phase 1 (Week 1-2) — Positioning firehose (highest ROI)**
- CFTC COT TFF + Disaggregated → weekly ingest
- NSE participant OI + FII/DII cash → daily ingest
- SqueezeMetrics DIX/GEX → daily scrape
- SocGen CTA indices → daily scrape

**Phase 2 (Week 3-4) — Filings + insider firehose**
- SEC EDGAR RSS (8-K, Form 4, 13D/G) → event-driven ingest
- SEC submissions API bulk backfill
- BSE/NSE corporate announcements → daily poll
- Extend existing Congress.gov pipeline for bill-text classification

**Phase 3 (Week 5-6) — News + sentiment**
- Reuters/AP/Bloomberg Opinion RSS → real-time ingest
- Reddit API (4 subreddits) → hourly poll
- StockTwits API → daily aggregation
- Substack RSS bundle → daily ingest

**Phase 4 (Week 7-8) — Macro + LLM digest layer**
- FRED / BLS / BEA / EIA / RBI / Treasury TIC → daily/weekly ingest
- Stand up Agents 1-4 (event alerter, positioning digest, weekly memo, idea digester)

**Phase 5 (Month 3) — Paid tier evaluation**
- Trial Unusual Whales for 1 month, measure signal lift
- Add Quiver if lobbying/contracts data proves useful
- Decide flex slot based on which gap is most painful

---

## Compliance & ToS Notes

- **SEC EDGAR**: User-Agent header must include real contact — use github handle, never personal email (per user preference).
- **NSE India**: No official API; scrape is tolerated but throttled. Respect rate limits (>2s between calls), realistic User-Agent, cookie handling.
- **CFTC Socrata**: Free, but register app token to raise rate limit from 1k/day → unlimited.
- **Reddit**: Free OAuth, 100 QPM per authenticated user.
- **Substack RSS**: All allowed for personal use; do not republish content.
- **VIC / SumZero**: Read-only ingestion for research; do not republish or share downstream.
- **Unusual Whales / Quiver**: Check ToS on LLM-derived-work; both currently allow personal/derivative use as of 2026-Q3.

---

## Cross-references

- Schema layout: `docs/architecture/06-schema-rehau.md`
- Existing political pipeline (extends here): `docs/data-sources/political/pipeline.md`
- Existing short interest doc (subsumed here): `docs/data-sources/14-short-interest-positioning.md`
- Existing macro doc: `docs/data-sources/11-macro-regime.md`
- IBKR read-only role: memory `project_ibkr_role.md`
