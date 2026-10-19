# Russell 3000 daily coverage through Schwab

Research date: 2026-10-01. This note records the investigation before the implementation in this task. Repository references describe the inspected baseline, not production verification.

## Conclusion

Schwab's per-symbol daily price-history endpoint can support roughly 3,000 stocks in a paced nightly batch. The blockers are the configured membership set, daily scheduling, and verifiable completeness rather than the volume of a once-daily batch. Minute collection should be a separately bounded subset.

The existing free IWV holdings source is useful immediately, but cannot establish that every legal Russell 3000 constituent is covered. Exact current and historical membership requires an authoritative constituent file or entitled data feed. A full fetch of a holdings proxy must continue to identify its provenance as a proxy.

## Inspected collection path

- `deploy/us-universe.yaml` configures `us_configured`, `github_csv`, and only `sp500`, with a 450-member minimum. Its current configuration does not request a Russell 3000 universe.
- `components/ingest-us/src/factorlab/components/ingest_us/legacy/universe/` resolves membership and validates symbols through Schwab quotes in batches of 50. Normalization maps class shares to provider symbols and checks equity type, USD currency, and supported exchange.
- `legacy/daemon.py` consumes published daily expected series, queues symbols requiring history or latest-session updates, and separately ranks a 250-stock minute tier. Full daily history starts at 1970; later fetches resume from state. Daily membership and minute membership must remain independent.
- During market hours, the inspected loop runs an entire minute sweep before processing one minute-recovery item and one daily-recovery item. At a 300-second cadence, a 250-call sweep can occupy most of each interval and severely delay a broad daily recovery. Lowering daily priority is inappropriate for this requirement.
- `legacy/schwab/market.py` requests one `/pricehistory` symbol per call, using explicit millisecond bounds, `frequencyType=daily` or `minute`, frequency 1, and no extended hours. It archives HTTP responses before normalization, drops incomplete regular sessions, and retains valid daily candles when the vendor returns some invalid candles.
- `MarketClient` paces requests at a configurable interval, retries network errors/429/5xx up to five attempts, and handles numeric or HTTP-date `Retry-After`. Its inspected limiter is per client instance. Separate universe, legacy, and shadow processes can therefore consume the same credential budget independently.
- `providers/schwab` remains a shadow engine adapter. Changing that provider alone does not change the legacy production collector. `configs/ingestion/bindings.yaml` and `providers/schwab/CONTEXT.md` explain this boundary.

These are source-code observations. No authenticated Schwab call, live database query, production log read, backfill, or deployment was performed for this research note.

## Membership: exact Russell roster versus free proxy

