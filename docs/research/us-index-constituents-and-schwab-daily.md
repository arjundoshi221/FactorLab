# US index constituents without EODHD, and Schwab daily OHLCV

Research date: 2026-09-22

## Recommendation

For FactorLab's current requirement (resolve the current S&P 500 ticker set and
collect daily OHLCV), EODHD is not necessary.

Use two independent, no-cost constituent sources:

1. Treat the open [`datasets/s-and-p-500-companies` CSV](https://github.com/datasets/s-and-p-500-companies/blob/main/data/constituents.csv)
   as the machine-readable membership candidate.
2. Cross-check it against BlackRock's first-party [IVV holdings CSV](https://www.ishares.com/us/products/239726/ishares-core-s-p-500-etf/latest-holdings.csv),
   filtered to equity holdings. IVV tracks the S&P 500, but its portfolio is a
   proxy rather than the legal index roster.
3. Normalize class-share symbols at the provider boundary (`BRK.B` / `BRK B`
   to Schwab's `BRK/B`), validate every candidate through Schwab's instrument
   or quote API, and publish atomically only when count, overlap, and symbol
   validation pass. Keep the previous successful universe on any failure.
4. Use Schwab Price History for daily OHLCV. The endpoint is one-symbol-per-call,
   so an S&P 500 backfill or refresh is roughly 500 calls. This is practical as
   a paced batch, and FactorLab already contains working Schwab daily-fetch and
   backfill code.

This removes EODHD from the current path. It does **not** replace EODHD's full
exchange master, raw/unadjusted prices, corporate actions, statements, or other
fundamental datasets if those become requirements later.

## Constituent-source findings

### 1. There is no confirmed free, official S&P constituent API

S&P DJI's public S&P 500 page identifies the index and shows its constituent
count and top ten, but not a public, stable full-roster API. S&P DJI says its
Data Services APIs include constituents and corporate actions, require a Data
Services account and authenticated token, and are subscription services.
Therefore, the authoritative programmatic source is commercial, not open
source or confirmed free.

Sources: [official S&P 500 page](https://www.spglobal.com/spdji/en/indices/equity/sp-500/),
[official S&P DJI API Data Solutions](https://www.spglobal.com/spdji/en/landing/topic/api-data-solutions/).

The SEC and listing exchanges cannot substitute for the index owner: they can
identify registrants and listed securities, but S&P 500 membership is an index
selection maintained by S&P DJI. They remain useful for validating securities,
not for deriving membership.

### 2. Best open-data option: `datasets/s-and-p-500-companies`

The repository provides a directly consumable constituent CSV. Its own source
code shows that it scrapes the first table of Wikipedia's “List of S&P 500
companies,” and its GitHub Actions workflow runs every day at 00:00 UTC. The
repository declares the data under ODC PDDL 1.0 and the code under MIT/BSD.

Sources: [repository README and declared license](https://github.com/datasets/s-and-p-500-companies),
[scraper source](https://github.com/datasets/s-and-p-500-companies/blob/main/scripts/scrape.py),
[daily workflow](https://github.com/datasets/s-and-p-500-companies/blob/main/.github/workflows/actions.yml),
[data package provenance and license](https://github.com/datasets/s-and-p-500-companies/blob/main/datapackage.json).

Important limitation: the upstream roster is community-maintained Wikipedia,
not S&P DJI. Open-source software does not make the underlying facts official.
The repository's PDDL declaration also does not erase the upstream Wikimedia
terms; the conservative approach is to retain provenance and attribution.
Wikimedia permits reuse under open licensing and documents attribution and
share-alike obligations for covered content.

Source: [Wikimedia Foundation Terms of Use](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use).

Reliability controls should include:

- reject malformed, empty, or implausibly small/large rosters;
- require unique normalized symbols and preserve multiple share classes;
- compare against the prior roster and reject unexpectedly large daily churn;
- require very high overlap with IVV equity holdings;
- validate each resulting symbol with Schwab before activation;
- retain a dated raw snapshot and the last-known-good published universe.

### 3. Best first-party free cross-check: iShares IVV holdings

BlackRock states that IVV seeks to track the S&P 500 and publishes a dated,
downloadable holdings CSV. It also states that the fund invests all or
substantially all assets in index components in approximately the index's
proportions. This makes IVV an excellent independent cross-check and a workable
emergency source.

Sources: [official IVV product and holdings page](https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf-ivv),
[official latest-holdings CSV](https://www.ishares.com/us/products/239726/ishares-core-s-p-500-etf/latest-holdings.csv),
[official IVV fact sheet](https://www.ishares.com/us/literature/fact-sheet/ivv-ishares-core-s-p-500-etf-fund-fact-sheet-en-us.pdf).

It is still a fund portfolio, not the index owner's constituent file. Holdings
can contain cash, derivatives, or temporary tracking positions, and can differ
around index-change settlement dates. Filter `Asset Class == Equity`, but do not
silently assume the result is the legal index roster.

The CSV is free to download but is not published under an identified open-data
license. “Publicly downloadable” and “openly licensed for redistribution” are
different. Internal ingestion is the safer intended use; seek a terms/license
review before redistributing a derived roster.

### 4. Sources not recommended as the sole production feed

- `yfinance` and similar wrappers are open-source software over unofficial
  upstream interfaces; they do not turn Yahoo data into an official or openly
  licensed constituent feed.
- “Top 500” holdings from a total-market/Russell ETF are not the S&P 500. S&P
  membership is committee-selected and is not exactly the 500 largest stocks.
- Scraping S&P's interactive site or a logged-in response creates a brittle,
  potentially license-sensitive dependency. Use the licensed API if an
  authoritative S&P feed becomes mandatory.

## Schwab daily OHLCV findings

### Supported endpoint and request shape

Schwab's Individual Trader product includes Market Data, but its detailed
specification is rendered behind the developer portal rather than as a stable,
anonymous document. The market-data call used by FactorLab and the maintained
open-source `schwab-py` client is:

```text
GET https://api.schwabapi.com/marketdata/v1/pricehistory
    ?symbol=AAPL
    &periodType=year
    &period=20
    &frequencyType=daily
    &frequency=1
    &startDate=<epoch-ms>
    &endDate=<epoch-ms>
    &needExtendedHoursData=false
```

The response supplies `open`, `high`, `low`, `close`, `volume`, and `datetime`
inside `candles`. Price History accepts one `symbol`, unlike Schwab's batch
quotes endpoint.

Sources: [official Schwab Trader API product portal](https://developer.schwab.com/products/trader-api--individual),
[`schwab-py` endpoint implementation and enums](https://github.com/alexgolec/schwab-py/blob/main/schwab/client/base.py),
[`schwab-py` repository example](https://github.com/alexgolec/schwab-py).

The client exposes these raw parameter values:

- `periodType`: `day`, `month`, `year`, `ytd`;
- year periods: 1, 2, 3, 5, 10, 15, or 20;
- `frequencyType`: `minute`, `daily`, `weekly`, `monthly`;
- daily/weekly/monthly frequency value: 1;
- explicit `startDate` and `endDate` in Unix epoch milliseconds;
- `needExtendedHoursData` and `needPreviousClose` flags.

The maintained wrapper's daily helper requests a 20-year/daily series and says
that some symbols have been observed further back (AAPL to 1985), but it
explicitly calls the exact retention period unclear. Treat 20 years as the
normal request horizon, not a contractual completeness guarantee.

Source: [`schwab-py` price-history documentation/source](https://github.com/alexgolec/schwab-py/blob/main/schwab/client/base.py).

### Authentication and operational limits

Schwab access requires an approved developer application and OAuth bearer
tokens. FactorLab already has a Schwab credential-refresh path and read-only
market-data client, so this is not a new provider onboarding exercise.

The public Schwab portal does not expose a durable anonymous page stating a
general request-rate number. The frequently repeated “120 requests/minute” is
not safe to present as a current contractual Market Data limit without checking
the authenticated app/product screen. FactorLab's current client deliberately
paces at one request/second and retries `429` and `5xx`, honoring
`Retry-After`. At that conservative pace:

- full 500-symbol daily request pass: at least about 8 minutes plus response and
  write time;
- daily incremental refresh: the same request count, but only a few candles per
  response;
- initial 20-year backfill: still about 500 API calls because one response can
  contain the symbol's daily history.

This is suitable for a post-close batch, but not for a strict simultaneous
cross-sectional snapshot. Use bounded concurrency only after confirming the
actual limit shown for the approved Schwab app.

### Adjustment behavior

The Price History candle has no separate `adjusted_close` field and no request
parameter selecting raw versus adjusted history. Schwab's public portal does
not provide a clear anonymous contractual statement about split/dividend
adjustment behavior.

FactorLab's existing implementation records an empirical finding that Schwab
daily OHLC and volume are split-adjusted (verified internally around AAPL's
2020 split), copies `close` into `adj_close`, and notes that raw close is not
available. That is useful evidence, but it should remain labeled an observed
provider behavior rather than a guaranteed API contract. Before making Schwab
the sole daily source, add a regression canary around known splits and compare
periodically with an independent source. Do not infer total-return adjustment:
there is no distinct dividend-adjusted close in the response.

Relevant FactorLab sources: [`candles.py`](../../src/factorlab/countries/us/equities/schwab/candles.py),
[`market.py`](../../src/factorlab/sources/schwab/market.py), and the existing
[Schwab source notes](../data-sources/us/schwab.md).

## Proposed source architecture

```text
open Wikipedia-derived CSV ----\
                                +-- validate/normalize --> active S&P universe
official IVV holdings CSV -----/             |
                                              +--> Schwab symbol validation
                                              +--> Schwab daily OHLCV
```

Publishing gates should be stricter than either input alone: expected security
count near S&P's published current count, high cross-source overlap, all symbols
valid as Schwab equities, limited churn versus last-known-good, and atomic
activation. A source disagreement should alert and retain the last-known-good
universe rather than guessing.

## Bottom line

Yes, a no-EODHD design is viable for today's scope. The data source can be free
and the ingestion code open source, but there is no equally authoritative,
openly licensed S&P constituent feed. The practical compromise is an open
Wikipedia-derived roster with BlackRock IVV corroboration, followed by Schwab
validation and Schwab daily OHLCV. This is operationally adequate for research;
buy S&P DJI constituent data if official membership and redistribution rights
become hard requirements.
