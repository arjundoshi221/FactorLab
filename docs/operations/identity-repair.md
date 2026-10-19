# Identity repair: duplicate listings and political trade re-resolution

Every dataset should link to one FactorLab listing per ticker. Cutover-era code minted a
second `ref.listings` identity for 150 US symbols (production read, 2026-10-19): same
exchange and symbol, a different security, no `schwab_symbol` alias. Market data collects
on the listing that carries `schwab_symbol`; 340 political trades pointed at the orphan,
and trades written after the second identity appeared were left unresolved as ambiguous.

The political writer also rejected trades dated before `first_traded`, which records when
FactorLab first saw a listing, not when it listed, and could not match filing tickers such
as `BRK-B` to the listing `BRK.B`. Both are fixed in `sinks/political.py`.

## Commands

Before the platform rollout, the political trial applies this end to end:
`./deploy/release-ui-trial.ps1 -Trial political` from the CI-green tip of
`restructure/deploy-market-data-ui` ([deploy/testing/README.md](../../deploy/testing/README.md#political-trial)).
The steps it runs are below.

Run inside the `ingest-political` image, which has the writer credentials. Each command is
a dry run unless `--apply` is given and prints a JSON report.

```bash
sudo docker compose --env-file production.env -f compose.production.yml \
  --profile jobs run --rm ingest-political retire-duplicate-listings
sudo docker compose --env-file production.env -f compose.production.yml \
  --profile jobs run --rm ingest-political reresolve-political-trades
```

Order: `dedupe-political-trades`, then `retire-duplicate-listings`, then
`reresolve-political-trades`, each as a dry run, reviewed, then with `--apply`. All three
are idempotent.

- **Dedupe** deletes, per `political_trade_id`, every row older than its newest version
  (a lightweight `DELETE`). The table's sort key includes `entity_id`, so a version that
  resolves to another identity is a second row, not a replacement. Trades with two rows
  at their newest version are reported as ambiguous and left alone.

- **Retire** only provable duplicates: exactly one listing of the symbol carries the
  canonical alias, all are on one exchange and no two carry different ISINs; others are
  listed under `skipped`. It writes `ref.listing_migrations` (`duplicate_identity`), a new
  `ref.listings` version of each orphan with `active = false`, and moves the orphan's open
  aliases to the canonical listing (closing the old ones). Rows stored under an orphan,
  such as legacy bars on three listings, stay where they are.
- **Re-resolve** rewrites the newest version of unresolved trades and trades on retired
  listings under the same `political_trade_id` with a new version, using the writer's
  lookup, then deletes the superseded rows. The writer does the same after each batch.

The first political trial (2026-10-19, `political-trial/342ab392b39c`) retired the 150
listings and relinked trades but did not yet delete superseded rows: 431 trades kept a
stale second row (433 rows), and its verification failed. The second trial removes them.

## Checks

Expected from the 2026-10-19 dry-run preview: 150 retirements, 3 aliases moved, nothing
skipped. Result after the second trial (2026-10-20): 641 rows for 641 trades, 563 linked,
0 on retired listings; the 63 unresolved stock trades name tickers with no FactorLab US
listing (for example VIK, TSM, NVO) and link once reference data adds them. Afterwards, no US symbol has two active listings that are not retired, and
`alt.political_trades` has no `listing_id` among retired listings.

## Undo

Write a new `ref.listings` version with `active = true` for the orphan, a superseding
`ref.listing_migrations` row with another `migration_type`, and reopen the closed alias.
Re-resolved trades keep their history in older versions until merges collapse them.
