# Codebase and production audit — 2026-10-01

## Scope and evidence

Audit of the active `v2-release` workspace, including libraries, providers,
components, CLI entry points, deployment tooling, Workers, configuration and
cross-cutting tests. The sibling `FactorLab` checkout is an earlier monolith
checkout and was preserved. Existing worktree changes were preserved; files
were also being edited concurrently during this audit.

Production was inspected through strict-host-key SSH, Docker's service state
and allowlisted build labels, the redacting host log reader, systemd, listening
ports, the political cron entry, and unauthenticated HTTP probes. No runtime
credentials, full container environments, or account data are recorded here.
Read-only checks occurred around September 30, 20:40–21:00 UTC (October 1 IST).
No production services, routing, data or release tags were changed.

## Verified production inventory

| Service | Image ID prefix | Build commit | Observed state |
|---|---|---|---|
| web | `79aeb39f5b4a` | `379c377` | healthy, loopback 8080 |
| api-candidate | `e9f0dfa1f132` | `60afea8` | healthy, loopback 18000; Caddy API upstream |
| ingest-india | `d2b611f826ab` | `60afea8` | running, no Docker health probe |
| ingest-us | `2cc390ad9882` | `60afea8` | running, no Docker health probe |
| universe-us | `2cc390ad9882` | `60afea8` | running, no Docker health probe |
| ibkr-snapshot | `514cf6ef7646` | `60afea8` | running, unhealthy |
| monitor | `664ea7a8121e` | `60afea8` | healthy |
| cloudflare-secrets-agent | `02ecaf5d6a33` | `60afea8` | healthy |
| bootstrap | `c66159bc0c8c` | `d599402` | exited zero |
| old api | `4a5b239e5a96` | `8f72e1a817df` | running, loopback 8000; retained rollback path |
| ClickHouse 25.3 | `b627d7a9bc0e` | upstream image | healthy, loopback 8123/9000 |

Every listed container had zero recorded restarts at inspection. Component
images are trial builds (`0.0.0-test.monitoring`; web
`0.0.0-test.monitoring-nav`), rather than normal immutable component releases.
The host deployer exists, but `/opt/factorlab/state/images.env` does not.

Political ingestion is a scheduled job, not an idle permanent container:
root cron runs `run-political-ingest.sh` at 02:15 UTC under `flock`.
IB Gateways, Portainer and uptime-kuma were absent from the container inventory.
This is a point-in-time inventory; a running container is not proof of fresh,
complete ingestion or successful future scheduled jobs.

## Production findings requiring remediation

1. **High: origin bypass of Cloudflare Access.** The normal hostname's overview
   endpoint returns a 302 Access sign-in redirect. The same unauthenticated GET
   using `curl --resolve arjundoshi221.com:443:145.239.75.163` returns 200 directly
   from the VPS. Caddy is active, listens publicly on 80/443, and forwards API
   calls to 18000 and UI calls to 8080. `factorlab-cloudflared` is also active.
   Verify the tunnel routes point to the current API/web ports, then disable
   Caddy and close public origin web listeners using the protected-edge runbook.
   Recheck authenticated access, unauthenticated redirects and direct-origin
   denial. The existing [installer](../../deploy/scripts/install-edge-tunnel.sh)
   already disables Caddy, but production must be reconciled with the trial
   port mapping before running it. A separate authorized production change is
   required; this audit did not alter routing.
2. **High: IBKR snapshot unhealthy.** Redacted logs show Gateway connectivity
   loss (1100) and position, account update and execution timeouts. Do not infer
   a complete snapshot from partial writes or container liveness. Local code
   now bounds post-connection blocking requests; it has not been deployed.
   Restore/verify Gateway connectivity and validate a complete broker run after
   deploying the fix. Existing open-order capture changes remain in the worktree.
3. **Release cutover incomplete.** Trial component images coexist with the old
   monolith API and missing component pins. Keep deployment compatibility,
   image fallbacks, script shims and rollback artifacts until the normal
   platform cutover and rollback acceptance gates are completed.

