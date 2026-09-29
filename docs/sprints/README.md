# Sprints

Two-week sprints named `YYYY-Sxx`, starting on Mondays. 2026-S20 runs Sep 28 – Oct 11,
and each later sprint follows on. One file per sprint, from [_template.md](_template.md),
with `status: planned | active | closed`; only one sprint is active at a time.

A sprint's `## Planned` list is generated from the features whose `sprint:` names it
(`uv run python tools/check_docs.py --write`). Plan work by editing the features, not
the list. Close a sprint by writing its `## Review` and setting `status: closed`; move
unfinished features to the next sprint.

<!-- BEGIN GENERATED: sprints index -->
| Sprint | Dates | Status | Goal |
|---|---|---|---|
| [2026-S20](2026-S20.md) | 2026-09-28 – 2026-10-11 | active | Close the hub exposure, merge platform v3 and start per-component releases. |
<!-- END GENERATED: sprints index -->
