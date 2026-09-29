# Migration from docs/developments

The numbered documents in `docs/developments/` became decision records here, with the same
slug and a four-digit number. Each body is the original document unchanged, except for three
things:

- The old status line (`> Status: ...` or `**Status:** ...`) was removed. Its status and date
  moved into the YAML frontmatter, and a `status_note` gives the status as of 2026-09-30.
- Frontmatter was added (see [README.md](README.md)).
- Links to sibling documents point to the new file names (for example
  `001-local-docker-canonical-db.md` became `0001-local-docker-canonical-db.md`). Links such
  as `../architecture/...` resolve unchanged, because `docs/decisions/` is a sibling of
  `docs/developments/`.

`docs/developments/README.md` has no counterpart. Its conventions and index are replaced by
[README.md](README.md).

| Old path | New path | Old status | New status |
|---|---|---|---|
| `docs/developments/001-local-docker-canonical-db.md` | [`docs/decisions/0001-local-docker-canonical-db.md`](0001-local-docker-canonical-db.md) | `[accepted]` | superseded by ADR-0012 |
| `docs/developments/002-live-us-market-data.md` | [`docs/decisions/0002-live-us-market-data.md`](0002-live-us-market-data.md) | `[accepted]` | accepted |
| `docs/developments/003-sectoral-political-analytics.md` | [`docs/decisions/0003-sectoral-political-analytics.md`](0003-sectoral-political-analytics.md) | `[proposed]` | proposed |
| `docs/developments/004-nas-migration.md` | [`docs/decisions/0004-nas-migration.md`](0004-nas-migration.md) | `[in-progress]` | superseded by ADR-0012 |
| `docs/developments/005-commodities-squeeze-signals.md` | [`docs/decisions/0005-commodities-squeeze-signals.md`](0005-commodities-squeeze-signals.md) | `[proposed]` | proposed |
| `docs/developments/006-pre-commit-enforcement.md` | [`docs/decisions/0006-pre-commit-enforcement.md`](0006-pre-commit-enforcement.md) | `[proposed]` | implemented |
| `docs/developments/007-getting-live-again.md` | [`docs/decisions/0007-getting-live-again.md`](0007-getting-live-again.md) | `[proposed]` (`[in-progress]` in the old index) | implemented |
| `docs/developments/008-script-naming-india-historical.md` | [`docs/decisions/0008-script-naming-india-historical.md`](0008-script-naming-india-historical.md) | `[accepted]` | superseded by ADR-0012 |
| `docs/developments/009-us-script-rename.md` | [`docs/decisions/0009-us-script-rename.md`](0009-us-script-rename.md) | `[accepted]` | superseded by ADR-0012 |
| `docs/developments/010-hyperliquid-tokenized-equities.md` | [`docs/decisions/0010-hyperliquid-tokenized-equities.md`](0010-hyperliquid-tokenized-equities.md) | `[proposed]` | proposed |
| `docs/developments/011-provider-abstraction.md` | [`docs/decisions/0011-provider-abstraction.md`](0011-provider-abstraction.md) | `[in-progress]` | accepted |

Every migrated record has `status_confirmed: false` until the owner confirms its new status.
