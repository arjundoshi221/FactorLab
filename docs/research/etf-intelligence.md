# ETF Intelligence — Sources, Signals & Consumption Playbook

> Status: `[research]` · Last updated: 2026-09-21 · Owner: Arjun · Data-source foundation ([`20-edgar-sec-filings.md`](../data-sources/20-edgar-sec-filings.md)) live at API-surface tier.

## Purpose

Build a durable operating picture of the ETF universe so we can:
- **Spot new products early** — before they land in retail flow / news cycle
- **Read flows as sentiment** — money-in/out leads narrative, price is the lagging tell
- **Understand structural shifts** — issuer fee cuts, category maturation, thematic exhaustion
- **Leverage inside FactorLab** — turn the flow/holdings feed into a look-through factor screen so ETFs are first-class citizens in universe construction and portfolio construction

This doc is *research-facing*, not a data-source spec. It covers who to listen to, what to read, how to consume it, and how to operationalize it. For the pipeline/schema side see `docs/data-sources/18-fund-flows-buybacks-shareholding.md`.

---

## Part 1 — Landscape (one-page context)

Roughly 3,800 US-listed ETFs, ~$12T AUM. The industry is concentrated:

- **Top 3 issuers** (BlackRock/iShares, Vanguard, State Street/SPDR) hold ~75% of AUM.
- **Top 20 ETFs** hold ~50% of all US ETF AUM (VOO, IVV, SPY, QQQ, VTI, IWM, etc.).
- **New launches** run 300–500/year. Survivorship is brutal — ~30% close within 3 years.
- **Fee floor** for cap-weighted US equity has been driven to 0.03% (VOO, IVV). Anything charging >0.20% needs a story.
- **Growth categories 2024–2026**: actively managed ETFs, covered-call/derivative-income, single-stock leveraged, crypto, AI/thematic.

This shape matters because it tells you *where alpha in tracking the industry lives*: not in the top 20 (well-covered, indexed to death) but in **new launches, thematic rotations, active ETF migration from mutual funds, and structural fee/regulation shifts**.

---

## Part 2 — Sources

Organized in four tiers by signal-per-minute. Start at the top.

### Tier 1 — People (highest signal, near-real-time)

The ETF ecosystem is small; the same 8-10 people break every meaningful story. Following them replaces most industry news feeds.

