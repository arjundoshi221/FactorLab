# Local PostgreSQL Setup

## Installation

- **Version**: PostgreSQL 18
- **Install path**: `C:\Program Files\PostgreSQL\18\`
- **Data directory**: `C:\Program Files\PostgreSQL\18\data\`
- **Platform**: Windows 11

Installed via the official PostgreSQL Windows installer. The `bin/` directory is **not** on PATH by default.

## Authentication

- **Method**: `trust` for local connections (no password required)
- **Superuser**: `postgres`
- **Config**: `C:\Program Files\PostgreSQL\18\data\pg_hba.conf`

To switch to password authentication later, edit `pg_hba.conf` and change `trust` to `scram-sha-256`, then reload:

```cmd
"C:\Program Files\PostgreSQL\18\bin\pg_ctl" reload -D "C:\Program Files\PostgreSQL\18\data"
```

## Starting / Stopping

### Manual

```cmd
:: Start
"C:\Program Files\PostgreSQL\18\bin\pg_ctl" start -D "C:\Program Files\PostgreSQL\18\data" -l "C:\Program Files\PostgreSQL\18\data\server.log"

:: Stop
"C:\Program Files\PostgreSQL\18\bin\pg_ctl" stop -D "C:\Program Files\PostgreSQL\18\data"

:: Status
"C:\Program Files\PostgreSQL\18\bin\pg_ctl" status -D "C:\Program Files\PostgreSQL\18\data"
```

### Auto-start as Windows Service (requires admin)

Register the service (run once, from an elevated Command Prompt):

```cmd
"C:\Program Files\PostgreSQL\18\bin\pg_ctl" register -N "postgresql-18" -D "C:\Program Files\PostgreSQL\18\data"
net start postgresql-18
```

To remove the service later:

```cmd
net stop postgresql-18
"C:\Program Files\PostgreSQL\18\bin\pg_ctl" unregister -N "postgresql-18"
```

## Connection Details

| Parameter | Value |
|-----------|-------|
| Host      | `localhost` |
| Port      | `5432` |
| User      | `postgres` |
| Password  | *(none — trust auth)* |

## Client Access

### pgAdmin 4

Bundled at `C:\Program Files\PostgreSQL\18\pgAdmin 4\`. Register the server manually:

- **General** → Name: `Local`
- **Connection** → Host: `localhost`, Port: `5432`, User: `postgres`, Password: blank

### psql (CLI)

Blocked by Device Guard on this machine. Use pgAdmin or Python (`psycopg2`) instead.

### Python

```python
import psycopg2
conn = psycopg2.connect(host="localhost", port=5432, user="postgres")
```

## Notes

- Database cluster initialized with `initdb -U postgres -E UTF8`
- Locale: `English_United States.1252`
- Default timezone: `Asia/Calcutta`
- Data page checksums: enabled
- `psql.exe` is blocked by Device Guard policy on this machine
