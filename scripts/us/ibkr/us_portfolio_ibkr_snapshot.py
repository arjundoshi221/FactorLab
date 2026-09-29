"""Compatibility path for ibkr-snapshot: runs ``factorlab.components.ingest_broker.snapshot.cli``.

Compose commands, cron wrappers and runbooks call this path; the code lives in the
package, so this file only forwards to it.
"""

from factorlab.components.ingest_broker.snapshot import cli

if __name__ == "__main__":
    raise SystemExit(cli())
