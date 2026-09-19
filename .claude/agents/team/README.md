# FactorLab Team Agents

These are the five Claude Code sub-agents committed to the repo so **Arjun** and **Jai** get the same behavior. They are auto-discovered by Claude Code from `.claude/agents/team/*.md`.

The rest of `.claude/` is personal / machine-local and gitignored. Only this folder is committed (see `.gitignore` — the `.claude/*` block).

## Non-negotiable rule: tests for everything

Every agent enforces this. There is no exception, no "we'll add tests later," no "this one is too simple."

- **Every new function** → at least one pytest test covering the interesting path + one failure case
- **Every bug fix** → a regression test that fails before the fix and passes after (`bug-hunter` writes it first)
- **Every migration** → a test in [`tests/migrations/`](../../tests/migrations/) that applies to a scratch schema and asserts shape
- **Every non-trivial query** → a fixture-seeded test in [`tests/queries/`](../../tests/queries/)
- **Every feature spec's acceptance criterion** → phrased as a pytest assertion, not prose
- **Every sprint deliverable's "definition of done"** → the pytest node ID that must pass

Run the suite: `"C:/Users/arjd2/.conda/envs/factorlab/python.exe" -m pytest tests/ -x`

No PR merges with red CI. No sprint item flips to "done" without green tests. Missing tests is a **blocking** review comment.

## The five agents

| Agent | Role | Owns | Doesn't own |
|-------|------|------|-------------|
| [`product`](product.md) | Feature intake | Specs, acceptance criteria, non-goals, backlog hygiene | Prioritization, architecture, estimation |
| [`sprint`](sprint.md) | PM + sprint runner | Roadmap, 2-week sprints, definition-of-done, blockers, status | Feature intake, bug triage, schema decisions |
| [`bug-hunter`](bug-hunter.md) | Bug investigator | Repro, RCA, severity, regression detection, **blast-radius analysis**, failing-test-first | Writing the fix, merging PRs |
| [`developer`](developer.md) | Code reviewer + clean-code writer | Diff reviews, readability, dead-code deletion, reusable-when-real, matching existing patterns | Deciding what to build, DB migrations, triage |
| [`dba`](dba.md) | Database lead | Schema, migrations, indexes, query correctness, backup verification, canonical views | Cloud infra, app code calling the DB, secrets |

## How they hand off

```
new ask       →  product    →  sprint  →  developer (write)  →  developer (review)  →  merge
new bug       →  bug-hunter →  sprint  →  developer (fix, with failing test + blast-radius map)
schema change →  dba (design)  →  sprint (schedule)  →  dba (migration)  →  developer (Python around it)  →  docs
code review   →  developer  (before any non-trivial merge)
```

- **`product`** clarifies asks and writes specs; hands to `sprint` for prioritization.
- **`sprint`** decides what goes in the current 2-week window and tracks blockers.
- **`bug-hunter`** turns "it's broken" into "here's the failing test + root cause + fix location + blast radius"; hands to `developer` to fix.
- **`developer`** reviews every non-trivial diff before merge and writes new code that's clean, readable, and reusable *only where reuse is real* (no premature abstraction).
- **`dba`** reviews *every* SQL and migration touching FactorLab schemas before merge. Enforces canonical views (e.g., always query `alt_political_us.legislator_trades_dedup`, not the raw tables).

## Invoking them

In a Claude Code session, either:

- Let Claude pick the right agent based on the request, or
- Ask explicitly: *"Use the `dba` agent to review this migration"* / *"Have `bug-hunter` triage issue #42"*

For long-running or independent work, they can be spawned in parallel via the `Task` tool.

## Where each agent writes

- `product` → `docs/product/features/` + `docs/product/_backlog.md`
- `sprint` → `docs/sprints/` (one file per 2-week sprint)
- `bug-hunter` → `docs/quality/bugs/` + `docs/quality/_triage.md` + `docs/quality/patterns.md`
- `developer` → `docs/engineering/` (`style.md`, `patterns.md`, `review-log.md`, `refactor-candidates.md`)
- `dba` → `docs/architecture/06-schema-rehau.md`, `docs/database/`, `docs/data-sources/`

If any of those directories don't exist yet, the agent creates them on first use.

## Editing these agents

- Any change to an agent should be reviewed by the other collaborator (Arjun/Jai) — same as reviewing a policy change.
- Frontmatter fields (`name`, `description`, `tools`) are load-bearing — Claude Code reads them to route requests. Don't change `name` without checking existing references.
