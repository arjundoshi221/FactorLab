---
name: new-provider
description: Add a new FactorLab data provider (a factorlab-provider-* workspace member) behind the ingestion abstraction, from scaffold to recorded fixtures to a shadow binding. Use when the user wants to ingest data from a new vendor or API, or to replace or duplicate an existing provider.
---

# Adding a provider

The contract is [docs/architecture/07-ingestion-provider-abstraction.md](../../../docs/architecture/07-ingestion-provider-abstraction.md):
a provider turns a request for a *dataset* into typed records (`plan → fetch → normalize`)
and never touches storage. Read an existing small provider first
([providers/github-csv](../../../providers/github-csv/CONTEXT.md)).

**Switching or duplicating a provider for an existing dataset needs no new code if one
exists:** change roles in `configs/ingestion/bindings.yaml` (primary / secondary / shadow).

## Steps

1. Check the dataset exists: `uv run python -c "from factorlab.ingest.datasets import DATASETS; print(sorted(DATASETS))"`.
   A new dataset is a bigger change (dataset module, a new schema wave, a sink) — plan it
   as a feature with the `clickhouse-steward` agent first.
2. Scaffold (runs `uv lock`, `uv sync` and the new tests):
   ```bash
   uv run python tools/scaffold.py new-provider <name> --dataset <id> --market <ISO3> \
       [--resolutions 1min,daily] --component <component> --bind --summary "..."
   ```
   `--component` wires it into the component that ships it (dependency, `PROVIDERS`,
   manifest); `--bind` adds `configs/sources/<name>.yaml` and a **shadow** binding.
3. Implement `sources.py`: `plan` (fetch units), `fetch` (one call → `RawCapture`),
   `normalize` (offline, deterministic records). Use `factorlab.ingest.transport` helpers
   and the typed errors (`TransientError`, `PermanentError`, `AuthRequired`,
   `NormalizationError`). Allowed imports: `factorlab.core`, `factorlab.calendars`,
   `factorlab.ingest` only (rule R8).
4. Record real captures under `providers/<name>/tests/fixtures/<dataset>/`
   (`<case>.capture.json` + `<case>.expected.json`). The conformance tests fail until
   they exist; never hand-write a capture you did not fetch. Scrub credentials.
5. Secrets: name them in settings (`Secret("NAME")`) and in the component manifest's
   `secrets:`; the owner adds values to the Cloudflare secret store and the secrets agent.
6. Fill in `providers/<name>/CONTEXT.md`, then:
   ```bash
   uv run pytest providers/<name> && uv run python tools/components.py check
   uv run pytest tests/architecture && uv run python tools/check_docs.py
   ```
7. Ship it with the component release (`deploy/release.ps1 -Component <component>`). It
   runs as shadow; promotion to primary follows a parity check (07 §15.2) and is a
   bindings change plus another release.
