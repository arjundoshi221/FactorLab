---
name: release-component
description: Release one FactorLab unit (component, platform or worker) to production on its own <unit>/vX.Y.Z tag. Only when the user explicitly asks for a release.
disable-model-invocation: true
---

# Releasing a unit

A release deploys to production. **Only run it when the user explicitly asks for this
release** (AGENTS.md). Read [docs/operations/releases.md](../../../docs/operations/releases.md)
and the unit's `CONTEXT.md` ("Release and rollback") first.

## Preconditions (check, do not assume)

- On `main`, clean, equal to `origin/main`; `ci.yml` green for HEAD.
- The unit's closure changed since its last tag (`uv run python tools/affected.py --base <last tag> --head HEAD`).
- Writers (`rollback: writer`): outside market hours for the markets they write; bump
  `data_contract` in `component.yaml` if written data changed.
- Schema changes need their migration applied first (owner-run, forward-only); a release
  never runs migrations.

## Release

```powershell
.\deploy\release.ps1 -Component <unit> -Bump patch|minor|major -DryRun   # review version + changelog
.\deploy\release.ps1 -Component <unit> -Bump patch|minor|major
```

The tag starts `component-release.yml`: tests, image build/publish/attest/verify, then
the host deployer (`deploy-component.sh` / `deploy-platform.sh`). Report the job summary
(verification and rollback result) to the user; do not infer success from the tag push.

## After

- Verify: `uv run python tools/read_logs.py list` shows the new version; `errors --since 1h`
  is clean; writers have fresh `meta.ingestion_runs` rows (`factorlab-clickhouse` skill).
- If it failed: the deployer already acted by rollback class (auto → rolled back,
  writer with a new data_contract → stopped for fix-forward, forward-only → failed). Fix
  forward with a new patch release; a manual rollback is `component-rollback.yml`
  (dispatch) and refuses writers across a data-contract change.
- Log the release on the shipped features (`## Log`, status `shipped`) and run
  `uv run python tools/check_docs.py --write`.
