"""Compatibility path for deploy-release.sh: runs ``factorlab.schema.verify_writes.main``.

Compose commands, cron wrappers and runbooks call this path; the code lives in the
package, so this file only forwards to it.
"""

from factorlab.schema.verify_writes import main

if __name__ == "__main__":
    raise SystemExit(main())
