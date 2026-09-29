---
name: feature
description: Write or update a FactorLab feature file (docs/features/F-NNN-<slug>.md) and keep the generated indexes, sprint lists and hub roadmap in sync. Use when the user describes new work, asks to plan something into a sprint, changes a feature's status, or ships one.
---

# Writing a FactorLab feature

Features are the one planning system: `docs/features/F-NNN-<slug>.md`, sprints in
`docs/sprints/`, decisions in `docs/decisions/`. `tools/check_docs.py` validates them in CI.

## New feature

1. If the idea is still fuzzy, stress-test it first with the `grill-me` skill.
2. Take the next free number: `ls docs/features/F-*.md` (numbers are never reused).
3. Copy `docs/features/_template.md` to `docs/features/F-NNN-<kebab-slug>.md` and fill:
   - `status`: `idea` (unshaped), `backlog` (shaped, not scheduled), `planned` (in a sprint).
   - `priority`: P0 blocks other work or is a production risk; P1 this quarter; P2 soon; P3 someday.
   - `components`: release units or members it changes (`api`, `ingest-us`, `platform`,
     `storage`, …). These decide which releases carry it.
   - `decisions`: ADRs it relies on; write a new ADR (docs/decisions/_template.md) when the
     feature makes an architectural choice.
   - `links`: related features.
   - `roadmap:` only for items that belong on the hub's /roadmap page.
4. Body: Problem (who is hurt, today), Scope, Non-goals, Acceptance criteria as checkable
   boxes, Rollout (which component releases, migrations or owner actions), Log.

## Changing a feature

- Plan it: set `sprint: YYYY-Sxx` (the file must exist in docs/sprints/) and `status: planned`.
- Every status change: update `updated:` and add a dated `## Log` line
  (`- 2026-10-02: in-progress (PR #…)`, `- …: shipped in ingest-us/v1.3.0`).
- Dropping: `status: dropped` and the reason in the log; the file stays.

## Always finish with

```bash
uv run python tools/check_docs.py --write   # regenerate indexes, sprint lists, roadmap json
uv run python tools/check_docs.py           # must print "docs are consistent"
```

Commit the feature file together with the regenerated files. A changed roadmap feature
also changes `components/web/src/roadmap.generated.json`, which ships with the next `web`
release.
