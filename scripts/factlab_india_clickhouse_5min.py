"""Compatibility path for ingest-india: runs the maintained engine collector.

Compose commands, cron wrappers and runbooks call this path; the code lives in the
package, so this file only forwards to it.
"""

from factorlab.components.ingest_india.collector import main

if __name__ == "__main__":
    raise SystemExit(main())