| Person | Role | Where | Why they matter |
|---|---|---|---|
| **Eric Balchunas** | Sr. ETF Analyst, Bloomberg Intelligence | X: `@EricBalchunas` · [Trillions podcast](https://www.bloomberg.com/podcasts/series/trillions) · Bloomberg ETF IQ TV | The single most important account. Breaks launch news, flow anomalies, issuer moves within hours. Author of *The Bogle Effect* + *Institutional ETF Toolbox*. |
| **Athanasios Psarofagis** | ETF Analyst, Bloomberg Intelligence | X: `@AthanasiosPsar` | Balchunas's partner; more data-heavy, less noisy. Best flow charts on the platform. |
| **Nate Geraci** | President, The ETF Store | X: `@NateGeraci` · [ETF Prime podcast](https://etfprime.com/) | Hosts the industry's best weekly podcast. Guest lineup covers every corner (issuers, index providers, structure specialists). |
| **Dave Nadig** | Financial Futurist, VettaFi (formerly ETF.com CIO) | X: `@DaveNadig` · frequent podcast guest | Structure/tax/regulatory brain of the industry. Best explainer for *why* a novel wrapper matters. |
| **Todd Rosenbluth** | Head of Research, VettaFi | X: `@ToddRosenbluth_` | Category-level research; regularly on ETF Prime. Covers active ETF migration + inflows/outflows deeply. |
| **Todd Sohn** | ETF & Technical Strategist, Strategas | X: `@ToddSohn` | Overlay of flows + technicals. Best "positioning" reads in the business. |
| **Kirsten Chang** | Sr. Industry Analyst, VettaFi | X: `@KirstenChang` | Focus on *new launches* — AI, space, crypto, novel thematics. First to write up most novel funds. |
| **Jared Dillian** | Newsletter author, ex-Lehman | X: `@dailydirtnap` | Sentiment/positioning contrarian. Not ETF-pure but useful sanity check on crowding. |

**Consumption rule:** Follow all 8 on X. Turn on notifications for Balchunas + Psarofagis. Their signal-to-noise on ETF-specific events is ~10x anything else on the platform.

### Tier 2 — Podcasts & long-form (weekly rhythm)

| Podcast | Cadence | Length | Value |
|---|---|---|---|
| **[Trillions (Bloomberg)](https://www.bloomberg.com/podcasts/series/trillions)** | Weekly | 20–30 min | Balchunas + Joel Weber. Dense, current, no filler. **Non-negotiable weekly listen.** |
| **[ETF Prime (VettaFi/Nate Geraci)](https://etfprime.com/)** | Weekly | 45–60 min | Guest-driven; rotates through issuers + specialists. Best long-form coverage of new products and category evolution. |
| **[Excess Returns](https://www.excessreturnspod.com/)** | Weekly | 45 min | Broader factor/quant angle; ETF episodes when guest warrants. |
| **[Animal Spirits (Batnick/Carlson)](https://theirrelevantinvestor.com/animalspirits)** | 2x/week | 45 min | Retail-flow zeitgeist. Useful to hear what *advisors* are seeing on the ground. |
| **The Long View (Morningstar)** | Weekly | 45 min | More manager-interview than ETF-first, but Morningstar's angle is worth the rotation. |

**Consumption rule:** Trillions + ETF Prime every week = ~90 min. That is the floor.

### Tier 3 — Newsletters & written research

**Free / low cost:**

| Source | Cadence | Format | Coverage |
|---|---|---|---|
| **[ETF.com newsletter](https://www.etf.com/newsletter)** | Daily + weekly | Email | Daily flow leaders, launches, industry news. Free. |
| **[ETFGI Research](https://etfgi.com/research)** | Monthly + weekly | Email + PDF | Global industry stats (AUM, flows, launches). Free tier is usable. |
| **[TrimTabs Daily Liquidity Report](https://en.wikipedia.org/wiki/TrimTabs_Investment_Research)** | Daily | Email | Fund flows by asset class, style, industry, category. Paid but sometimes syndicated. |
| **[VettaFi ETF Trends](https://www.etftrends.com/)** | Daily | Web | Category commentary, launch coverage. |
| **[Morningstar US Fund Flows](https://www.morningstar.com/business/insights/blog/funds/us-fund-flows)** | Monthly | Blog | The reference report for monthly flow attribution. Free. |
| **[State Street SPDR Chart Pack](https://www.ssga.com/us/en/intermediary/insights/state-street-etfs-chart-pack)** | Monthly | PDF | Institutional-quality charts — flows, valuations, factors, sectors. **Free and excellent.** |

**Paid (worth it once ETF work becomes core):**

| Source | Cost | Why |
|---|---|---|
| Bloomberg Intelligence (via Terminal) | $$$ | Balchunas / Psarofagis publish here first |
| Morningstar Direct | $$ | Best classification + ratings + attribution |
| FactSet ETF Analytics | $$$ | Real-time flow alerts, AP-level create/redeem |
| ETFGI Pro | $$ | Global AUM + launch database with API |

**Substacks / independent research:**
- **The Diff** (Byrne Hobart) — occasional ETF/structure pieces, always sharp.
- **Fabricated Knowledge** (Doug O'Laughlin) — semis-heavy, matters for anyone tracking chip ETFs.
- **Doomberg** — commodity/energy angle; useful for XLE/USO/energy thematic context.

### Tier 4 — Primary data & official filings

This is where automation lives. Everything above is human-consumed; below is machine-readable.

#### 4A. SEC EDGAR (free, canonical)

The regulatory truth. Every US ETF files here and nowhere else matters.

| Form | Filed | Purpose | Signal |
|---|---|---|---|
| **N-1A** | Before launch | Initial registration for open-end fund/ETF | First public signal a new ETF is coming |
| **N-1A/A** | Pre-effective amendments | Prospectus revisions | Refines strategy details |
| **497K** | On effective date | Summary prospectus | Officially "live" |
| **N-CEN** | Annually | Census filing (fund-level facts) | Reference data: sub-adviser, custodian, auditor, fee waivers |
| **N-PORT** | Monthly (60-day lag) | Full portfolio holdings | **The gold** — look-through holdings for every US ETF |
| **N-CSR / N-CSRS** | Semi-annually | Financial statements + certified holdings | Deep detail incl. securities lending revenue |
| **24F-2** | Annually | Registration fee filing | Signals AUM scale used for SEC fee calc |
| **8.3-N** | On rebalance | Series/class trust filings | Structure-level changes |

**Access:** wired in this repo — see [`docs/data-sources/20-edgar-sec-filings.md`](../data-sources/20-edgar-sec-filings.md) for the full spec (form catalog, per-form field schemas, parsing recipes) and [`data/edgar/samples/CATALOG.md`](../../data/edgar/samples/CATALOG.md) for a real sample of each form. API surface at [`src/factorlab/sources/edgar/`](../../src/factorlab/sources/edgar/):

- `EdgarClient` — UA-authenticated, 10 rps throttled, retries with `Retry-After`
- `get_daily_filings()` / `get_quarterly_index()` — the .idx catalog (form filter supported)
- `get_submissions(cik)` — every filing by a filer
- `get_company_facts()` / `get_company_concept()` / `get_frame()` — XBRL JSON
- `search()` — full-text search since 2001
- Form-type RSS feeds via `form_rss_url("N-1A")`

Pattern: pull the daily N-1A/497K index every AM, diff against known issuers, alert on genuinely new products. Cost = $0. Third-party wrappers (`edgartools`, `sec-api.io`, `kscope.io`) unnecessary — we own the primary path.

#### 4B. Fund flow data

Fund flows are the highest-signal daily datapoint in ETF-land. Where to get them:

| Source | Access | Frequency | Coverage | Cost |
|---|---|---|---|---|
| **ETF.com Flow Tool** | Web scrape | Daily | Per-ETF net flows | Free |
| **ETFdb.com** | Web scrape | Daily | Per-ETF, categorized | Free |
| **ICI weekly ETF report** | PDF/CSV | Weekly | Aggregate by category | Free |
| **Morningstar Direct** | Terminal | Daily | Per-fund, historical | $$ |
| **FactSet Funds API** | API | Real-time | Per-fund, AP-level | $$$ |
| **Bloomberg** | Terminal | 15-min refresh | Per-ETF, real-time-ish | $$$ |
| **ETF Global (via Massive)** | REST API | Daily | Per-ETF, classified | $ |
| **EODHD Fundamentals** | REST API | Daily | Basic per-ETF metadata + AUM | Already on our plan |
| **Twelve Data ETF API** | REST API | Daily | Metadata + holdings snapshots | $ |

**Path for FactorLab:** Confirm EODHD's ETF endpoint coverage first (we already pay for it). Fallback to daily ETF.com scrape for what EODHD doesn't cover. Add FactSet later if we want AP-level create/redeem.

#### 4C. Holdings data (look-through)

| Source | Format | Frequency | Notes |
|---|---|---|---|
| **N-PORT via EDGAR** | XML | Monthly, 60-day lag | Complete, canonical, free |
| **Issuer daily holdings files** | CSV/XLS | Daily | On each issuer's site — iShares, Vanguard, SPDR, Invesco all publish daily. **Free and fresh.** |
| **Financial Modeling Prep** | JSON | Daily-ish | Aggregates issuer files; free tier available |
| **Morningstar Direct** | GUI/export | Daily | Categorized, but paid |

**Path for FactorLab:** Scrape issuer daily-holdings pages for the top 200 ETFs (covers 95% of AUM). Fall back to N-PORT for the long tail.

#### 4D. Reference data (structure, index, fees)

- **NASDAQ Fund Network** — daily NAV/shares outstanding for every US-listed fund.
- **CBOE ETF listings** — for CBOE/BATS-listed funds.
- **Issuer sites** — index methodology PDFs, fee schedules, tax character.
- **Index providers** — MSCI, S&P DJI, FTSE Russell, CRSP, Solactive publish index rules directly.

---

## Part 3 — What signals actually matter

The whole point of consuming the above is to catch signals *before* they show up in price. Ranked by lead time:

1. **N-1A launch pipeline** — 60–90 days lead time on new products. Cluster of similar filings = an emerging theme. E.g., a wave of "AI infrastructure" N-1A filings in early 2024 predicted the 2025-26 semiconductor-ETF explosion.
2. **Issuer fee cuts on a category** — signals category maturation. When Vanguard/iShares/Schwab drop fees on a category, they've decided it's a permanent staple, not a fad. Bullish for the category's long-term AUM, bearish for high-fee incumbents.
3. **Net creations/redemptions** — daily. Flows lead narrative by weeks. An ETF up 40% on price with net outflows means late buyers are being distributed to by early ones. Divergence is the signal.
4. **AUM concentration in a thematic** — when top-3 holdings exceed 40% of AUM, the ETF is a single-name bet in disguise (SMH ≈ NVDA + TSMC proxy). Useful for constructing hedges.
5. **Options open interest** on SPY/QQQ/IWM/HYG/TLT — where institutional hedging sits. Skew changes lead vol regimes.
6. **Bid/ask spread widening** intraday — early sign of stress in illiquid ETFs (fixed income, EM small-cap, single-country).
7. **Premium/discount blowouts** — persistent premium >0.5% on liquid EM/intl ETFs = the underlying market is closed or stressed. Discount = forced selling.
8. **Active-to-ETF conversions** — mutual fund manager launching an ETF or converting a mutual fund is a structural signal: they're bringing (or trying to bring) their track record and existing AUM.
9. **Category outflows** — 4+ consecutive weeks of category outflows often mark late-cycle sentiment washouts (contrarian long) or beginning of secular declines. Context-dependent.

---

## Part 4 — Consumption routines

Discipline > volume. Fixed cadences beat "when I remember."

### Daily (5–10 min, weekday AM)

- **X feed** — Balchunas, Psarofagis, Chang. Skim, star anything notable.
- **ETF.com flow leaders** — top 10 inflow / top 10 outflow. Note anomalies against yesterday.
- **N-1A filings diff** — automated job posts to Slack/email. Human-review only novel issuers or novel structures.

### Weekly (60–90 min, Sunday PM)

- **Trillions podcast** (~25 min)
- **ETF Prime podcast** (~50 min)
- **ETFGI weekly + ICI weekly** — 5-min skim of aggregate flow numbers by category.
- **Personal notes** — one paragraph: "what shifted this week." Compounds.

### Monthly (2 hr)

- **State Street SPDR Chart Pack** — go slow, take notes on flow attribution and positioning charts.
- **Morningstar US Fund Flows** blog post — category-level narrative.
- **ETFGI monthly newsletter** — global stats.
- **N-PORT holdings diff for top 100 ETFs** — automated. Human-review notable changes.
- **New-launch retrospective** — 30/60/90-day AUM growth of launches from previous month. Which ones "took."

### Quarterly

- **13F season** — cross-reference top ETFs' active-manager cousins to see if PMs disagree with the passive tape.
- **Category review** — what worked, what didn't, what's crowded, what's abandoned. Adjust attention allocation.

---

## Part 5 — How to operationalize inside FactorLab

The consumption above is human-side. Everything below is what we can automate in the platform. Ordered by dependency + value.

### Phase 1 — Reference layer (1 week)

- Extend `ref.securities` — add `is_fund`, `structure` (`ETF`|`ETN`|`MF`|`CEF`|`UIT`), `issuer`, `launch_date`, `benchmark_index`, `expense_ratio_bps`, `distribution_yield`.
- New: `ref.fund_registry` (per-ETF) — populated from EDGAR N-CEN + issuer sites.
- Universe configs: `configs/universes/{us_etfs_core.yaml, us_etfs_thematic.yaml, us_etfs_factor.yaml}`. Mirror the shape of the existing `us_mutual_funds.yaml` stub.

### Phase 2 — Launch pipeline (1 week)

**Foundation ready** — EDGAR client + N-1A/497K discovery via `get_daily_filings(client, day, forms={"N-1A", "N-1A/A", "497K", "497"})`. Only DB wiring pending.

- `scripts/us/edgar/us_edgar_launches_daily.py` — daily job. Pull N-1A + N-1A/A + 497K filings. Diff against `ref.fund_registry`. Emit a daily digest.
- Storage: `market.fund_launch_events` (small table — event log).
- Alerting: file into `data/etfs/launches/YYYY-MM-DD.md` for weekly review.

### Phase 3 — Flows & metrics (2 weeks)

- `market.fund_flows` — daily net creations/redemptions, per-ETF. Source: EODHD if available, else ETF.com scrape.
- `market.fund_metrics` — daily AUM, shares_outstanding, NAV, premium_discount. Compute from prices + shares_out.
- `scripts/us/etfs/us_etfs_flows_daily.py` — daily job, runs after close.

### Phase 4 — Look-through holdings (2–3 weeks)

**Foundation ready** for N-PORT path — the EDGAR client already fetches primary_doc.xml; parser recipe in [`20-edgar-sec-filings.md § Parsing recipes`](../data-sources/20-edgar-sec-filings.md#parsing-recipes). Working sample of SPY holdings (504 positions, $781B NAV) at [`data/edgar/samples/nport_p/`](../../data/edgar/samples/nport_p/).

- `ref.fund_holdings` (already in intl expansion memo) — one row per (fund, security, as_of_date).
- Two ingesters:
  - `scripts/us/etfs/us_etfs_issuer_holdings_daily.py` — scrape issuer daily-holdings CSV for top 200 ETFs (faster than N-PORT's 60-day lag).
  - `scripts/us/edgar/us_edgar_nport_monthly.py` — parse N-PORT XML for the long tail; fills gaps.
- Enables: bottom-up factor exposure of any ETF against our factor library in `derived`.

### Phase 5 — Analytics that unlock the point of doing all this

Once the above lands, these screens become one-line SQL:

- **"ETFs launched in last 90 days that raised >$100M"** — where's real money going, not just hype
- **"ETFs with top-3 holdings concentration >50%"** — hidden single-name risk
- **"ETFs where 30-day flow > 20% of AUM"** — sentiment surges
- **"Cleanest QUAL+MTUM look-through I'm not already getting from direct book"** — hedging + diversification
- **"Sector ETFs whose flow direction diverges from their price direction >5%"** — the divergence-lead signal from Part 3
- **"Legislator PTR ↔ ETF underlying overlap"** — cross-connect to `alt_political_us` (political signal on ETF constituents)

---

## Part 6 — Watch-outs

- **Chasing YTD winners is the median mistake.** DRAM launched April 2026 up huge; you didn't own it in April. The 138% on PSI is realized only for those who held Jan 1. Track *forward* AUM and flows, not backward returns.
- **Thematic post-launch is often mean-reversion in disguise.** AI-themed ETFs launched 2023–24 are now in the "chase" phase. Late-launch thematics rarely deliver.
- **Leveraged/inverse decay via daily rebalancing** — TQQQ, SOXL etc. are day-trading instruments, not holds >30 days.
- **Actively managed "growth" ETFs** often underperform QQQ after fees over 5yr — pay for demonstrable edge, don't just pay.
- **Dividend traps** — highest-yield ETFs are often distressed sectors. SCHD screens against this; many high-yield ETFs don't.
- **ETF ≠ ETN ≠ commodity pool ≠ UIT.** Structural differences drive tax, credit, and roll-cost outcomes. Always check `structure` before comparing.

---

## Open Questions

- **EODHD ETF coverage depth** — do we get flows + holdings, or only prices + basic reference? Need to probe in playground.
- **Issuer daily-holdings scraping legality** — every issuer publishes freely on their site but ToS varies. Do we need any commercial license?
- **N-PORT parsing library** — `edgartools` covers basics; check whether it handles the newer NPORT-P XML schema cleanly or if we need our own parser.
- **International ETF universe** — European UCITS ETFs file differently (KIID docs, not N-1A). Do we treat as a separate stream or unify?
- **Cross-connect to political data** — can we build a "legislator ETF exposure" screen from the existing `alt_political_us` PTR data + `ref.fund_holdings` join? Feels like a natural first-analytics deliverable.

---

## References

- [Bloomberg Trillions Podcast](https://www.bloomberg.com/podcasts/series/trillions)
- [ETF Prime Podcast — Nate Geraci](https://etfprime.com/)
- [ETF.com Fund Flow Tool](https://www.etf.com/etfanalytics/etf-fund-flows-tool)
- [Morningstar US Fund Flows](https://www.morningstar.com/business/insights/blog/funds/us-fund-flows)
- [State Street SPDR Chart Pack](https://www.ssga.com/us/en/intermediary/insights/state-street-etfs-chart-pack)
- [ETFGI Research](https://etfgi.com/research)
- [SEC EDGAR Developer Resources](https://www.sec.gov/about/developer-resources)
- [SEC EDGAR RSS Feeds](https://www.sec.gov/about/rss-feeds)
- [ETF Global via Massive REST API](https://massive.com/docs/rest/partners/etf-global/fundflows)
- [FactSet Funds API](https://developer.factset.com/api-catalog/factset-funds-api)
- Related internal: [docs/data-sources/18-fund-flows-buybacks-shareholding.md](../data-sources/18-fund-flows-buybacks-shareholding.md)
