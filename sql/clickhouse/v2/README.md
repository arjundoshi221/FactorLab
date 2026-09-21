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
