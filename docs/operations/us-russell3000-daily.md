# Russell 3000 daily collection

Schwab can fetch daily OHLCV for roughly 3,000 equities in a paced nightly pass.
The membership source determines whether this is the entire Russell 3000.
The default `deploy/us-universe.yaml` currently selects S&P 500; it is not a
Russell roster. See [the cited investigation](../research/schwab-russell3000-daily-coverage.md).

## Scope and prerequisites

Use an authoritative current constituent file from an entitled FTSE Russell/LSEG
feed or another source with a verified exact roster. IWV uses representative
sampling and cannot guarantee every constituent. The CSV adapter does not claim
to authenticate a file's origin: the operator must establish that provenance.
An expected count is a truncation check, not proof that a file is authoritative.

The file must contain a `Symbol` column, one security per row. Include every
share class. `BRK.B` and `BRK/B` normalize to `BRK-B`; blanks, invalid symbols,
duplicates after normalization, incorrect counts, stale timestamps, and any
Schwab validation failure reject publication. A failed refresh retains the
previous published universe. Raw roster bytes, SHA-256, effective timestamp,
source URL and Schwab quote responses are archived.

The code supports exact membership ingestion but does not include a licensed
constituent file. Until that file is provided and the configuration activated,
the existing S&P 500 scope continues. No production deployment or backfill was
performed as part of this change.

## Activation after a reviewed release

1. Release the platform directory creation and `ingest-us` component changes using
   [the normal release workflow](../../deploy/README.md). The universe service
   mounts `/etc/factorlab/constituents` at `/app/config/constituents` read-only;
   `FACTORLAB_US_CONSTITUENTS_DIR` can select another allowed host directory.
2. Install the roster at `/etc/factorlab/constituents/russell3000.csv` with mode
   `0640`, owner root, group factorlab. Replace files atomically. Never commit
   licensed roster data to the repository.
3. Adapt [the exact-roster template](../../configs/universes/us_russell3000.yaml)
   into `/etc/factorlab/us-universe.yaml`. Set `expected_constituents` to the
   security count in the authoritative file, `as_of` to its effective timestamp
   with timezone, and `source_url` to its actual provenance. The template's 3023
   is an example, not a permanent index count. `max_age_days` defaults to 7;
   refresh the roster and effective timestamp regularly, without misdating old data.
4. Restart `universe-us` through the reviewed maintenance workflow. It validates
   every symbol in 50-symbol Schwab quote batches at a two-second pacing floor
   before publishing. The collector discovers membership within 60 seconds and
   queues daily history for every new member.
5. Set `US_MINUTE_TIER_SIZE` to 100 for a smaller minute tier, or 0 for daily only,
   in the operator environment and recreate `ingest-us`. Default is 250. This
   changes only minute scope; daily scope always contains the full published roster.

## Scheduling and rate budget

Daily requests need one call per symbol, not one call per bar. A 3,000-symbol pass
at the default one-second floor has a 50-minute request-only lower bound; response
latency, writes and retries add time. The universe validator adds roughly 60 calls
per roster refresh, at at most 30 calls/minute. Conservatively, the two production
consumers can reach about 90/minute together. No cross-process limiter exists.

The often quoted 120/minute is an approximate planning assumption, not a verified
market-data contract. Confirm the approved app's current limits before increasing
`SCHWAB_MIN_REQUEST_INTERVAL_SECONDS` throughput. Avoid concurrent engine/shadow
traffic during broad batches; a second worker shares the same credential quota.
429 and 5xx retries are bounded, honor `Retry-After`, and preserve the cooldown
even when a symbol exhausts its retries. Do not create extra accounts to bypass
limits. The shadow provider configuration also defaults to one request/second.

The full-universe collector prioritizes runnable daily requests over live minute
sweeps and minute recovery. Missing bars retain their queue entry and exponential
15-minute-to-24-hour retry backoff. A deferred symbol does not stop other daily
members or minute work. Weekly full-history refreshes revisit adjusted data and
invalid candles; invalid rows remain recorded as partial results. Daily bars are
collected only after the latest exchange session closes, with the existing grace
period, holiday, early-close and DST handling. Initial histories can run at any time.

## What completeness means

Three separate checks must pass:

- Membership: published daily members equal the dated authoritative roster, not
  merely a target count or ETF holdings.
- Freshness: every expected member has a daily bar for the latest completed session,
  or an explicit unresolved vendor/mapping exception. Empty responses are failures.
- History: session coverage has no unexplained gaps from each security's first
  valid returned candle through the latest completed session. A recovery flag alone
  does not prove this; `history_complete` means the requested response was traversed.

Use `meta.expected_series`, `meta.recovery_state`, `meta.session_coverage`, and
`meta.ingestion_runs` for verification. For example, a read-only membership count:

```sql
SELECT universe, count() AS daily_members
FROM meta.expected_series FINAL
WHERE country_code = 'US' AND source = 'schwab' AND resolution = 'daily' AND active
GROUP BY universe;
```

Daily price-history requests explicitly use `periodType=year`, `period=20`,
`frequencyType=daily`, `frequency=1`, and date bounds starting in 1970 for initial
history. The period is not a guaranteed retention limit. The calendar excludes
pre-1970 candles. Minute availability around 48 days is observational; older
minute data must already have been archived or obtained elsewhere.

The response has no separate adjusted-close field. Keep provider price semantics
and existing null `adj_close`; do not infer dividend-adjusted returns. Current
membership backfills have survivorship bias and do not reconstruct historical
Russell membership or delisted-security history. An independent licensed EOD
feed can fill unavailable history under its own source and price basis; it must
not silently overwrite Schwab bars. Streaming can reduce future minute REST load
but cannot replace historical daily ingestion.
