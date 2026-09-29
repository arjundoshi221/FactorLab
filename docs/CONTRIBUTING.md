# Contributing to FactorLab

Start with [AGENTS.md](../AGENTS.md) (repository rules), the
[repository layout](architecture/08-repository-layout.md), and the `CONTEXT.md` of the
member you are changing.

## First-time setup

```bash
# Python 3.12 and uv (https://docs.astral.sh/uv/)
uv sync --all-packages                         # every member, editable, plus dev tools
uv run pre-commit install --install-hooks      # REQUIRED: the hooks enforce the policies below
uv run pre-commit install --hook-type pre-push # the type check runs before push
git config blame.ignoreRevsFile .git-blame-ignore-revs

# Web UI (Node 22)
cd components/web && npm ci
```

Local runs read no `.env` implicitly; pass it explicitly when you need it:
`uv run --env-file .env python -m factorlab.components.ingest_us ...`. Never commit `.env`.

## Everyday commands

```bash
uv run pytest libs/storage                     # focused tests for one member
uv run pytest                                  # everything
uv run ruff check . && uv run ruff format .    # lint and format
uv run basedpyright                            # types (strict on libs; baseline elsewhere)
uv run python tools/components.py check        # component manifests
uv run python tools/check_docs.py --write      # regenerate doc indexes, then check docs
cd components/web && npm test && npm run build
```

CI (`.github/workflows/ci.yml`) runs all of this plus isolated per-member installs, image
builds with closure checks, workflow and shell linting and coverage floors; `ci-ok` is the
one required check.

## Adding things

- A provider, component or library: `uv run python tools/scaffold.py new-provider|new-component|new-lib`
  (the `new-provider` / `new-component` skills explain the follow-ups).
- A schema change: a new wave under `libs/schema/.../sql/clickhouse/v2/`, recorded with
  `uv run python tools/schema_checksums.py add`. Applied waves are never edited.
- Planned work: a feature in `docs/features/`; architectural choices: an ADR in `docs/decisions/`.

## Commits and releases

Conventional commits (`feat(api): …`, `fix(ingest-us): …`): the release tool builds each
unit's changelog from them. Releases are per unit (`deploy/release.ps1 -Component <unit>`,
see [operations/releases.md](operations/releases.md)) and change production.

## What pre-commit blocks

The repo policies are enforced mechanically, not by reviewer attention:

1. **Data / logs / secrets** (`tools/hooks/check_no_data_paths.py`)
   - No file under `data/`, `logs/`, or `.env*` may be staged.
   - Allowed exceptions: `tests/fixtures/**` (small curated samples) and
     `docs/**` (architecture diagrams, examples).
   - Bypass requires explicit `git add -f`. Don't.

2. **Plaintext secrets in committed files** (`tools/hooks/check_no_plaintext_secrets.py`)
   - URL query strings (`?api_token=…`) and quoted literals assigned to secret-named keys
     (`token = "…"`) with 15+ characters including digits or mixed case are blocked unless
     the value contains `REDACTED`, `<your_…>`, etc.

3. **gitleaks** scans staged content for known secret patterns from the
   gitleaks ruleset (AWS keys, GitHub tokens, etc.).

4. **Project checks**: ruff (lint and format), `uv.lock` consistency, component manifests,
   forward-only migration checksums, architecture rules, hardcoded raw-data paths
   (`tools/hooks/check_paths_in_code.py`), and basedpyright before push.

5. **Standard hygiene**: large files (>500 KB), private-key files, merge
   conflict markers, malformed YAML/JSON/TOML, trailing whitespace, line endings (LF).

## What `.gitignore` covers

Defense in depth. The hooks above guard against bypasses, but most accidents
are stopped by `.gitignore` before they reach `git add`:

- `.env`, `.env.*` (except `.env.example`), `*_token.txt`, `*_credentials.json`
- `data/` (anything ingested or scraped)
- `logs/` and `.tmp/` (scratch work)
- Parquet, CSV, Arrow, DuckDB, SQLite files (with a small `tests/fixtures/` allowance)
- Build artifacts, caches, virtual envs, IDE files

## Bypassing hooks

Don't, except in emergencies. If you must, the message is `git commit --no-verify`
— and you should rotate any potentially exposed credentials immediately after.

## If the audit catches a leak

1. **Untrack** without deleting: `git rm --cached <path>` then commit.
2. **Rotate** any exposed API keys (FEC, Congress.gov, Schwab, EODHD, Upstox, IBKR,
   ClickHouse, the FactorLab API key — whichever appeared).
3. **Don't try to rewrite history** unless the repo is genuinely private and
   you're certain no clones exist. The leak is on disk somewhere; treat it
   as compromised.
