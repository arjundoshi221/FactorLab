"""Compatibility path for schema changes: runs ``factorlab.schema.codegen.main``.

Compose commands, cron wrappers and runbooks call this path; the code lives in the
package, so this file only forwards to it.
"""

from factorlab.schema.codegen import main

if __name__ == "__main__":
    raise SystemExit(main())
