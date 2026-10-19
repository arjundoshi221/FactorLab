---
id: F-037
title: Canonical instrument data explorer
status: review
priority: P1
components: [api, web]
owner: unassigned
sprint: null
decisions: []
links: [F-036]
created: 2026-10-16
updated: 2026-10-16
---

# F-037 Canonical instrument data explorer

## Problem

Configured collection universes omit inactive listings, unconfigured instruments and
many non-market datasets. Operators need to inspect what is actually stored for a
canonical instrument without confusing collection intent, pipeline health or issuer
context with direct instrument data.

## Scope

`/market-data` defaults to **All tickers**. **Collection coverage** retains the existing
US/India daily and historical screens and unchanged `/universe` API contracts.
The inventory includes all `ref.listings FINAL` and `ref.contracts FINAL` entries,
using listing IDs and contract IDs. Symbols are presented with exchanges and contract
expiry, strike and right. Search accepts symbols, company names, UUIDs and provider
aliases, including historical aliases. Country, exchange, instrument type, active
status, collection membership, pagination and selection are URL-backed and work with
refresh and browser back. Pages default to 50 instruments.

Private endpoints under `/hub/api/v1/market-data`:

- `GET /instruments`: filtered inventory, default limit 50, maximum 200.
- `GET /instruments/{kind}/{id}`: identity records, aliases with validity dates,
  related listings/contracts and dataset descriptors. Related/alias lists are capped
  at 200 with explicit warnings; use the corresponding datasets for additional rows.
- `GET /instruments/{kind}/{id}/datasets/{dataset}/coverage`: lifetime instrument-scoped
  counts, earliest/latest event times, latest ingestion and checked timestamp.
- `GET /instruments/{kind}/{id}/datasets/{dataset}/rows`: bounded previews filtered by
  provider and date, default latest 30 days, 50 rows, maximum 200 and offset 5,000.

Kinds are `listing` and `contract`; IDs are UUIDs. Pydantic models in `instruments.py`
and TypeScript models in `instrumentTypes.ts` define the responses. Inventory failures
return safe catalog errors; individual dataset failures remain independent panel states.

### Dataset registry and relationships

`instrument_datasets.json` is an explicit, reviewed registry for every current schema
table and view. It freezes trusted relationship predicates, timestamp, dimensions,
preview columns, storage engine and sort key. Its completeness test compares names and
columns against the schema package, requiring new datasets to be classified explicitly.
Runtime table and column names are never supplied as SQL by the client.

Datasets are grouped into Market, Alternative & political, Fundamentals, Broker &
portfolio, Derived, and Reference & operations. Each is queryable, related context,
unsupported, or an explicitly labeled investigation dataset. Catalog descriptions,
writer metadata, preview restrictions, hidden columns, raw metadata allowlists,
sanitization and query limits are reused. Views link to their catalog and underlying
storage rather than receiving a separate physical row count.

- Market bars retain source/resolution/contract dimensions. Daily collection and
  historical recovery can write the same physical table. `market.bars_best` is a
  virtual selection: lowest numeric priority among active primary/secondary sources,
  per field; shadow and disabled sources are excluded.
- Political trades use the most specific canonical identity, with disjoint listing,
  security and issuer groups. Filings join matching trades on country, chamber and
  filing ID with the same precedence. Social posts use resolved listing/entity arrays.
- Fundamentals are explicitly issuer-level; for contracts they are underlying-issuer
  context. Reference identity and corporate actions retain their actual linkage level.
- Broker records preserve account, broker, paper/live and source channel. Preview
  provenance includes recorded ownership/simulation flags; missing flags mean unknown.
  Order assignments follow `(broker_code, account_id, perm_id)` links. Legacy execution
  attribution is queried only for exec IDs that resolve to one broker/account and have
  no conflicting instrument rows. Tags follow supported order assignment links.
  Account balances and forward-test account totals are related context, never instrument
  totals. Trade legs have no contract link and are not inherited by a selected contract.
