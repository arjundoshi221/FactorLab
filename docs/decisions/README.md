# Decisions

Architecture decision records (ADRs) for FactorLab: what was decided, why, and what it
costs. They replace `docs/developments/`, whose documents became ADR-0001 to ADR-0011
([MIGRATION.md](MIGRATION.md) maps the old paths). Current-state specifications stay in
`docs/architecture/`, `docs/operations/` and the other folders. An ADR records the
reasoning and links to them.

## Conventions

- **One decision per file.** If a record grows a second decision, split it.
- **Append-only numbering.** Files are `NNNN-slug.md`, and the IDs are `ADR-NNNN`. Take the
  next free number. Numbers are never reused, even for rejected or deleted drafts.
- **The body is a record.** Once a decision is accepted, its body is only corrected, not
  rewritten. A changed decision gets a new ADR, and the old one is marked `superseded`.
- **Frontmatter** (exact keys, in this order):

  | Key | Meaning |
  |---|---|
  | `id` | `ADR-NNNN`, matching the file name |
  | `title` | The decision in one line, without the number |
  | `status` | One of the statuses below |
  | `date` | The day it was drafted or decided (`YYYY-MM-DD`) |
  | `superseded_by` | The replacing ADR. Present only when `status` is `superseded` |
  | `status_note` | One sentence explaining the status as of its last review |
  | `status_confirmed` | `false` until the owner has confirmed the status |

## Statuses

| Status | Meaning |
|---|---|
| `proposed` | Drafted and open for discussion. Not yet decided. |
| `accepted` | Decided. Implementation or rollout is pending or partial, as the `status_note` says. |
| `implemented` | Decided and fully in effect. The record is now historical reference. |
| `superseded` | Replaced by the ADR in `superseded_by`. Kept for the audit trail. |
| `rejected` | Considered and declined. Kept so the question is not reopened without new facts. |

## Adding a decision

1. Copy [`_template.md`](_template.md) to `docs/decisions/NNNN-slug.md` with the next free
   number.
2. Fill in the frontmatter. Keep `status: proposed` and `status_confirmed: false`, and delete
   `superseded_by` unless it applies.
3. Write the four sections: `## Context`, `## Decision`, `## Consequences` and
   `## Alternatives considered`. Link the files and earlier ADRs involved, with paths
   relative to this folder.
4. If it replaces an earlier decision, set the earlier record's `status` to `superseded`,
   add its `superseded_by`, and update its `status_note`. Change nothing else in the earlier
   record.
5. Add its row to the index below, keeping the rows sorted by ID. Change only rows between
   the markers, not the markers themselves.

Features (`docs/features/`) refer to the decisions they rely on by ID in their `decisions:`
list.

## Index

<!-- BEGIN GENERATED: decisions index -->
| ID | Title | Status | Date |
|---|---|---|---|
| [ADR-0001](0001-local-docker-canonical-db.md) | Local Docker as canonical database; Railway as auth-only | superseded by ADR-0012 (unconfirmed) | 2026-05-08 |
| [ADR-0002](0002-live-us-market-data.md) | Live US market data via Schwab streaming + REST tier | accepted (unconfirmed) | 2026-05-08 |
| [ADR-0003](0003-sectoral-political-analytics.md) | Sectoral political-analytics layer | proposed (unconfirmed) | 2026-05-08 |
| [ADR-0004](0004-nas-migration.md) | Raw vendor data backed up to NAS | superseded by ADR-0012 (unconfirmed) | 2026-05-13 |
| [ADR-0005](0005-commodities-squeeze-signals.md) | Commodity squeeze-signal sleeve | proposed (unconfirmed) | 2026-05-15 |
| [ADR-0006](0006-pre-commit-enforcement.md) | Pre-commit enforcement | implemented (unconfirmed) | 2026-05-15 |
| [ADR-0007](0007-getting-live-again.md) | Getting live again | implemented (unconfirmed) | 2026-05-15 |
| [ADR-0008](0008-script-naming-india-historical.md) | Script naming convention + India historical build-out | superseded by ADR-0012 (unconfirmed) | 2026-05-16 |
| [ADR-0009](0009-us-script-rename.md) | Apply 008 naming convention to US scripts | superseded by ADR-0012 (unconfirmed) | 2026-05-16 |
| [ADR-0010](0010-hyperliquid-tokenized-equities.md) | Hyperliquid tokenized-equity perps as an after-hours signal source | proposed (unconfirmed) | 2026-06-23 |
| [ADR-0011](0011-provider-abstraction.md) | Strict provider abstraction for ingestion | accepted (unconfirmed) | 2026-09-24 |
| [ADR-0012](0012-uv-workspace-monorepo.md) | One uv workspace monorepo with namespace packages | accepted (unconfirmed) | 2026-09-29 |
| [ADR-0013](0013-per-component-images-and-tags.md) | One image per component, released on its own tag | accepted (unconfirmed) | 2026-09-29 |
| [ADR-0014](0014-structured-logs-and-retention.md) | Structured JSON logs with host-side retention and a read-only reader | accepted (unconfirmed) | 2026-09-29 |
| [ADR-0015](0015-host-deployer-and-rollback-classes.md) | Host deployer with per-component rollback classes | accepted (unconfirmed) | 2026-09-29 |
| [ADR-0016](0016-edge-routing-and-access.md) | Single hub hostname behind Cloudflare Tunnel and one Access application | accepted (unconfirmed) | 2026-09-29 |
<!-- END GENERATED: decisions index -->
