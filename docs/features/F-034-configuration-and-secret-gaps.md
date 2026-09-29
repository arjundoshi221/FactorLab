---
id: F-034
title: Close configuration and secret inventory gaps found while documenting members
status: backlog
priority: P2
components: [ingest-broker, ingest-us, ingest-india, ingest-political, secrets-agent, ingest]
owner: unassigned
sprint: null
decisions: [ADR-0011]
links: [F-007]
created: 2026-09-30
updated: 2026-09-30
---

# F-034 Close configuration and secret inventory gaps found while documenting members

## Problem

Writing each member's CONTEXT.md from the code (P8 of the restructure) surfaced places where
the manifests, settings files and docs promise something the code does not do. None breaks
production today, but each will mislead the next change or the engine cutovers.

## Scope

- `ingest-broker` declares the secret `IBKR_CLIENT_ID`, but the secrets agent never writes it;
  the snapshot uses `--client-id` (default 2). Deliver it or drop it from the manifest.
- The EDGAR provider reads `EDGAR_USER_AGENT` via `get_secret`, but `ingest-us` does not list
  it in `secrets:`. Decide whether it is a secret (it is a contact header) or a setting.
- docs/architecture/07 §6.3 says the engine sets `reauth_required` in `meta.source_status` on
  `AuthRequired`; no engine code writes `meta.source_status` (only legacy code does).
- Settings in `configs/sources/*.yaml` that no code reads: EDGAR's nested `api:` and
  `rate_limits:`, Upstox `retries` (the engine uses `DEFAULT_RETRIES`), `ibkr.yaml` (the
  Gateway host/port come from `IBKR_*` secrets); `house_clerk.yaml` and
  `congress_legislators.yaml` do not exist, and `political.yaml` is read by neither.
- Stale references: house-clerk docstrings name `sources.political.house_clerk`; the schema SQL
  README names the old codegen script; the `secrets.py` docstring mentions Vault Agent.

## Non-goals

- Changing provider behaviour beyond what each fix needs; cutovers are F-008–F-012.

## Acceptance criteria

- [ ] Every declared secret is delivered by the secrets agent and read by the code, and every
      secret the code reads is declared (a check in `tools/components.py` or a test).
- [ ] 07 §6.3 matches the engine (either the engine writes `meta.source_status` or the doc says it doesn't).
- [ ] No unread keys in `configs/sources/*.yaml` (a settings-model `extra="forbid"` or a test).
- [ ] The stale docstrings and READMEs are corrected.

## Rollout

Through the affected components' releases; secret changes need the owner to update the
Cloudflare secret store and the secrets agent first.

## Log

- 2026-09-30: created from the P8 context-writing review.