- Factor/adjustment/continuous-future computation IDs link to computations and lineage.
  Aliases, corporate actions, adjustment factors, membership and migrations are explicit
  datasets. Contract records never inherit siblings' records.
- Unresolved candidates require country/exchange plus an alias validity interval covering
  the observed unresolved interval. They are labeled investigation candidates and excluded
  from confirmed coverage. Unscoped symbols are not guessed into an instrument.

### Availability and provenance

States distinguish stored data, no matching records, missing tables, unavailable queries,
and unsupported linkage. Counts are never taken from table-wide storage metadata.
Coverage is lifetime and only requested for the selected instrument. FINAL deduplicates
replacing/collapsing storage. Aggregate result and read limit overflow throws, so partial
statistics cannot be represented as complete coverage.

Identity loads first; coverage loads progressively with two browser workers. A shared
three-slot server semaphore covers every explorer database query, including metadata and
provenance. Queries are read-only, bounded by catalog execution/read/memory/result limits.
Preview windows obey catalog maximum widths (31 days for market/raw, 366 otherwise).
Client cancellation and response guards prevent old ticker results from replacing a new
selection. Individual panels report failures without hiding the identity or other panels.

Expected series, session coverage and recovery state are separate reference/operations
datasets. Listing-only evidence is not assigned to contracts. Recorded ingestion runs
use touched-listing IDs; per-record run IDs provide exact provenance where present.
Pipeline links represent general operational context, not instrument coverage.
Previews resolve recorded raw IDs, run IDs and computation IDs from returned records;
missing IDs and expired/absent evidence are reported. Raw response bodies, headers and
other non-allowlisted metadata never appear. Credentials and URL query strings are scrubbed.

## Non-goals

No new identity scheme, migration, ingestion repair, ingestion job, APK build or deployment.
No production data or logs are queried during implementation. Runtime observations alone
establish availability. Account-wide totals are never allocated to instruments.

## Acceptance criteria

- [x] Default inventory includes inactive/unconfigured/empty instruments and contracts.
- [x] URL-backed filtering, paging and selection preserve refresh/back behavior.
- [x] Explicit registry covers every schema table/view and guards contract isolation.
- [x] Dataset coverage is bounded, instrument-scoped and distinct from collection intent.
- [x] Record previews enforce catalog policy and preserve missing provenance evidence.
- [x] Existing collection API and UI tests remain compatible.
- [x] Focused API/catalog/frontend tests, frontend build, Python and documentation checks.

## Rollout

The authorized API/web branch trial uses `deploy/release-ui-trial.ps1` after CI passes;
`branch-ui-deploy.yml` publishes verified immutable images and runs the scoped UI verifier.
Normal component releases remain available after the component foundation reaches main. No schema or collector change is needed. Production
availability is intentionally evaluated at request time, not assumed from development.

## Log

- 2026-10-16: Implemented registry, private endpoints, instrument explorer, focused tests
  and documentation.

## Deployment record

- 2026-10-16: Deployed API candidate and web through the authorized API/web branch trial,
  version `0.0.0-trial.70f911b3cd3d`, source commit
  `70f911b3cd3dba79747a9766211c2b558586d3de`.
  [Deployment workflow](https://github.com/arjundoshi221/FactorLab/actions/runs/38918441456)
  completed successfully; verification passed and recovery was not needed.
- API digest: `sha256:9565ce254cf0a60dfbe4d428376f9e3ed890f39550a2f2f625f7a2817a595d92`.
- Web digest: `sha256:f33b1b2e1e36301d70a6e8352fd1fb980876337a8764755f9b8d01e9a0f41f4d`.
- Both containers were healthy with zero restarts; served web version matched the source.
  Inventory/identity, ten representative coverage datasets, record previews, existing
  collection views and monitoring checks passed. Separate read-only preflight checks also
  validated assignment/computation/candidate joins and contract-isolated queries.
- Protected production container identities stayed unchanged. No main merge, schema
  migration, ingestion deployment, component-pin bootstrap or routing change occurred.
