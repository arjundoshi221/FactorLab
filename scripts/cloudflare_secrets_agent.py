"""Compatibility path for cloudflare-secrets-agent: runs ``factorlab.components.secrets_agent.agent.main``.

Compose commands, cron wrappers and runbooks call this path; the code lives in the
package, so this file only forwards to it.
"""

from factorlab.components.secrets_agent.agent import main

if __name__ == "__main__":
    raise SystemExit(main())