BlackRock lists IWV with **2,600 holdings as of September 29, 2026**. The [first-party IWV page](https://www.ishares.com/us/products/239714/ishares-russell-3000-etf) and its downloadable holdings describe a fund portfolio. The [fund's filed summary prospectus](https://www.sec.gov/Archives/edgar/data/1100663/000119312525171590/d929840d497k.htm) describes representative sampling: the fund need not own every index security. Filtering equity rows may reduce the holdings count further.

FTSE Russell reports **3,023 Russell 3000 securities** for its April 30, 2026 rank-date universe. This is a different date and is not a same-day reconciliation, but demonstrates why a hard-coded 3,000 count and a roughly 2,600-stock fund are insufficient evidence of complete index membership. Starting in 2026 the current schedule uses June and December reconstitution; December implementation is after the December 11 close. The index also incorporates IPO and daily maintenance changes. Public downloads on the [official reconstitution page](https://www.lseg.com/en/ftse-russell/russell-reconstitution) are additions/deletions, not a complete starting roster.

[LSEG's index data catalogue](https://www.lseg.com/en/data-catalogue/indices-benchmarks) includes constituent information and history. The [official constituent-spreadsheet page](https://www.lseg.com/en/ftse-russell/index-resources/constituent-weights) offers only selected indexes and says spreadsheets are typically released within two months of reviews. This investigation did not establish a free, complete, continuously current Russell 3000 machine feed there. An entitled LSEG feed or an operator-supplied authoritative CSV is the suitable exact-membership route.

Implementation choices:

1. Enable broad daily collection from the existing IWV source as an explicitly labeled proxy; retain S&P 500 coverage if desired.
2. Support replacement with a complete authoritative roster using the existing CSV universe mechanism. Preserve snapshot date, source, index name, and raw provenance.
3. Require plausible minimum count, symbol validation, and atomic publication. Never silently discard unresolved candidates and then claim 100% membership. Retain the previous successful snapshot if validation fails.
4. Keep historical membership snapshots. Fetching current members' past prices does not reproduce historical Russell membership and creates survivorship bias in index backtests.

## Price-history availability and evidence strength

The [official Schwab Market Data specification](https://developer.schwab.com/products/trader-api--individual/details/specifications/Market%20Data%20Production) returned no browsable specification text in this investigation. Therefore the exact retention and quota are not presented as independently verified Schwab service guarantees.

The [schwab-py project's own client documentation](https://schwab-py.readthedocs.io/en/latest/client.html#price-history) reports observed availability: about 48 days for one-minute bars, about nine months for five-to-thirty-minute bars, and uncertain daily retention with AAPL observed back to 1985. These are the wrapper author's observations, not a Schwab retention contract.

The [wrapper implementation](https://github.com/alexgolec/schwab-py/blob/master/schwab/client/base.py) sends one symbol per history request. Its daily convenience method uses year/daily/frequency 1, the 20-year period enum, and explicit bounds; when no start is supplied it starts in 1971. The distinction matters: a 20-year period parameter is not proof of a universal 20-year maximum or minimum. The collector should request all available daily history and record the earliest actual valid bar per security.

No workaround can make Schwab supply history the vendor does not expose. New IPOs have shorter histories, some symbols can lack bars or contain invalid adjusted candles, and historical ticker changes or delistings need identifier-aware handling. Coverage must report these separately from pipeline failures. Do not manufacture missing bars or silently mix another provider's raw prices into Schwab's adjusted series.

## Rate limits and feasible throughput

`configs/sources/schwab.yaml` already labels **120 requests/minute approximate and not officially documented**. The older `docs/data-sources/us/schwab.md` instead calls it documented and associates it with the portal order-limit setting. That order setting should not be treated as independently proven market-data quota. Public official searches did not establish the rate's exact scope, burst window, or a contractual daily cap. Use 120/min as a planning assumption, retain a conservative configurable operating pace, and respond to real 429s.

For N symbols and sustainable R history calls/minute, the request-only lower bound is N/R. For 3,000 daily symbols:

| Operating pace | Request-only minimum | Interpretation |
| --- | ---: | --- |
| 60/min (1-second interval) | 50 minutes | Current conservative starting pace |
| 100/min (0.6-second interval) | 30 minutes | Leaves assumed quota headroom; validate operationally |
| 120/min (0.5-second interval) | 25 minutes | Assumed ceiling, no retry or other-consumer headroom |

These are calculations, not observed batch timings. HTTP latency, archives, database writes, retries, and concurrent quote/engine traffic lengthen them. A 250-stock minute tier every five minutes alone averages 50 calls/minute; a full 3,000-stock tier every five minutes would require 600 calls/minute. Daily histories do not need one request for every candle: one symbol request can return its entire available daily series.

## Recommended operational workarounds

- Reserve daily progress before minute recovery and prioritize daily histories/latest completed sessions. Run the broad sweep after the exchange close, respecting early closes and time zones; allow catch-up after restarts.
- Cap minute membership independently and permit operators to reduce or disable it while daily backlog is large. Minute retention means continuing to archive a small tier is more useful than periodically attempting an unbounded historical minute sweep.
- Use one shared credential pacing budget across request consumers where possible. Until cross-process coordination exists, explicitly account for all configured consumers and retain headroom; additional workers are not additional quota.
- Retain bounded retries, `Retry-After`, per-symbol recovery backoff, and persisted checkpoints. An empty/partial successful response must not conceal missing latest-session data or invalid candles.
- Audit expected members against actual earliest/latest daily bars, unresolved symbol mappings, invalid candles, and exchange-session gaps. A state flag that a response was traversed is weaker than coverage completeness.
- For missing vendor history or precise historical membership, use a separate licensed EOD provider/constituent feed with explicit source and adjustment semantics. Streaming may reduce minute polling demand, but does not provide historical daily backfill.

The acceptance target is every published daily member processed and fresh through the latest completed session, with explicit vendor/mapping exceptions. Exact Russell completeness additionally requires reconciliation against an authoritative dated roster.
