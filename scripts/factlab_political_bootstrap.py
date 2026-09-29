"""Compatibility path for ingest-political: runs ``factorlab.components.ingest_political.legacy.bootstrap.main``.

Compose commands, cron wrappers and runbooks call this path; the code lives in the
package, so this file only forwards to it.
"""

from factorlab.components.ingest_political.legacy.bootstrap import main

if __name__ == "__main__":
    raise SystemExit(main())
