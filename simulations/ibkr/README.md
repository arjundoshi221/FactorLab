# IBKR trade-stream simulator

Run a dedicated synthetic account through FactorLab's regular broker capture,
archive, normalization, storage, and portfolio views. This folder contains the
launcher, account registration, and an editable timed trade scenario. No IBKR
login, Gateway, or actual brokerage account is required.

From the `v2-release` repository root, with the workspace dependencies installed:

```powershell
# Preview all events immediately; no database connection or state files.
python simulations/ibkr/run.py --dry-run --interval 0

# Write an event every 30 seconds so the portfolio can be observed as it changes.
python simulations/ibkr/run.py --write --interval 30

# Advance only one event, then pause. Repeat to advance/resume the same session.
python simulations/ibkr/run.py --write --max-events 1
```

Writing uses the configured ClickHouse target and requires the profile migration
from [shared test users](../../docs/operations/shared-test-users.md) to be applied.
The script registers the **IBKR stream tester** profile and **SIM_IBKR_STREAM**
paper account. Open `/portfolio`, select that profile/account, and refresh the
account to see intermediate states; automatic page polling is every 60 seconds.

The supplied scenario starts with USD 100,000, buys AAPL in two partial fills,
buys MSFT, sells part of AAPL, changes market prices, and cancels an unfilled bid.
It ends with **7 AAPL, 5 MSFT, USD 96,512.60 cash, and no open orders**. Its prices
are fictional fixtures. Buy commissions enter weighted average cost; sale
commissions reduce realized P&L. Net liquidation is cash plus marked holdings.

## Editing scenarios

`account.yaml` must contain exactly one active test profile and one active,
simulated US/USD IBKR paper account with a `SIM_` ID. Use a different account ID
and user UUID if you want an additional independent test account.

`trades.yaml` defines starting cash, instruments (`con_id`, `initial_price`), and
ordered events. Supported kinds are:

| Kind | Fields | Effect |
|---|---|---|
| `quote` | `symbol`, `price` | Update the mark and unrealized P&L |
| `order` | `order_id`, `symbol`, `side`, `quantity`, `limit_price` | Add a working BUY/SELL limit order |
| `fill` | `order_id`, `quantity`, `price`, optional `commission` | Partially or fully fill an order; update cash/holdings |
| `cancel` | `order_id` | Remove the unfilled remainder of an order |

Order IDs must be unique in a scenario. Fills cannot exceed remaining quantity,
violate the limit price, overspend cash, or sell more shares than held. The entire
scenario is validated before ingestion. Each event emits a fresh account snapshot,
including empty positions/orders when appropriate; fills retain stable IDs/times.
This model supports long-only USD stocks, without leverage or short selling.

## Sessions and resume

Write runs checkpoint under `data/_state/ibkr-simulation/<account-hash>/demo.json`
by default (the runtime state-root setting can relocate it). `--state-dir <path>`
overrides the simulator state directory. The session defaults to `demo`;
`--session <name>` selects another. Keep this state directory between runs.

Ctrl+C stops the script; running the same command resumes after the last successful
snapshot. A partially written event retries with the same execution IDs/times,
allowing the existing replacing tables to deduplicate fills. A completed session
does not replay automatically. Changing an existing session's account/scenario is
rejected. Only one local write runner may operate on the account at a time.

A new session starts a new synthetic cash ledger. For a completely clean portfolio
and execution history, use a fresh synthetic account ID as well. Checkpoints are
local; do not run the same account from multiple machines or delete checkpoint
files if you intend to resume it. Use a separate state directory for each database
target, and do not run the same account concurrently with different state directories.
Dry runs neither resume nor alter checkpoints.

This is an IBKR-shaped data replay, not a TWS socket-server emulator or a broker
execution simulator. It does not place real orders or consume current market
prices. After Waves 12/13 and the portfolio release, orders can be tagged on `/portfolio`;
all partial fills inherit their durable order assignment. Use the same three tags
on closing orders to match the opening lots in the FIFO reports. For installed component usage, the same
runner is available as `factorlab-ingest-broker simulate-stream --config <yaml>
--scenario <yaml>` with the same options.
