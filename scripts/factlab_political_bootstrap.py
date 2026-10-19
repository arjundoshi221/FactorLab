"""Compatibility path for ingest-political: runs the maintained engine bootstrap.

Compose commands, cron wrappers and runbooks call this path; the code lives in the
package, so this file only forwards to it.
"""

from factorlab.components.ingest_political.bootstrap import main

if __name__ == "__main__":
    raise SystemExit(main())
