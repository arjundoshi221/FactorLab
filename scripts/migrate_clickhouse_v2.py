"""Compatibility path for operators (reviewed migrations): runs ``factorlab.schema.migrate.main``.

Compose commands, cron wrappers and runbooks call this path; the code lives in the
package, so this file only forwards to it.
"""

from factorlab.schema.migrate import main

if __name__ == "__main__":
    raise SystemExit(main())