## Changes made locally

- Replaced the shared lock's check-then-write race and age-based live-owner
  stealing with OS advisory ownership on a persistent `.guard` file. A killed
  process releases ownership automatically; a stale version-two JSON record
  can be reclaimed immediately. Old JSON-only records keep the previous age
  guard for migration. Stop old collectors before upgrading; never delete
  guard files while collectors can use them. Tests exercise contention,
  an aged live record, a separate process and forced process death.
- Bounded IBKR blocking requests after connection using `RequestTimeout`,
  default 15 seconds. The existing `connect(timeout=...)` bounded startup only;
  execution requests otherwise inherited ib_async's unlimited default.
- Removed the version-one EODHD universe publication branch. The CLI's
  `UniverseConfig` accepts `Literal[2]`, and version two remains unchanged.
  Removed `legacy/eodhd/configured_universe.py`, its exclusive normalization
  helpers, and `V2USStorage.active_reference_count` / `sync_reference_master`.
  Kept the canonical symbol helpers used by the active Schwab collector.
- Removed obsolete version-one tests while retaining expected-series storage
  coverage and active symbol mapping/recovery tests. Lowered both architecture
  ratchets to reflect the real reduction.
- Removed uncalled, unexported `company_rss_url` and unused testkit
  `dump_capture`. Supported provider APIs, registration and fixture loading
  remain available.
- Fixed new-test type errors and a recovery test's unconfigured writer mock;
  regenerated `.env.example` and corrected import/format drift.
- Fixed asynchronous OAuth callback failures escaping the route's `try/catch`;
  regression tests for both brokers now run in CI and Worker release validation.
- Updated both Workers' Wrangler/Miniflare lockfiles to patched dependencies
  and upgraded the web test runner to Vitest 4.1.11 or later. The Vitest
  maintainer's [advisory](https://github.com/vitest-dev/vitest/security/advisories/GHSA-82fw-gwwq-j7x9)
  identifies 4.1.11 as the first patched 4.x release. These are development
  dependencies, not modules in the deployed static web bundle.

## Retention decisions

The India, US and political component CLIs still execute code under `legacy/`;
that code is actively used, and deleting it would break production collection.
The provider engine bindings are still marked shadow. Engine/provider APIs,
one-shot jobs, administrative tools, migrations, package exports and fixtures
must not be classified as dead merely because no permanent container calls
them. Applied schema migrations remain unchanged and checksummed.

## Validation and limits

The full Python suite passed: **807 passed, 4 skipped**. Checks cover Python
behavior, package boundaries, type checking, formatting, component manifests,
generated Compose, documentation, environment examples, schema checksums,
Worker TypeScript checks and dry-run bundles, two callback regression tests,
web tests and the web production build. Basedpyright reports no new errors;
920 pre-existing diagnostics remain baselined (seven fewer after cleanup).
The web suite passes **56 tests** on Vitest 4.1.11. `npm audit` reports zero
vulnerabilities for all three npm projects after the tooling updates. OSV's
batch API returned no advisories for the 69 installed third-party Python
distributions; this does not cover uninstalled optional/research dependencies
or prove that the deployed images have the same dependency versions.

Windows verification uses Git Bash and a task-specific pytest temp directory;
the workstation's default pytest temp directory is inaccessible. Linux image
builds, isolated installs and live ingestion continuity were not exercised by
this audit. Web tooling reports a large schema-explorer bundle and existing
React test `act` warnings. No claim of zero defects or completed production
cutover is supported by these checks.

## Follow-up collector migration

The source cleanup and replay evidence are recorded in
[engine-cutover-2026-10-01.md](engine-cutover-2026-10-01.md).
Component collector legacy modules and their exclusive storage writers are removed.
The later October 1 inspection found the IBKR container healthy; the routing
incident remains pending Cloudflare Tunnel/DNS edit access.
