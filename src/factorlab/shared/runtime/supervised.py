"""Top-level wrapper for long-running script entrypoints.

Standardises:
  - heartbeat init (so canary jobs can detect hangs)
  - exception capture → ExitCode.CRASH (no stack-trace tossed at the user;
    the message + traceback go to the logger)
  - optional ``on_crash`` callback (Phase 4 wires ``notify(severity='fatal')``)

Use it from every script's ``__main__`` block::

    if __name__ == "__main__":
        sys.exit(supervised(main, name="india_equities_upstox_live"))
"""

from __future__ import annotations

import logging
from typing import Callable

from factorlab.shared.runtime.exit_codes import ExitCode
from factorlab.shared.runtime.heartbeat import Heartbeat


def supervised(
    main: Callable[[], int],
    *,
    name: str,
    on_crash: Callable[[BaseException], None] | None = None,
    enable_heartbeat: bool = True,
) -> int:
    """Run *main* with standardised exception capture + heartbeat init.

    - Initialises ``Heartbeat(name)`` (writes the file on construction).
      Scripts call ``hb.tick()`` themselves inside their loop.
    - Catches everything except ``SystemExit`` / ``KeyboardInterrupt``.
    - On uncaught exception: logs (with traceback), calls ``on_crash``
      if provided, and returns :data:`ExitCode.CRASH`.
    """
    log = logging.getLogger(name)

    if enable_heartbeat:
        try:
            Heartbeat(name)
        except Exception as e:  # pragma: no cover — best effort
            log.warning("heartbeat init failed for %s: %s", name, e)

    try:
        return int(main())
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as exc:
        log.exception("Unexpected error in %s: %s", name, exc)
        if on_crash:
            try:
                on_crash(exc)
            except Exception as cb_err:  # pragma: no cover
                log.error("on_crash callback failed: %s", cb_err)
        return int(ExitCode.CRASH)


__all__ = ["supervised"]
