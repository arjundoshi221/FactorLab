"""Top-level wrapper for long-running script entrypoints.

Standardises:
  - file heartbeat initialization and UDP lifecycle reporting
  - exception capture → ExitCode.CRASH (no stack-trace tossed at the user;
    the message + traceback go to the logger)
  - optional ``on_crash`` callback, alongside UDP crash reporting

Use it from every script's ``__main__`` block::

    if __name__ == "__main__":
        sys.exit(supervised(main, name="india_equities_upstox_live"))
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from factorlab.runtime.exit_codes import ExitCode
from factorlab.runtime.heartbeat import Heartbeat
from factorlab.runtime.monitor import monitor_client


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
    monitor = monitor_client(name)
    monitor.heartbeat("starting")

    if enable_heartbeat:
        try:
            Heartbeat(name)
        except Exception as e:  # noqa: BLE001  # pragma: no cover — best effort
            log.warning("heartbeat init failed for %s: %s", name, e)

    try:
        result = int(main())
        monitor.heartbeat("stopping")
        return result
    except (SystemExit, KeyboardInterrupt):
        monitor.heartbeat("stopping")
        raise
    except Exception as exc:
        log.exception("Unexpected error in %s: %s", name, exc)
        monitor.error("crash", str(exc), severity="fatal")
        if on_crash:
            try:
                on_crash(exc)
            except Exception as cb_err:  # noqa: BLE001  # pragma: no cover
                log.error("on_crash callback failed: %s", cb_err)
        return int(ExitCode.CRASH)


__all__ = ["supervised"]
