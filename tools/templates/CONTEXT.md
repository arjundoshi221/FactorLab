# {name}

> {summary}

<!-- Context for people and AI agents working on this member. Keep it under 150 lines,
     true to the code (link to it rather than repeat it), and update it in the same
     change that alters behaviour. tools/check_docs.py checks the headings below. -->

## Purpose

What this member is for, in two or three sentences, and where it sits
(library / provider / component).

## Owns and does not own

- Owns: ...
- Does not own: ... (and which member does)

## Entry points

Console scripts, CLI subcommands, public modules or functions, HTTP routes, compose
services. "None (library)" is a valid answer.

## Configuration and secrets

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|

Secrets are listed by *name* only; values live in the Cloudflare secret stores and
runtime secret volumes.

## Data

ClickHouse tables read or written, pipeline / `source` / `source_channel` names
(frozen strings), files and host paths.

## Dependencies and contracts

Workspace members and third-party packages it relies on, and the contracts it must
keep (dataset types, sink protocol, API response models, manifest fields).

## Observability

Log service names and fields, heartbeats, health checks, `meta.*` rows it writes, and
how to read them (`factorlab-logs` / `factorlab-clickhouse` skills).

## Tests

Where the tests live and the focused command, e.g.
`uv run pytest libs/<member>`.

## Release and rollback

How changes reach production (for a library: through the components that depend on
it), what a version bump means, and the rollback class.

## Pitfalls

Things that have bitten before or are easy to get wrong.
