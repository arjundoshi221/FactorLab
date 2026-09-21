# 006 — Pre-commit enforcement

**Status:** `[proposed]` · **Owner:** ritchie + factorlab-pm · **Security review:** heimdall · **Drafted:** 2026-05-15

## Decision

Treat `.pre-commit-config.yaml` as a **required** developer-environment setup step, not a documentation suggestion. Every new clone of the repo runs `pre-commit install` once; every `git commit` thereafter runs the full hook suite and **blocks** on failure. CI (if/when added) runs `pre-commit run --all-files` to enforce the same gates on PRs.

## Rationale

The config exists but doesn't enforce anything until each developer runs `pre-commit install` in their clone. Today that's nobody — which means:

1. **Secrets can ship.** Gitleaks + the local `no-plaintext-secrets` hook catch `?api_key=…` / `?token=…` patterns. If neither is wired into `.git/hooks/pre-commit`, a careless `git add` of a cached FEC response leaks the key.
2. **Path discipline drifts.** Phase 1 established `factorlab.shared.paths.raw_dir(...)`. The Phase 7 lint `no-hardcoded-raw-paths` rejects new `Path("data/<source>/raw/...")` literals — but only if it runs. Without enforcement, drift creeps in over months.
3. **Large files ship by accident.** `check-added-large-files --maxkb=500` would block a stray PDF or parquet. Without it, our 37 GB NAS-bound dataset risks getting `git add -A`'d into history.

Manual discipline doesn't scale across multiple machines / future contributors / future-me.

## What's wired

Already in `.pre-commit-config.yaml` (no edits needed):

| Hook                       | Source                          | What it blocks                                                          |
|----------------------------|---------------------------------|-------------------------------------------------------------------------|
| `check-added-large-files`  | pre-commit/pre-commit-hooks v4.6.0 | Any file > 500 KB                                                       |
| `detect-private-key`       | pre-commit/pre-commit-hooks     | RSA / SSH private-key file patterns                                     |
| `check-merge-conflict`     | pre-commit/pre-commit-hooks     | Leftover `<<<<<<<` markers                                              |
| `check-yaml`, `check-json` | pre-commit/pre-commit-hooks     | Malformed YAML / JSON                                                   |
| `end-of-file-fixer`        | pre-commit/pre-commit-hooks     | Auto-fix: missing trailing newline                                      |
| `trailing-whitespace`      | pre-commit/pre-commit-hooks     | Auto-fix: trailing whitespace                                           |
| `gitleaks`                 | gitleaks v8.18.4                | Known secret patterns across staged diff (AWS, GCP, GitHub PAT, JWT…)   |
| `no-data-or-logs`          | `scripts/_shared/_check_no_data_paths.py` | Files under `data/` / `logs/` / `.env` — defense in depth on `.gitignore` |
| `no-plaintext-secrets`     | `scripts/_shared/_check_no_plaintext_secrets.py` | Plaintext `api_key=...` / `token=...` query strings in staged text |
| `no-hardcoded-raw-paths`   | `scripts/_shared/_check_paths_in_code.py` | New `Path("data/<source>/raw/...")` literals in `src/` or `scripts/`     |

## Activation (per clone)

```powershell
# One-time install
pip install pre-commit
pre-commit install

# Sanity-check the existing tree against the hooks
pre-commit run --all-files
```

`pre-commit install` writes a shim at `.git/hooks/pre-commit` that delegates to the framework. The shim must be re-installed in any fresh clone; no auto-mechanism exists today.

## Tasks (proposed sequence)

1. **Baseline scan.** Run `pre-commit run --all-files` on the current `main`. Fix or grandfather any failures into the hook configs.
2. **Install on this machine.** `pre-commit install` in the working clone.
3. **Document the requirement.** Update `README.md` "Getting started" with the install line. Add a CONTRIBUTING.md when there's a second contributor.
4. **(Optional, when CI lands)** Add a GitHub Actions workflow that runs `pre-commit run --all-files` on every PR. This is the only durable enforcement — local hooks can be bypassed with `git commit --no-verify`. The plan rule already forbids `--no-verify` unless explicitly authorized.
5. **Quarterly cadence.** Bump `gitleaks` and `pre-commit-hooks` `rev:` tags during the quarterly housekeeping window. Pinned versions are intentional — drift surfaces as a controlled bump, not a silent change.

## Open questions

- **Heimdall sign-off on the secret rules.** The `_check_no_plaintext_secrets.py` regex matches `(api_key|token|access_token|key|auth)=...` — same shape as `factorlab.shared.ingest.security.SECRET_QS_KEYS`. If we add a new credentialled vendor with a different parameter name (e.g. `subscription-key`), both lists need updating in lockstep. Owner: heimdall.
- **CI host.** No CI today. When we add one, `pre-commit run --all-files` is the single command that gates a PR.
- **Large-file threshold.** 500 KB is conservative. A legitimate ~600 KB Excel master list (universe configs?) would trip it. Adjust per-need; the existing data/logs ban handles the bulk-data case anyway.

## What this is explicitly NOT doing

- Not adding CI (no `.github/workflows/`). That's a separate decision; this development plan just makes the existing hooks bite locally.
- Not changing the hooks themselves. The configuration already shipped in Phase 6b.
- Not requiring `--no-verify` to be impossible — local bypass is a footgun the user can choose to deploy if they understand the risk. The project rule (in `CLAUDE.md`) bans it for AI sessions.

## When this lands

Status → `[shipped]` once: (1) `pre-commit install` is run on this machine, (2) one clean commit happens through the active hook chain, (3) `README.md` carries the install line so future-me sees it.
