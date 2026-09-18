# Contributing to FactorLab

## First-time setup

```bash
# Conda environment
conda env create -f environment.yml   # if present, else: conda create -n factorlab python=3.12
conda activate factorlab
pip install -e ".[dev]"

# Pre-commit hooks (REQUIRED — they enforce the security policies below)
pip install pre-commit
pre-commit install
```

Hooks now run automatically on every `git commit`. Anything they reject does
not enter version control.

## What pre-commit blocks

The repo policies are enforced mechanically, not by reviewer attention:

1. **Data / logs / secrets** (`scripts/_check_no_data_paths.py`)
   - No file under `data/`, `logs/`, or `.env*` may be staged.
   - Allowed exceptions: `tests/fixtures/**` (small curated samples) and
     `docs/**` (architecture diagrams, examples).
   - Bypass requires explicit `git add -f`. Don't.

2. **Plaintext secrets in committed files** (`scripts/_check_no_plaintext_secrets.py`)
   - Any line matching `(api_key|token|secret|access_token)=<15+ chars>` is
     blocked unless the value contains `REDACTED`, `<your_…>`, etc.
   - This catches both real leaked credentials and copy-pasted scratch URLs.

3. **gitleaks** scans staged content for known secret patterns from the
   gitleaks ruleset (AWS keys, GitHub tokens, etc.).

4. **Standard hygiene**: large files (>500 KB), private-key files, merge
   conflict markers, malformed YAML/JSON, trailing whitespace.

## What `.gitignore` covers

Defense in depth. The hooks above guard against bypasses, but most accidents
are stopped by `.gitignore` before they reach `git add`:

- `.env`, `.env.*`, `*_token.txt`, `*_credentials.json`
- `data/` (anything ingested or scraped)
- `logs/` (any output dir, including the dated political_*/ ones)
- Parquet, CSV, Arrow, DuckDB, SQLite files (with a small `tests/fixtures/` allowance)
- Postgres data / dumps / backups
- Build artifacts, caches, virtual envs, IDE files

## Bypassing hooks

Don't, except in emergencies. If you must, the message is `git commit --no-verify`
— and you should rotate any potentially exposed credentials immediately after.

## If the audit catches a leak

1. **Untrack** without deleting: `git rm --cached <path>` then commit.
2. **Rotate** any exposed API keys (Finnhub, FEC, Congress.gov, Schwab, EODHD,
   Upstox, IBKR — whichever appeared).
3. **Don't try to rewrite history** unless the repo is genuinely private and
   you're certain no clones exist. The leak is on disk somewhere; treat it
   as compromised.

## Running tests

```bash
pytest tests/ -v
```

## Running the political pipeline (for development)

```bash
# Verify-only — runs verify_political + a metrics snapshot, no ingest:
python scripts/us/political/us_political_daily.py --mode verify-only

# Daily — full Mon..Sat ingest cycle:
python scripts/us/political/us_political_daily.py --mode daily --dry-run

# Weekly — daily + heavy work + anomaly detection + verify:
python scripts/us/political/us_political_daily.py --mode weekly --skip-contracts --dry-run
```

See `docs/data-sources/political/pipeline.md` for the full operations runbook.
