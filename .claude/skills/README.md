# FactorLab Team Skills

Shared Claude Code skills committed to the repo so **Arjun** and **Jai** get the same behavior. Auto-discovered by Claude Code from `.claude/skills/*/SKILL.md`.

Everything in `.claude/skills/` is committed; personal skills belong in `~/.claude/skills/`.

## Skills

| Skill | Trigger | Purpose | Source |
|-------|---------|---------|--------|
| [`grill-me`](grill-me/SKILL.md) | say "grill me" or invoke `/grill-me` | Relentless Socratic interview to stress-test a plan or design before it becomes a spec | [RobMitt/grill-me-skill](https://github.com/RobMitt/grill-me-skill) |
| [`feature`](feature/SKILL.md) | new work, planning, status changes | Write or update `docs/features/F-NNN-*.md` and regenerate indexes and the roadmap | FactorLab |
| [`new-provider`](new-provider/SKILL.md) | a new vendor or API | Scaffold a provider behind the ingestion abstraction, record fixtures, bind as shadow | FactorLab |
| [`new-component`](new-component/SKILL.md) | a new independently released service | Scaffold a component with its image, compose fragment, logs and tags | FactorLab |
| [`release-component`](release-component/SKILL.md) | `/release-component` only (never automatic) | Release one unit on its `<unit>/vX.Y.Z` tag and verify it | FactorLab |
| [`factorlab-logs`](factorlab-logs/SKILL.md) | debugging production, on request | Read component logs through the restricted log-reader account | FactorLab |
| [`factorlab-clickhouse`](factorlab-clickhouse/SKILL.md) | production data questions, on request | Bounded read-only ClickHouse queries; cross-link run_id with logs | FactorLab |

## When each skill fires

- **`grill-me`** — invoked automatically by the [`product`](../agents/product.md) agent as the mandatory first step of every feature intake. Can also be invoked manually when reviewing a plan, design doc, or architecture proposal.

## Updating a skill

If upstream ships an update:
1. Fetch the new `SKILL.md` from the source repo
2. Diff against the local copy — review any behavior changes before merging
3. Commit the update with a message like `skills: update grill-me to <upstream commit>`

Never edit a skill in-place without a note about *why* we diverge from upstream — otherwise sync becomes impossible.
