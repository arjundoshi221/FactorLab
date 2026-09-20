# FactorLab Team Skills

Shared Claude Code skills committed to the repo so **Arjun** and **Jai** get the same behavior. Auto-discovered by Claude Code from `.claude/skills/team/*/SKILL.md`.

The rest of `.claude/skills/` is personal / gitignored — only this folder is committed.

## Skills

| Skill | Trigger | Purpose | Source |
|-------|---------|---------|--------|
| [`grill-me`](grill-me/SKILL.md) | say "grill me" or invoke `/grill-me` | Relentless Socratic interview to stress-test a plan or design before it becomes a spec | [RobMitt/grill-me-skill](https://github.com/RobMitt/grill-me-skill) |

## When each skill fires

- **`grill-me`** — invoked automatically by the [`product`](../../agents/team/product.md) agent as the mandatory first step of every feature intake. Can also be invoked manually when reviewing a plan, design doc, or architecture proposal.

## Updating a skill

If upstream ships an update:
1. Fetch the new `SKILL.md` from the source repo
2. Diff against the local copy — review any behavior changes before merging
3. Commit the update with a message like `skills: update grill-me to <upstream commit>`

Never edit a skill in-place without a note about *why* we diverge from upstream — otherwise sync becomes impossible.
