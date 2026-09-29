"""Compatibility path for ingest-india: runs ``factorlab.components.ingest_india.legacy.daemon.main``.

Compose commands, cron wrappers and runbooks call this path; the code lives in the
package, so this file only forwards to it.
"""

from factorlab.components.ingest_india.legacy.daemon import main

if __name__ == "__main__":
    raise SystemExit(main())
