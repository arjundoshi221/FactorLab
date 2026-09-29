# 011 — Strict provider abstraction for ingestion

> Status: `[in-progress]` — drafted 2026-09-24; all phases P1–P8 built and tested the same day, every provider bound as `shadow`. Spec: [architecture/07](../architecture/07-ingestion-provider-abstraction.md). Production cutover per provider follows 07 §15.2 after the ClickHouse v2 table swap; legacy deletion awaits the owner's confirmation (07 §15.3).

## Decision

Every data ingestion path into ClickHouse v2 is split into three layers with enforced boundaries:

1. **DB service** (`storage/sinks/`). There is one typed sink per canonical dataset (`market.bars`, `ref.listings`, `alt.political_trades`, `broker.snapshot`, …). It accepts provider-neutral records. It alone resolves identity, mints canonical UUIDs and writes provenance.
2. **Engine** (`shared/ingest/engine.py`). It is provider-agnostic and drives any source into any sink inside the existing `ingestion_run` / `RunContext` contract (`shared/ingest/provider.py`).
3. **Provider adapters** (`sources/<provider>/`). Each implements `plan → fetch → normalize` for the datasets it supports. All vendor knowledge lives here and nowhere else: auth, rate limits, endpoints, formats, segment codes.

Which provider feeds which dataset is decided by `configs/ingestion/bindings.yaml`, not by code. Several providers can feed the same dataset at the same time. Each writes its own rows, distinguished by `source` and `source_channel`, and reads pick the best row by the priority in `ref.source_priorities`, with `market.bars_best` as the view.

Switching a provider is a config change. Adding one is a new `sources/<p>/` package plus a binding. Duplicating one is a new instance block plus a binding.

## Why

- **Storage knows about providers today.** Evidence:
  - `storage/v2_india.py` defaults `source="upstox"` and filters Upstox segment codes.
  - `storage/v2_us.py` defaults `source="schwab"`, and `reference()` mints a Schwab-specific listing key (`schwab:USA:{symbol}`) that gives the same symbol two listing IDs.

  Replacing or adding a vendor therefore means editing the DB layer.
- **Orchestration knows about providers.** Each daemon (`factlab_india_clickhouse_5min.py`, `factlab_us_clickhouse.py`, `factlab_political_bootstrap.py`) opens and closes runs by hand and passes vendor-shaped DataFrames and dicts into storage.
- **The contract exists but is not used.** IBKR (`sources/ibkr/provider.py`) already follows capture → archive → pure normalize → typed write, and it works well. But its storage Protocol is IBKR-owned, and nothing else uses the contract.
- **Multi-vendor data was designed for, but is unreachable.** `source` is in the `market.bars` sort key and 06 §13.1 designs `bars_best`, yet no code path lets two vendors feed one dataset under config control.

## Tradeoffs considered

| Option | Why not |
|---|---|
| **Per-provider storage classes** (status quo) | Every new vendor adds storage code; switching vendors means changing DB writers and daemons. |
| **One active provider per dataset** | Simpler, but gives up shadow trials, failover and vendor reconciliation, all of which the schema already supports. Rejected in favour of store-all + priority. |
| **Generic dict/DataFrame records** | Less boilerplate, but vendor quirks leak through untyped fields. It repeats today's problem. |
| **Plugin auto-discovery via entry points** | Convenient, but a mis-installed package could start writing production data. An explicit provider list is auditable. |
| **Re-keying existing listing IDs to natural keys** | Cleaner IDs, but it rewrites history across `market.bars` and every fact table. Instead, existing IDs are frozen, and natural keys are used only for resolution and new mints. |

Costs we accept:

- A records layer and a sink layer to maintain.
- A one-time migration of Upstox, Schwab, EODHD, political and IBKR.
- A shadow-parity step before each cutover.

## Explicitly not doing

- Porting the legacy-only Postgres political sources (FEC, LDA, Senate eFD, Senate Stock Watcher, Congress.gov, USAspending, Finnhub contracts). They are deprecated as they stand, and each gets ported only when research needs it.
- A streaming/websocket contract. Schwab streaming ([002](002-live-us-market-data.md)) gets its own contract later.
- Quality-aware vendor selection. Priority is static config (06 §17, F2).
- Changing any existing canonical ID.

## Rollout

The phases, P0 (spec) through P8 (EDGAR proof + legacy retirement), are listed with exit criteria in [07 §15](../architecture/07-ingestion-provider-abstraction.md#15-migration-phases). Each phase ships independently. Provider cutovers go through `role: shadow` parity before promotion.

`tests/architecture/test_boundaries.py` has a shrink-only allowlist that tracks the remaining leaks. The work is done when that allowlist is empty.

## Open questions

These are tracked in [07 §16](../architecture/07-ingestion-provider-abstraction.md#16-not-doing-and-open-questions):

- Should `bars_best` be a materialised view, or resolved at read time?
- How should new natural-key listing IDs be derived, and what is the symbol-rename policy?
- Is `market.quotes` in v1?
- Should the universe resolver be a dataset source or stay separate?
- Should `meta.source_status` get an instance column?
