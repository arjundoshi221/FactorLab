"""Compatibility path for bootstrap: runs ``factorlab.schema.storage_policy.main``.

Compose commands, cron wrappers and runbooks call this path; the code lives in the
package, so this file only forwards to it.
"""

from factorlab.schema.storage_policy import main

if __name__ == "__main__":
    raise SystemExit(main())
