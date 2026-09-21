# FactorLab — Agent Team (Nandi OS)

This project is **onboarded to Nandi**, the personal agent operating system at [`E:\AGENTS\`](file:///E:/AGENTS/). Every change here is owned by a Nandi specialist. Generic / unscoped agents are not used for work that has an owner — the routing table below is authoritative.

- **Nandi OS spec:** [`E:\AGENTS\SPEC.md`](file:///E:/AGENTS/SPEC.md)
- **Nandi OS map:** [`E:\AGENTS\CLAUDE.md`](file:///E:/AGENTS/CLAUDE.md)
- **Agent definitions:** [`E:\AGENTS\.claude\agents\<name>.md`](file:///E:/AGENTS/.claude/agents/)
- **FactorLab project memory:** [`E:\AGENTS\memory\projects\factorlab\`](file:///E:/AGENTS/memory/projects/factorlab/)

---

## The team that owns FactorLab

| Role                                  | Agent             | What they own here                                                                          |
|---------------------------------------|-------------------|---------------------------------------------------------------------------------------------|
| **Project manager** (single owner)    | **factorlab-pm**  | Roadmap, milestones, DoD, blockers, status. Writes `memory/projects/factorlab/`.            |
| Head of PMO                           | donna             | Cross-project view, portfolio standards, onboarding. Reads factorlab-pm's log weekly.       |
| **CTO** (technical direction)         | **turing**        | Architecture, ADRs, stack choices. Cross-cutting design. Delegates to his team below.       |
| Database administrator                | codd              | Postgres / Timescale / Railway schema, migrations, indexes. (Schema is frozen this round.)  |
| OS / scripts / Task Scheduler         | ritchie           | Windows scripts, `.bat` wrappers, scheduled jobs. The conduit for "make it run on schedule." |
| Environment / lockfiles / `.env`      | faraday           | `pyproject.toml`, Conda env, env-var hygiene, `paths.py`.                                   |
| Cloud / Railway                       | cerf              | Railway deploys (Postgres, Upstox auth-server). DNS / cost.                                 |
| Containers (deferred)                 | mclean            | Not active this round — containerization is paused.                                         |
| Code reviewer (on-demand)             | fowler            | DRY / dead code / naming pass at end of phases 3 + 5. Never auto-fired.                     |
| **Security sentinel**                 | **heimdall**      | Gate-keeper on secrets, tokens, NAS perms, Outlook bridge. Reviews; doesn't implement.      |
| News / political beat (read-only)     | bernstein         | Tracks legislative / regulatory events that touch FactorLab's political-data sources.       |
| Risk / red-team / veto                | watson            | Reviews any investment-flavored change. Rare for infra work.                                 |

**Routing rule of thumb:**

- *"How should X be designed?"* → **turing**
- *"What's the next deliverable?"* → **factorlab-pm**
- *"Is this safe to ship?"* → **heimdall** (security) or **watson** (risk)
- *"How does it run on Windows / at the right time?"* → **ritchie**
- *"What's the env / package shape?"* → **faraday**
- *"Where does the data live?"* → **codd** (DB) or **ritchie** (NAS) or **faraday** (`paths.py`)
- *"Did we already decide this?"* → search `memory/projects/factorlab/decisions.md` and `memory/infra/decisions.md`

Independent specialists run in parallel — one message, multiple Agent calls.

---

## How the agents see this project

Every Nandi agent reads from and writes to its own memory namespace. They do **not** scribble into each other's directories.

```
E:\AGENTS\memory\
├── projects\factorlab\              ← factorlab-pm writes; donna + nandi read
│   ├── plan.md
│   ├── outcomes.md
│   ├── architecture.md
│   ├── decisions.md
│   ├── blockers.md
│   ├── log.md
│   └── docs\
├── infra\                           ← turing + team write
│   ├── decisions.md                 (ADRs; turing)
│   ├── architecture.md              (turing)
│   ├── containers\                  (mclean)
│   ├── databases\                   (codd)
│   ├── cloud\                       (cerf)
│   ├── envs\                        (faraday)
│   ├── os\                          (ritchie)
│   └── runbooks\                    (turing curates; team contributes)
├── security\                        ← heimdall writes
├── news\                            ← lois + desk write
└── shared\decisions\                ← cross-agent decisions
```

The matching project layout for code lives in this repo — see `docs/operations/` for runbooks once Phase 7 is complete.

---

## Current state (2026-05-13)

FactorLab is **live, in Phase 0 of a restructure**. See [`memory/projects/factorlab/plan.md`](file:///E:/AGENTS/memory/projects/factorlab/plan.md) for the eight phases. The active restructure plan also lives at `C:\Users\arjd2\.claude\plans\factorlab-foundations-2026Q2.md`.

Headline outcomes of the restructure:

1. `country × domain` taxonomy for every script and source module.
2. `src/factorlab/shared/{paths,runtime,notify,ingest,storage}` as the one place duplicated patterns live.
3. Raw vendor dumps stay in repo (canonical) and are mirrored to `E:\NAS\factorlab\raw\` by a weekly one-way sync script.
4. One notifier (`notify(...)`) routes through this device's Outlook via a host-side daemon.
5. One backfill CLI dispatcher (`factlab_backfill.py`) with per-source `Backfiller` implementations.

**No containers in this round.** Everything runs locally via Windows Task Scheduler and the Conda env at `C:\Users\arjd2\.conda\envs\factorlab\python.exe`.

---

## For collaborators (humans and agents)

- Read [`docs/README.md`](docs/README.md) for the system overview.
- Read [`memory/projects/factorlab/plan.md`](file:///E:/AGENTS/memory/projects/factorlab/plan.md) for what's actually being built right now.
- Before opening a PR, identify which Nandi specialist owns the change and route accordingly.
- All architectural decisions are recorded in `memory/infra/decisions.md` (ADR format). Don't reopen a decided question without an ADR amendment.
- Heimdall reviews every change that touches secrets, tokens, OAuth flows, or new external network surface.

Nandi orchestrates; specialists execute. There is no "general-purpose agent" for this project.
