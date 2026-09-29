"""Compatibility path for universe-us: runs ``factorlab.components.ingest_us.legacy.universe_daemon.main``.

Compose commands, cron wrappers and runbooks call this path; the code lives in the
package, so this file only forwards to it.
"""

from factorlab.components.ingest_us.legacy.universe_daemon import main

if __name__ == "__main__":
    raise SystemExit(main())
