@AGENTS.md

# Claude Code specifics

Everything in AGENTS.md applies; this adds what is specific to Claude Code.

- **Member context loads itself.** Each member directory has a `CLAUDE.md` that imports its
  `CONTEXT.md`, so reading files in `libs/<x>/`, `providers/<p>/` or `components/<c>/`
  brings in that member's context. Keep those files current in the same change.
- **Skills** (`.claude/skills/`): `feature` (write or update docs/features), `new-provider`,
  `new-component`, `release-component` (user-invoked only; a release deploys to production),
  `factorlab-logs` and `factorlab-clickhouse` (live production reads, only on request), and
  `grill-me` (stress-test a plan).
- **Agents** (`.claude/agents/`): `product`, `sprint`, `developer`, `bug-hunter`, `docs` and
  `clickhouse-steward`; see `.claude/agents/README.md` for who owns what.
- **Planning** lives in the repo, not in chat: features in `docs/features/`, the active
  sprint in `docs/sprints/`, decisions in `docs/decisions/`. Record outcomes there.
- **Scratch work** goes under `.tmp/<task-name>/` and is removed when the task is done.
