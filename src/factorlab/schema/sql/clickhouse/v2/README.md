# FactorLab v2 ClickHouse migrations

These are forward-only migration waves. Legacy tables in the configured source
database remain authoritative and are never altered or dropped.

Run schema, resolver/import tooling, backfill, validation, and RBAC explicitly:

```powershell
python scripts/migrate_clickhouse_v2.py plan
python scripts/migrate_clickhouse_v2.py apply --phase schema --through-wave 4 --yes
python scripts/migrate_clickhouse_v2.py apply --phase backfill --through-wave 4 --yes
python scripts/migrate_clickhouse_v2.py validate --through-wave 4
python scripts/migrate_clickhouse_v2.py apply --phase rbac --through-wave 8 --yes
```

`CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_USER`,
`CLICKHOUSE_PASSWORD`, and `CLICKHOUSE_SECURE` configure the connection.
`--source-database` defaults to `factorlab`.

Resolver-owned rows must be approved and carry a lowercase SHA-256 of
`toJSONString(tuple(*))` for the complete `FINAL` legacy source row (in physical
column order). This expression is also used by the validation SQL.
Updating a legacy row therefore makes its mapping stale and validation blocks
the dependent backfill until the resolver approves a new mapping. Canonical
entity, security, listing, contract, and political-trade UUIDs are supplied by
the resolver; migration SQL never creates them.

`deferred.json` is the authoritative list of design contracts intentionally not
made runnable by this package.

## Forward waves

A table added to `docs/architecture/06-schema-rehau.md` after its namespace's
wave was applied must not land in that wave's file: that would change an
applied migration's checksum. List it in `FORWARD_TABLES` in
`scripts/generate_clickhouse_v2_schema.py`; the generator then emits it to its
own `wave_NN_schema_<suffix>.sql`.

- **Wave 10** (`*_source_priorities`, 06 rev 12): `ref.source_priorities` and
  the read-time `market.bars_best` view (07 §10). After applying, run
  `python scripts/factlab_ingest.py sync-priorities` to load priorities from
  `configs/ingestion/bindings.yaml`. Until a provider is `primary`/`secondary`,
  `market.bars_best` returns no rows for it.
