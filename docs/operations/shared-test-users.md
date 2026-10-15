# Shared test users and simulated portfolios

FactorLab now models named application profiles and one owner per broker account.
Every tester admitted through Cloudflare Access can view every profile. Market data
and public reference data stay shared. Login identities and permissions can later
map to stable profile IDs. [Portfolio tagging](../features/F-036-portfolio-tagging-pnl.md)
adds independent profile-owned catalogs and durable-order assignments after Wave 13.

## Prerequisite and registration

The forward `wave_12_schema_app_profiles` migration must be applied separately to
the target database, following [schema migration workflow](../../libs/schema/CONTEXT.md). Do not edit
applied waves. Migration and component releases are separate operator operations.
The profile schema does not change broker facts or historical account keys.

Use [the synthetic example](../../configs/testing/broker-profiles.yaml) as a starting
point. Assign explicit UUIDs once; preserve them when renaming users. Real account
configuration should remain outside source control and contain no credentials.

```powershell
uv run factorlab-ingest-broker profiles sync --config configs/testing/broker-profiles.yaml --dry-run
uv run factorlab-ingest-broker profiles sync --config configs/testing/broker-profiles.yaml
```

Syncs must be serialized by the operator. They validate all existing mappings
before writes and can be rerun after an interruption; they are not transactional.
Omitted records are retained. Change `active` to mark a profile/account inactive.
Ownership, simulation status, and account mode cannot be changed by registration.
Unmapped historical accounts are listed under **Unassigned**.

## Simulate two users without an IBKR account or Gateway

For timed trades on a dedicated account, use the separate [IBKR simulation folder](../../simulations/ibkr/README.md). Its runner supports partial fills, price changes, cancellations, consistent cash/holdings, and pause/resume. The fixed four-step example below remains available.

```powershell
# Pure dry run: no ClickHouse connection and no writes.
uv run factorlab-ingest-broker simulate --config configs/testing/broker-profiles.yaml --dry-run

# Register profiles and ingest initial balances, order, fill, and close snapshots.
uv run factorlab-ingest-broker simulate --config configs/testing/broker-profiles.yaml --scenario basic

# Leave open positions visible for inspection.
uv run factorlab-ingest-broker simulate --config configs/testing/broker-profiles.yaml --step filled

# Close the simulated positions; the portfolio should become empty.
uv run factorlab-ingest-broker simulate --config configs/testing/broker-profiles.yaml --step closed
```

Writing commands use the configured ClickHouse target. Run against the intended
test database. The basic scenario accepts only synthetic IBKR, US-booked USD paper
accounts. IDs start with `SIM_`; real accounts are excluded. Both sample users hold
AAPL with account-specific quantities and a USD 100,000 starting balance. Values
are fixtures, not current market prices. Repeating a scenario preserves logical
fill IDs/times while adding a fresh account snapshot. It never connects to a Gateway
or places orders, and it does not overwrite real IBKR source status.

The full scenario ends **closed**. Run `--step order` to inspect an open order or
`--step filled` to inspect positions. Inspect `/portfolio`, choose a profile and
account, and confirm balances, orders, fills, simulation labels, and timestamps.
All profiles shows selectable accounts, not an aggregated multi-user portfolio.

Captures are archived with simulation markers and file transport before normalizing
into the regular broker tables. Ingestion runs use `ibkr_broker_simulation`, and raw
captures/facts use the `ibkr_simulation` source. Shared instrument aliases are resolved
as usual; an unresolved instrument remains visible by its broker symbol/vendor ID.

## Snapshot meaning and future logins

A successful empty positions/orders snapshot clears the view. A failed capture
retains the last successful snapshot. Snapshot manifests live in run metadata;
older data without a manifest falls back to the latest stored snapshot. The current
direct IBKR snapshot collector publishes manifests; engine-source cutover must
carry that completeness contract forward. More than two hours old is labelled stale;
weekends and offline Gateways can therefore legitimately show old data.

Executed trades are imported fills, not application-authored trade theses. The UI
does not roll up different currencies. The portfolio theme/strategy/trade-name screens
show cumulative FIFO PnL for recorded stock/ETF history, retaining explicit missing
basis, price and cost states. Retagging restates reports; order history retains prior
assignments. Other products remain taggable with unavailable PnL. Future login work must map verified identity
to `user_id` and enforce permissions in every private read path, including the data
catalog and exports. A profile selector is not an authentication mechanism.

## Verification

Focused tests cover registration, account separation, raw provenance, empty
snapshots, failed refreshes, and duplicate fills. The opt-in integration test at
`tests/integration/test_shared_profiles.py` requires an **empty disposable** local
ClickHouse 25.3 server at `127.0.0.1:18124` with a `raw_archive` storage policy. It
creates its own tables and refuses an existing schema; it never loads DB credentials
from environment settings. Set `FACTORLAB_PROFILE_INTEGRATION=1` to enable it.
