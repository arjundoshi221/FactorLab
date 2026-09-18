# Roadmap

> Status: `[design]`

Roadmap is capability-ordered, not date-ordered. Each phase should harden the previous one before the next layer is treated as real.

---

## Phase 1 - Architecture reset `[in progress]`

**Goal:** Replace the Postgres/Timescale plan with a single-store ClickHouse target.

- [ ] ClickHouse table inventory defined by domain
- [ ] naming convention decided for databases vs table prefixes
- [ ] denormalized serving strategy documented for `alt_political`
- [ ] legacy Postgres assumptions marked as deprecated in docs and code
- [ ] migration sequencing agreed before new storage code lands

**Exit criterion:** One coherent target architecture exists and no core doc still describes Postgres as the destination state.

---

## Phase 2 - Local ClickHouse foundation `[design]`

**Goal:** Stand up ClickHouse locally and make it the new storage baseline.

- [ ] Docker Compose ClickHouse service
- [ ] app config for ClickHouse connectivity
- [ ] bootstrap script for database, users, and tables
- [ ] minimal DDL creation path checked into repo
- [ ] local smoke query from Python client

**Exit criterion:** Clean local startup can create and query the ClickHouse layout end to end.

---

## Phase 3 - Raw archive first `[design]`

**Goal:** Make raw retention the first guaranteed invariant.

- [ ] generic raw archive table pattern finalized
- [ ] each fetcher writes raw payload before parsing
- [ ] replay tooling from raw payload to parser
- [ ] storage sizing estimates for long-term retention
- [ ] integrity checks for duplicate fetches and malformed payloads

**Exit criterion:** At least one source can be re-parsed entirely from retained raw data.

---

## Phase 4 - Market data migration `[design]`

**Goal:** Move canonical market storage to ClickHouse.

- [ ] `ref` instrument model redefined for ClickHouse
- [ ] daily bars ingestion landed
- [ ] minute bars ingestion landed
- [ ] fundamentals and corporate actions model landed
- [ ] reconciliation against sample historical slices
- [ ] basic market REST endpoints working

**Exit criterion:** One market source flows from raw fetch to ClickHouse fact table to API response reproducibly.

---

## Phase 5 - `alt_political` denormalized rebuild `[design]`

**Goal:** Replace the normalized political schema with serving-oriented ClickHouse tables.

- [ ] legislator, committee, and FEC support dims defined
- [ ] denormalized trade table landed
- [ ] denormalized contracts table landed
- [ ] denormalized lobbying table landed
- [ ] denormalized donations table landed
- [ ] denormalized bills and hearings tables landed
- [ ] alias-resolution workflow for ticker mapping landed
- [ ] political timeline endpoints working

**Exit criterion:** Common political queries no longer require rebuilding a normalized graph at read time.

---

## Phase 6 - Universe, derived, and experiments `[design]`

**Goal:** Restore research and factor workflows on top of ClickHouse only.

- [ ] point-in-time universe membership model
- [ ] derived factor tables
- [ ] signal snapshot tables
- [ ] experiment and run metadata tables
- [ ] backtest reads fully from ClickHouse

**Exit criterion:** A full research run can be reproduced without depending on Postgres-era storage.

---

## Phase 7 - API-first product surface `[design]`

**Goal:** Expose the core system through stable REST endpoints.

- [ ] endpoint contracts versioned
- [ ] pagination and filter strategy standardized
- [ ] freshness metadata included in responses
- [ ] auth model chosen for private deployment
- [ ] benchmark and cache the slowest endpoints

**Exit criterion:** Research clients and local tools can consume the system through API endpoints without direct storage knowledge.

---

## Phase 8 - Deployment and operations `[design]`

**Goal:** Run the single-store platform reliably on one VPS.

- [ ] Dockerized ClickHouse plus app containers
- [ ] daily backups to off-box storage
- [ ] disk growth monitoring
- [ ] ingestion health checks
- [ ] restore drill from backup
- [ ] secrets rotation path documented

**Exit criterion:** Two weeks of unattended green ingestion and successful restore verification.

---

## Phase 9 - Broker integration and live pipeline `[design]`

**Goal:** Reconnect signals to paper execution once storage is stable.

- [ ] IBKR paper integration
- [ ] positions and fills persisted into ClickHouse
- [ ] proposed-vs-filled reconciliation
- [ ] daily signal-to-order pipeline
- [ ] alerting and kill switch

**Exit criterion:** Signals can flow from ClickHouse-derived research outputs into paper execution with daily reconciliation.

---

## What is explicitly out of scope for now

- Public web app
- Multi-tenant authentication
- Multi-store hybrid architecture
- Dual-write long-term compatibility layer for Postgres
- Generic ORM-first storage abstraction
