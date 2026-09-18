"""Launcher for the host-side notifier daemon.

Thin wrapper around :func:`factorlab.shared.notify.daemon.main`. Task
Scheduler runs this script at log-on (Phase 7) so the daemon is up before
any live poller fires.

Usage::

    python scripts/_shared/notifier_daemon.py

Env vars (see docs/operations/env-reference.md):

    FACTORLAB_NOTIFY_PIN          required (daemon fails closed without it)
    FACTORLAB_NOTIFY_DAEMON_HOST  default 127.0.0.1 (localhost-only — heimdall)
    FACTORLAB_NOTIFY_DAEMON_PORT  default 8765
    FACTORLAB_NOTIFY_TO           required for Outlook backend to do anything
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from factorlab.shared.notify.daemon import main


if __name__ == "__main__":
    sys.exit(main())
