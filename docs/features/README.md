# Features

One file per feature: `F-NNN-<slug>.md`, from [_template.md](_template.md). Numbers are
never reused. A feature moves through `idea → backlog → planned → in-progress → review →
shipped` (or `dropped`). Plan it into a sprint by setting `sprint:`, record the
decisions it relies on in `decisions:`, and add a line to its `## Log` whenever its
status changes. Features with a `roadmap:` block are the cards on the hub's `/roadmap`
page (`components/web/src/roadmap.generated.json`).

After editing features, regenerate this index, the sprint lists and the roadmap with
`uv run python tools/check_docs.py --write`. CI runs `tools/check_docs.py` and fails
when anything here is stale or invalid. The `feature` skill
(`.claude/skills/feature`) walks through writing one.

<!-- BEGIN GENERATED: features index -->
| ID | Title | Status | Priority | Sprint | Components |
|---|---|---|---|---|---|
| [F-001](F-001-data-health.md) | Data health | in-progress | P1 | — | api, web |
| [F-006](F-006-restore-edge-access-invariants.md) | Restore the Cloudflare Access invariants on the hub hostname | planned | P0 | 2026-S20 | platform, api |
| [F-007](F-007-per-component-rollout.md) | Roll out per-component releases (R0–R5) | planned | P0 | 2026-S20 | platform, api, web, secrets-agent, schema-migrator, ingest-india, ingest-us, ingest-political, ingest-broker |
| [F-002](F-002-data-explorer.md) | Data explorer | planned | P2 | — | api, web |
| [F-008](F-008-cutover-ingest-india.md) | Cut ingest-india over to the ingestion engine | backlog | P1 | — | ingest-india |
| [F-009](F-009-cutover-ingest-us-bars.md) | Cut US daily and 1-minute bars over to the ingestion engine | backlog | P1 | — | ingest-us |
| [F-010](F-010-cutover-universe-us.md) | Cut the US universe job over to the ingestion engine | backlog | P1 | — | ingest-us |
| [F-011](F-011-cutover-ingest-political.md) | Cut political ingestion over to the ingestion engine | backlog | P1 | — | ingest-political |
| [F-012](F-012-cutover-ingest-broker.md) | Cut the IBKR snapshot over to the ingestion engine | backlog | P1 | — | ingest-broker |
| [F-013](F-013-apply-wave-10-source-priorities.md) | Apply wave 10 (source priorities) in production | backlog | P1 | — | schema-migrator |
| [F-025](F-025-api-verifies-access-jwt.md) | API verifies the Cloudflare Access JWT | backlog | P1 | — | api |
| [F-026](F-026-alert-on-errors-and-failed-runs.md) | Alert on ERROR logs and failed runs | backlog | P1 | — | platform, api |
| [F-003](F-003-pipeline-operations.md) | Pipeline operations | backlog | P2 | — | api, web, platform |
| [F-014](F-014-port-senate-efd.md) | Re-port Senate eFD periodic transaction reports as a provider | backlog | P2 | — | ingest-political |
| [F-015](F-015-port-senate-stock-watcher.md) | Re-port Senate Stock Watcher as a provider | backlog | P2 | — | ingest-political |
| [F-016](F-016-port-fec.md) | Re-port FEC contributions as a provider | backlog | P2 | — | ingest-political |
| [F-017](F-017-port-lda.md) | Re-port LDA lobbying disclosures as a provider | backlog | P2 | — | ingest-political |
| [F-018](F-018-port-usaspending.md) | Re-port USASpending contracts as a provider | backlog | P2 | — | ingest-political |
| [F-019](F-019-port-finnhub-contracts.md) | Re-port Finnhub government contracts as a provider | backlog | P2 | — | ingest-political |
| [F-020](F-020-port-congress-gov.md) | Re-port Congress.gov bills and votes as a provider | backlog | P2 | — | ingest-political |
| [F-021](F-021-schwab-streaming.md) | Live US data via Schwab streaming | backlog | P2 | — | ingest-us |
| [F-027](F-027-political-systemd-timer.md) | Run the political job from a systemd timer | backlog | P2 | — | ingest-political, platform |
| [F-028](F-028-dependency-updates.md) | Automated dependency updates (Renovate) | backlog | P2 | — | platform |
| [F-029](F-029-ghcr-retention.md) | GHCR image retention | backlog | P2 | — | platform |
| [F-031](F-031-deploy-windows.md) | Deploy windows in component manifests | backlog | P2 | — | platform |
| [F-004](F-004-live-markets.md) | Live markets | backlog | P3 | — | api, web, ingest-us |
| [F-005](F-005-backtesting.md) | Backtesting | backlog | P3 | — | api, web |
| [F-030](F-030-release-candidates.md) | Release candidate tags (-rc.N) | backlog | P3 | — | platform |
| [F-033](F-033-shrink-type-baseline-raise-coverage.md) | Shrink the type baseline and raise coverage floors | backlog | P3 | — | platform |
| [F-022](F-022-sectoral-political-analytics.md) | Sectoral political analytics | idea | P3 | — | storage, api |
| [F-023](F-023-commodities-squeeze-signals.md) | Commodity squeeze-signal sleeve | idea | P3 | — | ingest-us |
| [F-024](F-024-hyperliquid-after-hours.md) | Hyperliquid tokenized-equity perps as an after-hours signal | idea | P3 | — | ingest-us |
| [F-032](F-032-worker-ci-deploys.md) | Decide on CI deploys for the Cloudflare Workers | idea | P3 | — | upstox-auth-worker, upstox-oauth-callback-worker |
<!-- END GENERATED: features index -->
