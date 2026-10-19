# Collector engine cutover — 2026-10-01

## Source changes

India, US prices/universe resolution and political bootstrap now call maintained
dataset sources through the shared ingestion engine and canonical sinks. The CLI
commands, curated source names, frozen job pipelines and existing listing UUIDs
remain compatible. The component legacy modules and their exclusive storage
writers are removed; legacy-only tests are replaced by source and sink checks.

US daily-first scheduling, checkpoint overlap, weekly history refresh, retry
backoff, minute-tier controls and exact operator CSV validation remain. Complete
index snapshots are staged until every member validates through Schwab. A derived
union manifest archives the configured recipe and upstream capture provenance.
Unresolved membership snapshots cannot close existing memberships.

India retains full-NSE quote cadence and nearest unexpired stock futures.
Named universes retain inclusive historical recovery. Full-NSE mode still skips
automatic historical recovery, matching the incumbent. Missing quote instruments
and invalid Schwab candles retain usable rows but remain visible as failed units.

Raw replay reads binary ClickHouse strings before gzip decompression. MIC lookup
preserves existing venue codes and identities. Long market history inserts are
bounded by country, resolution and five-year windows (at most 60 monthly partitions).

## Replay evidence

Read-only replay compared the maintained normalizers with the normalizers actually
installed in production, without replacing collectors or modifying source files.
Only aggregate results were returned; raw payloads remained on the server.

| Replay | Captures | Incumbent rows | Candidate rows | Missing / extra / value differences |
|---|---:|---:|---:|---|
| India, complete September 30 capture set | 11,250 | 1,953,864 | 1,953,864 | 0 / 0 / 0 |
| US, 16 daily and 16 minute responses across symbols | 32 | 14,169 | 14,169 | 0 / 0 / 0 |

India includes 4,860 quote and 6,390 intraday responses. Regular-session OHLC and
volume were compared with tolerance 0.000001. Open interest was excluded: missing
OI remains unknown in canonical records rather than being invented as zero.
US compares daily session dates and minute timestamps with the same value tolerance.
The US result is a bounded sample, not a complete-session replay.

India observed 2,880 listing/contract series and 523,250 distinct series-minutes;
206 series contained all 375 regular-session minutes. This reflects the incumbent
capture coverage and does not prove every expected series traded every minute.

## Production transition

### US universe acceptance — October 14

Owner-authorized deployment reused the already built `035dd0f` US component
image (`sha256:a8cd8f5d052a66087e6530d2cf37402ef1900adf1f1d05e5c762db128559108c`).
The scoped branch runner replaced only `ingest-us` and `universe-us`, retained
the operator environment and UID, and protected the other ingestion daemons
alongside the API, web, monitor, database and secrets agent. Fresh private
backup and plan: `/opt/factorlab/deployment-tests/20261014-035dd0f/`.

The regression test fails with the incumbent bare-ticker publication logic and
passes with validated Schwab aliases. Six collector tests and three runner-scope
tests passed locally. Production command imports, non-root engine validation,
startup stability and protected-service checks passed. Universe run
`4501e751-bfbd-4b52-8647-bd76e76bf5f3` finished successfully on
2026-10-14 at 18:15:38 UTC, with zero failed series and 503 configured stocks.
`meta.source_status` then reported `universe=ready`. This acceptance covers
universe publication; it does not establish complete US historical coverage or
acceptance of the other collectors. No normal component release was cut.

The source migration is complete locally. Production deployment and operational
acceptance are separate gates; this document must record the actual candidate
commit, image IDs, terminal ingestion runs and protected-service checks after cutover.

The branch-test runner preserves current UID and operator environment overrides,
including the hourly IBKR schedule, and protects API, web, monitor, ClickHouse and
secret-agent identities. It stops incumbent daemons before replacing writers.
No schema migration or data deletion belongs to this cutover.

The October 1 inspection found the IBKR container healthy. It also confirmed that
the connected tunnel exposes only a 503 fallback while Caddy listens publicly.
Cloudflare Tunnel/DNS edit access is required to establish the protected route
before closing that origin bypass. A connect token cannot perform that edit.
